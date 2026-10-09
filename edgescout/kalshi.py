"""M1: Kalshi's crypto price markets beside Deribit's options, recorded together so a Kalshi price can later be compared
with the probability the options imply for the same event, and with what actually happened. Public data only: no keys,
nothing traded. Every function returns plain rows and never raises (failures go to `errors`, as in venues.py).

Kalshi (US-legal): hourly and daily BTC/ETH markets - "above $K" (KXBTCD, KXETHD) and ranges (KXBTC, KXETH). A row
keeps each market's strike(s), close time, and both sides' best bid/ask and sizes, in dollars per $1 contract. Only
markets whose mid is within MID_RANGE are kept (a 0/1-cent book is a lottery ticket no fee schedule lets anyone trade
profitably, and there are hundreds per event). Settled EVENTS are recorded once each: the settlement index value, the
rule text (which index, averaged how), and every market's (strike, result) - so a strike's semantics can be checked.

Deribit (read only, not US-tradable): option book summaries for expiries within DERIBIT_MAX_DAYS and strikes within
DERIBIT_BAND of the index - mark IV, bid/ask, underlying forward - and the index price. Deribit's daily options expire
at 08:00 UTC and Kalshi's markets close at other hours, so the analysis interpolates total variance between expiries.
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timezone

import httpx

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = ("KXBTCD", "KXBTC", "KXETHD", "KXETH")          # above/below and ranges, BTC and ETH
DERIBIT = "https://www.deribit.com/api/v2/public"
CURRENCIES = ("BTC", "ETH")
DERIBIT_MAX_DAYS = 3                                     # Kalshi's markets close within a day: two expiries bracket it
DERIBIT_BAND = 0.10                                      # strikes within +-10% of the index
MID_RANGE = (0.02, 0.98)                                 # a Kalshi market is kept while its mid is inside this
SETTLED_LOOKBACK_S = 6 * 3600


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


async def _markets(c, series: str, **params) -> list[dict]:
    """Every market of a series with these filters, following the cursor (bounded)."""
    out, cursor = [], None
    for _ in range(10):
        q = {"series_ticker": series, "limit": 1000, **params, **({"cursor": cursor} if cursor else {})}
        d = await _get(c, f"{KALSHI}/markets", params=q)
        out += d.get("markets") or []
        cursor = d.get("cursor")
        if not cursor:
            break
    return out


def market_row(m: dict, series: str) -> dict:
    """One Kalshi market, compact: dollars per $1 contract; strike fields as Kalshi states them."""
    return {"venue": "kalshi", "series": series, "ticker": m.get("ticker"), "event": m.get("event_ticker"),
            "strike_type": m.get("strike_type"), "floor": _f(m.get("floor_strike")), "cap": _f(m.get("cap_strike")),
            "close": _ts(m.get("close_time")),
            "yes_bid": _f(m.get("yes_bid_dollars")), "yes_ask": _f(m.get("yes_ask_dollars")),
            "yes_bid_size": _f(m.get("yes_bid_size_fp")), "yes_ask_size": _f(m.get("yes_ask_size_fp")),
            "no_bid": _f(m.get("no_bid_dollars")), "no_ask": _f(m.get("no_ask_dollars")),
            "last": _f(m.get("last_price_dollars")), "volume_24h": _f(m.get("volume_24h_fp")),
            "open_interest": _f(m.get("open_interest_fp"))}


def informative(r: dict) -> bool:
    """A quoted market whose mid (bid 0 when none) is within MID_RANGE."""
    a = r.get("yes_ask")
    if a is None:
        return False
    mid = ((r.get("yes_bid") or 0.0) + a) / 2
    return MID_RANGE[0] <= mid <= MID_RANGE[1]


async def kalshi_open(c: httpx.AsyncClient) -> tuple[list[dict], dict]:
    rows, errors = [], {}
    res = await asyncio.gather(*(_markets(c, s, status="open") for s in SERIES), return_exceptions=True)
    for s, r in zip(SERIES, res):
        if isinstance(r, Exception):
            errors[f"kalshi:{s}"] = f"{type(r).__name__}: {str(r)[:120]}"
            continue
        rows += [x for x in (market_row(m, s) for m in r) if informative(x)]
    return rows, errors


async def kalshi_settled(c: httpx.AsyncClient, since: float) -> tuple[list[dict], dict]:
    """Events whose markets closed since `since`: one row each - the settlement index value, the rule text, and every
    market's [strike_type, floor, cap, result]."""
    rows, errors = [], {}
    res = await asyncio.gather(*(_markets(c, s, status="settled", min_close_ts=int(since)) for s in SERIES),
                               return_exceptions=True)
    for s, r in zip(SERIES, res):
        if isinstance(r, Exception):
            errors[f"kalshi:{s}"] = f"{type(r).__name__}: {str(r)[:120]}"
            continue
        events: dict = {}
        for m in r:
            ev = events.setdefault(m.get("event_ticker"), {
                "venue": "kalshi", "series": s, "event": m.get("event_ticker"), "close": _ts(m.get("close_time")),
                "settled_value": _f(m.get("expiration_value")), "rules": (m.get("rules_primary") or "")[:600],
                "markets": []})
            ev["markets"].append([m.get("strike_type"), _f(m.get("floor_strike")), _f(m.get("cap_strike")),
                                  m.get("result")])
        rows += list(events.values())
    return rows, errors


EXPIRY = re.compile(r"^(BTC|ETH)-(\d{1,2})([A-Z]{3})(\d{2})-(\d+(?:d\d+)?)-([CP])$")
MONTHS = {m: i + 1 for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}


def parse_option(name: str) -> dict | None:
    """'BTC-9OCT26-82000-C' -> currency, expiry (08:00 UTC), strike, call/put."""
    m = EXPIRY.match(name or "")
    if not m:
        return None
    cur, day, mon, yy, strike, cp = m.groups()
    exp = datetime(2000 + int(yy), MONTHS[mon], int(day), 8, tzinfo=timezone.utc).timestamp()
    return {"currency": cur, "expiry": exp, "strike": float(strike.replace("d", ".")), "cp": cp}


async def deribit_options(c: httpx.AsyncClient, now: float | None = None) -> tuple[list[dict], dict]:
    now = time.time() if now is None else now
    rows, errors = [], {}

    async def one(cur):
        idx = (await _get(c, f"{DERIBIT}/get_index_price", params={"index_name": f"{cur.lower()}_usd"}))["result"]
        book = (await _get(c, f"{DERIBIT}/get_book_summary_by_currency",
                           params={"currency": cur, "kind": "option"}))["result"]
        spot = _f(idx.get("index_price"))
        out = [{"venue": "deribit", "kind": "index", "currency": cur, "index": spot}]
        for b in book:
            o = parse_option(b.get("instrument_name"))
            if not o or spot is None or not now < o["expiry"] <= now + DERIBIT_MAX_DAYS * 86400:
                continue
            if abs(o["strike"] / spot - 1) > DERIBIT_BAND:
                continue
            out.append({"venue": "deribit", "kind": "option", **o,
                        "mark_iv": _f(b.get("mark_iv")), "mark": _f(b.get("mark_price")),
                        "bid": _f(b.get("bid_price")), "ask": _f(b.get("ask_price")),
                        "forward": _f(b.get("underlying_price"))})
        return out
    res = await asyncio.gather(*(one(cur) for cur in CURRENCIES), return_exceptions=True)
    for cur, r in zip(CURRENCIES, res):
        if isinstance(r, Exception):
            errors[f"deribit:{cur}"] = f"{type(r).__name__}: {str(r)[:120]}"
        else:
            rows += r
    return rows, errors
