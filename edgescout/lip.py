"""M4: Kalshi's Liquidity Incentive Program (LIP) - the order books of a fixed random sample of the markets it pays
on, recorded so a quoting rule's reward share and fills can be simulated afterwards (docs/M4_LIP.md; edgescout/m4.py).
Public data only: no keys, nothing traded.

Kalshi pays each LIP market's pool, per period, pro rata to resting size near the best bid: an order book snapshot every
second, both sides scored separately, depth counted up to the program's target size, orders below the reference price
discounted per tick, and snapshots without the target size resting on both sides excluded. The pools are public
(`GET /incentive_programs`, period_reward in centi-cents), and so are the books (`GET /markets/{ticker}/orderbook`, YES
bids and NO bids) and, afterwards, the trade tape. What isn't public is who rests where - hence these books.

The sample is fixed by rule, not chosen: a market is in it when the SHA-256 of its ticker falls in the first
SAMPLE_PER_MILLE of 1,000 buckets. New markets enter by the same rule as programs roll over.

Rows:
- `lip_programs`: each active liquidity program on a sampled market, when first seen (or changed): its id, market,
  period, reward in dollars, target size and discount factor;
- `lip_series`: each sampled market's series once, with its fee type and multiplier (makers pay a fee on some);
- `lip_books`: every LIP_EVERY_S, each sampled market with an active program: both sides' bids from the best down,
  until the cumulative size passes twice the target (at most MAX_LEVELS levels), and the time it was read.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import datetime

import httpx

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
SAMPLE_PER_MILLE = 35                                    # ~3.5% of markets: ~150 of the ~4,300 with live programs
LIP_EVERY_S = 120
PROGRAMS_EVERY_S = 3600
PACE_S = 0.4                                             # one request at a time, beside kalshi.py's own (0.25 s)
MAX_LEVELS = 30
BACKOFF_S = 5.0                                          # after an HTTP 429


def sampled(ticker: str) -> bool:
    return int(hashlib.sha256(ticker.encode()).hexdigest()[:8], 16) % 1000 < SAMPLE_PER_MILLE


def _f(x) -> float | None:
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _ts(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def series_of(ticker: str) -> str:
    return ticker.split("-")[0]


def program_row(x: dict) -> dict | None:
    start, end = _ts(x.get("start_date")), _ts(x.get("end_date"))
    if x.get("incentive_type") != "liquidity" or start is None or end is None or end <= start:
        return None
    t = x.get("market_ticker") or ""
    return {"id": x.get("id"), "ticker": t, "series": series_of(t), "start": start, "end": end,
            "reward": (x.get("period_reward") or 0) / 1e4, "target": _f(x.get("target_size_fp")),
            "discount": (x.get("discount_factor_bps") or 0) / 1e4, "paid_out": x.get("paid_out")}


def side(levels: list | None, target: float) -> list[list[float]]:
    """Bids, best first, cut once the cumulative size passes 2 x target (or at MAX_LEVELS)."""
    lv = sorted(([_f(p), _f(s)] for p, s in (levels or [])), key=lambda r: -(r[0] or 0))
    out, cum = [], 0.0
    for p, s in lv:
        if p is None or s is None or s <= 0:
            continue
        out.append([round(p, 4), s])
        cum += s
        if cum > 2 * target or len(out) >= MAX_LEVELS:
            break
    return out


def book_row(ticker: str, ob: dict, target: float, t: float) -> dict:
    b = ob.get("orderbook_fp") or {}
    return {"ticker": ticker, "t": round(t, 3), "yes": side(b.get("yes_dollars"), target),
            "no": side(b.get("no_dollars"), target)}


async def _get(c: httpx.AsyncClient, path: str, **params) -> dict:
    await asyncio.sleep(PACE_S)
    r = await c.get(f"{KALSHI}{path}", params=params)
    if r.status_code == 429:
        await asyncio.sleep(BACKOFF_S)
    r.raise_for_status()
    return r.json()


async def active_programs(c: httpx.AsyncClient) -> list[dict]:
    out, cursor = [], None
    for _ in range(50):
        d = await _get(c, "/incentive_programs", status="active", type="liquidity", limit=1000,
                       **({"cursor": cursor} if cursor else {}))
        out += [r for r in (program_row(x) for x in d.get("incentive_programs") or []) if r]
        cursor = d.get("next_cursor")
        if not cursor:
            break
    return out


async def run(c: httpx.AsyncClient, end: float, write, every: float = LIP_EVERY_S,
              programs_every: float = PROGRAMS_EVERY_S) -> None:
    """The recorder loop (a task beside scan's): refresh the sampled programs hourly, read their books every `every`."""
    progs: dict = {}                                     # id -> row (sampled markets only)
    seen_series: set = set()
    last_programs = 0.0
    while time.time() < end:
        t0 = time.time()
        if t0 - last_programs >= programs_every:
            try:
                rows = [r for r in await active_programs(c) if sampled(r["ticker"])]
                new = [r for r in rows if progs.get(r["id"]) != r]
                progs = {r["id"]: r for r in rows}
                last_programs = t0
                serr, srows = {}, []
                for s in sorted({r["series"] for r in rows} - seen_series):
                    try:
                        x = (await _get(c, f"/series/{s}")).get("series") or {}
                        srows.append({"series": s, "fee_type": x.get("fee_type"),
                                      "fee_multiplier": _f(x.get("fee_multiplier"))})
                        seen_series.add(s)
                    except Exception as ex:              # noqa: BLE001 - recorded, retried next refresh
                        serr[f"lip_series:{s}"] = f"{type(ex).__name__}: {str(ex)[:120]}"
                write("lip_programs", new, {})
                if srows or serr:
                    write("lip_series", srows, serr)
            except Exception as ex:                      # noqa: BLE001 - recorded; the old list is kept
                write("lip_programs", [], {"lip_programs": f"{type(ex).__name__}: {str(ex)[:120]}"})
        now = time.time()
        by_ticker: dict = {}
        for r in progs.values():
            if r["start"] <= now < r["end"]:
                by_ticker[r["ticker"]] = max(by_ticker.get(r["ticker"], 0.0), r["target"] or 0.0)
        books, errs = [], {}
        for ticker, target in sorted(by_ticker.items()):
            try:
                ob = await _get(c, f"/markets/{ticker}/orderbook")
                books.append(book_row(ticker, ob, target, time.time()))
            except Exception as ex:                      # noqa: BLE001 - per market, never raised
                errs[f"lip_book:{ticker}"] = f"{type(ex).__name__}: {str(ex)[:120]}"
        write("lip_books", books, errs)
        await asyncio.sleep(max(1.0, every - (time.time() - t0)))
