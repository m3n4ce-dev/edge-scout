"""M1b: Polymarket's BTC/ETH price markets beside Kalshi's (public data, no keys, nothing traded).

Polymarket (international) lists daily "Bitcoin above ___ on <date>?" and "Bitcoin price on <date>?" (ranges) that
resolve on Binance's BTC/USDT 1-minute candle closing at 12:00 ET - the same moment Kalshi's hourly "above" event for
12:00 ET closes (on CF Benchmarks' 60-second average). So the two venues price nearly the same event. Its markets are
`restricted` (international Polymarket bars US persons): for a US resident Polymarket is a PRICE SIGNAL here, not a
venue, unless the owner confirms access through Polymarket's US exchange.

A row: the event, its kind (above / range), close, strike (or range), the YES side's best bid/ask, spread, liquidity
and 24-hour volume, and the fee schedule. Markets whose mid is outside MID_RANGE are dropped (as for Kalshi). Settled
events are recorded once each with every market's result.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime

import httpx

GAMMA = "https://gamma-api.polymarket.com"
TITLES = {"Bitcoin above": ("BTC", "above"), "Ethereum above": ("ETH", "above"),
          "Bitcoin price on": ("BTC", "range"), "Ethereum price on": ("ETH", "range")}
MID_RANGE = (0.02, 0.98)
HORIZON_S = 36 * 3600
PACE_S = 0.25
PAGES = 5


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


async def _get(c: httpx.AsyncClient, url: str, **kw):
    r = await c.get(url, **kw)
    r.raise_for_status()
    return r.json()


def kind_of(title: str) -> tuple[str, str] | None:
    for prefix, k in TITLES.items():
        if (title or "").startswith(prefix):
            return k
    return None


NUM = r"(\d[\d,]*(?:\.\d+)?)"


def strike_of(group_title: str) -> tuple[float | None, float | None]:
    """'82,000' -> (82000, None); '118,000-120,000' -> (118000, 120000); '<76,000' / '>96,000' -> open ends."""
    t = (group_title or "").replace("$", "").replace(" ", "")
    m = re.fullmatch(NUM + "-" + NUM, t)
    if m:
        return float(m.group(1).replace(",", "")), float(m.group(2).replace(",", ""))
    m = re.fullmatch(r"[<↓]" + NUM, t)
    if m:
        return None, float(m.group(1).replace(",", ""))
    m = re.fullmatch(r"[>↑]" + NUM, t)
    if m:
        return float(m.group(1).replace(",", "")), None
    m = re.fullmatch(NUM, t)
    if m:
        return float(m.group(1).replace(",", "")), None
    return None, None


def market_row(e: dict, m: dict, cur: str, kind: str) -> dict:
    lo, hi = strike_of(m.get("groupItemTitle"))
    fs = m.get("feeSchedule") or {}
    return {"venue": "polymarket", "currency": cur, "kind": kind, "event": e.get("slug"), "market": m.get("slug"),
            "close": _ts(m.get("endDate") or e.get("endDate")), "low": lo, "high": hi,
            "yes_bid": _f(m.get("bestBid")), "yes_ask": _f(m.get("bestAsk")), "spread": _f(m.get("spread")),
            "liquidity": _f(m.get("liquidityNum")), "volume_24h": _f(m.get("volume24hr")),
            "fee_rate": _f(fs.get("rate")) if m.get("feesEnabled") else 0.0, "fee_exponent": _f(fs.get("exponent")),
            "taker_only": fs.get("takerOnly"), "restricted": m.get("restricted")}


def informative(r: dict) -> bool:
    a = r.get("yes_ask")
    if a is None:
        return False
    mid = ((r.get("yes_bid") or 0.0) + a) / 2
    return MID_RANGE[0] <= mid <= MID_RANGE[1]


async def _events(c, **params) -> list[dict]:
    out = []
    for page in range(PAGES):
        await asyncio.sleep(PACE_S)
        d = await _get(c, f"{GAMMA}/events", params={"tag_slug": "crypto", "limit": 100, "offset": 100 * page,
                                                    **params})
        out += d or []
        if not d or len(d) < 100:
            break
    return out


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


async def polymarket_open(c: httpx.AsyncClient, now: float | None = None) -> tuple[list[dict], dict]:
    now = time.time() if now is None else now
    try:
        evs = await _events(c, closed="false", end_date_min=_iso(now), end_date_max=_iso(now + HORIZON_S))
    except Exception as ex:                              # noqa: BLE001 - reported, never raised
        return [], {"polymarket": f"{type(ex).__name__}: {str(ex)[:120]}"}
    rows = []
    for e in evs:
        k = kind_of(e.get("title"))
        if k:
            rows += [r for r in (market_row(e, m, *k) for m in e.get("markets") or []) if informative(r)]
    return rows, {}


def _result(m: dict) -> str | None:
    try:
        p = [float(x) for x in json.loads(m.get("outcomePrices") or "[]")]
    except (TypeError, ValueError):
        return None
    if len(p) == 2 and sorted(p) == [0.0, 1.0]:
        return "yes" if p[0] == 1.0 else "no"
    return None


async def polymarket_settled(c: httpx.AsyncClient, since: float, now: float | None = None) -> tuple[list[dict], dict]:
    """Events that closed since `since`, once each: every market's [low, high, result]."""
    now = time.time() if now is None else now
    try:
        evs = await _events(c, closed="true", end_date_min=_iso(since), end_date_max=_iso(now))
    except Exception as ex:                              # noqa: BLE001
        return [], {"polymarket": f"{type(ex).__name__}: {str(ex)[:120]}"}
    rows = []
    for e in evs:
        k = kind_of(e.get("title"))
        if not k:
            continue
        ms = [[*strike_of(m.get("groupItemTitle")), _result(m)] for m in e.get("markets") or []]
        if ms and all(x[2] for x in ms):
            rows.append({"venue": "polymarket", "currency": k[0], "kind": k[1], "event": e.get("slug"),
                         "close": _ts(e.get("endDate")), "markets": ms,
                         "rules": ((e.get("markets") or [{}])[0].get("description") or "")[:600]})
    return rows, {}
