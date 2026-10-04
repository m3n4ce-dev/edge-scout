"""Public market data, no API keys. Every function returns plain rows and never raises: a venue that
fails or is unreachable (Binance and Bybit refuse US addresses) is skipped and reported in `errors`.

US_TRADABLE marks venues a US resident may actually trade on. The others are read for price discovery
only - their terms bar US persons, so an edge that needs them is research, not a trade.
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timezone

import httpx

SYMBOLS = ("BTC", "ETH", "SOL", "XRP", "LINK", "ARB", "AVAX")
US_TRADABLE = {"coinbase", "kraken", "gemini", "bitstamp", "binanceus", "coinbase_cde", "kalshi"}
TIMEOUT = httpx.Timeout(8.0)


def _f(x) -> float | None:
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


async def _get(c: httpx.AsyncClient, url: str, **kw):
    r = await c.get(url, **kw)
    r.raise_for_status()
    return r.json()


# ------------------------------------------------------------------ spot (US venues)
KRAKEN_KEYS = {"XXBTZUSD": "BTC", "XETHZUSD": "ETH", "XXRPZUSD": "XRP"}
KRAKEN_PAIRS = {"BTC": "XBTUSD"}


async def _coinbase(c, symbols):
    async def one(s):
        d = await _get(c, f"https://api.exchange.coinbase.com/products/{s}-USD/ticker")
        return {"venue": "coinbase", "symbol": s, "bid": _f(d.get("bid")), "ask": _f(d.get("ask"))}
    return await asyncio.gather(*(one(s) for s in symbols), return_exceptions=True)


async def _kraken(c, symbols):
    pairs = ",".join(KRAKEN_PAIRS.get(s, f"{s}USD") for s in symbols)
    d = await _get(c, "https://api.kraken.com/0/public/Ticker", params={"pair": pairs})
    out = []
    for key, t in (d.get("result") or {}).items():
        sym = KRAKEN_KEYS.get(key) or (key[:-3] if key.endswith("USD") else None)
        if sym in symbols:
            out.append({"venue": "kraken", "symbol": sym, "bid": _f(t["b"][0]), "ask": _f(t["a"][0])})
    return out


async def _gemini(c, symbols):
    async def one(s):
        d = await _get(c, f"https://api.gemini.com/v1/pubticker/{s.lower()}usd")
        return {"venue": "gemini", "symbol": s, "bid": _f(d.get("bid")), "ask": _f(d.get("ask"))}
    return await asyncio.gather(*(one(s) for s in symbols), return_exceptions=True)


async def _bitstamp(c, symbols):
    async def one(s):
        d = await _get(c, f"https://www.bitstamp.net/api/v2/ticker/{s.lower()}usd/")
        return {"venue": "bitstamp", "symbol": s, "bid": _f(d.get("bid")), "ask": _f(d.get("ask"))}
    return await asyncio.gather(*(one(s) for s in symbols), return_exceptions=True)


async def _binanceus(c, symbols):
    d = await _get(c, "https://api.binance.us/api/v3/ticker/bookTicker")
    by = {x["symbol"]: x for x in d}
    return [{"venue": "binanceus", "symbol": s, "bid": _f(by[f"{s}USD"]["bidPrice"]), "ask": _f(by[f"{s}USD"]["askPrice"])}
            for s in symbols if f"{s}USD" in by]


SPOT_VENUES = {"coinbase": _coinbase, "kraken": _kraken, "gemini": _gemini, "bitstamp": _bitstamp,
               "binanceus": _binanceus}


async def spot_quotes(c: httpx.AsyncClient, symbols=SYMBOLS) -> tuple[list[dict], dict]:
    """Best bid/ask per (venue, symbol) on US spot venues. Rows with a missing or crossed book are dropped."""
    names = list(SPOT_VENUES)
    res = await asyncio.gather(*(SPOT_VENUES[n](c, symbols) for n in names), return_exceptions=True)
    rows, errors = [], {}
    for n, r in zip(names, res):
        if isinstance(r, Exception):
            errors[n] = f"{type(r).__name__}: {str(r)[:120]}"
            continue
        for x in r:
            if isinstance(x, Exception):
                errors.setdefault(n, f"{type(x).__name__}: {str(x)[:120]}")
            elif x["bid"] and x["ask"] and 0 < x["bid"] <= x["ask"]:
                rows.append(x)
    return rows, errors


# ------------------------------------------------------------------ perpetual funding
def _sym(s: str) -> str:
    return {"XBT": "BTC"}.get(s, s)


async def _hyperliquid(c, symbols):
    r = await c.post("https://api.hyperliquid.xyz/info", json={"type": "metaAndAssetCtxs"})
    r.raise_for_status()
    meta, ctxs = r.json()
    out = []
    for a, x in zip(meta["universe"], ctxs):
        if a["name"] in symbols:
            out.append({"venue": "hyperliquid", "symbol": a["name"], "rate": _f(x.get("funding")), "interval_s": 3600,
                        "mark": _f(x.get("markPx"))})
    return out


async def _okx(c, symbols):
    async def one(s):
        d = (await _get(c, "https://www.okx.com/api/v5/public/funding-rate", params={"instId": f"{s}-USDT-SWAP"}))["data"][0]
        iv = (int(d["nextFundingTime"]) - int(d["fundingTime"])) / 1000 if d.get("nextFundingTime") else 28800
        return {"venue": "okx", "symbol": s, "rate": _f(d.get("fundingRate")), "interval_s": iv or 28800, "mark": None}
    return await asyncio.gather(*(one(s) for s in symbols), return_exceptions=True)


async def _dydx(c, symbols):
    d = await _get(c, "https://indexer.dydx.trade/v4/perpetualMarkets")
    out = []
    for s in symbols:
        m = (d.get("markets") or {}).get(f"{s}-USD")
        if m:
            out.append({"venue": "dydx", "symbol": s, "rate": _f(m.get("nextFundingRate")), "interval_s": 3600,
                        "mark": _f(m.get("oraclePrice"))})
    return out


async def _deribit(c, symbols):
    async def one(s):
        d = (await _get(c, "https://www.deribit.com/api/v2/public/ticker",
                         params={"instrument_name": f"{s}-PERPETUAL"}))["result"]
        return {"venue": "deribit", "symbol": s, "rate": _f(d.get("funding_8h")), "interval_s": 28800,
                "mark": _f(d.get("mark_price"))}
    return await asyncio.gather(*(one(s) for s in symbols if s in ("BTC", "ETH")), return_exceptions=True)


async def _kraken_futures(c, symbols):
    d = await _get(c, "https://futures.kraken.com/derivatives/api/v3/tickers")
    out = []
    for t in d.get("tickers") or []:
        m = re.fullmatch(r"PF_([A-Z]+)USD", t.get("symbol", ""))
        if m and _sym(m.group(1)) in symbols and _f(t.get("markPrice")):
            # Kraken quotes an absolute hourly rate (USD per unit of the underlying): relative = rate / mark.
            # Checked 2026-10-04 against /v4/historicalfundingrates: hourly, relativeFundingRate = fundingRate / price
            out.append({"venue": "kraken_futures", "symbol": _sym(m.group(1)),
                        "rate": (_f(t.get("fundingRate")) or 0.0) / _f(t["markPrice"]), "interval_s": 3600,
                        "mark": _f(t["markPrice"])})
    return out


async def _coinbase_products(c, kind: str) -> list[dict]:
    d = await _get(c, "https://api.coinbase.com/api/v3/brokerage/market/products",
                   params={"product_type": "FUTURE", "contract_expiry_type": kind, "limit": 500})
    return d.get("products") or []


async def _coinbase_cde_perps(c, symbols):
    out = []
    for p in await _coinbase_products(c, "EXPIRING"):
        f = p.get("future_product_details") or {}
        if f.get("venue") == "cde" and p.get("display_name", "").endswith(" PERP") and f.get("contract_root_unit") in symbols:
            iv = int(str(f.get("funding_interval") or "3600s").rstrip("s") or 3600)
            out.append({"venue": "coinbase_cde", "symbol": f["contract_root_unit"], "rate": _f(f.get("funding_rate")),
                        "interval_s": iv, "mark": _f(p.get("price")), "instrument": p["product_id"]})
    return out


FUNDING_VENUES = {"coinbase_cde": _coinbase_cde_perps, "hyperliquid": _hyperliquid, "okx": _okx, "dydx": _dydx,
                  "deribit": _deribit, "kraken_futures": _kraken_futures}


async def perp_funding(c: httpx.AsyncClient, symbols=SYMBOLS) -> tuple[list[dict], dict]:
    names = list(FUNDING_VENUES)
    res = await asyncio.gather(*(FUNDING_VENUES[n](c, symbols) for n in names), return_exceptions=True)
    rows, errors = [], {}
    for n, r in zip(names, res):
        if isinstance(r, Exception):
            errors[n] = f"{type(r).__name__}: {str(r)[:120]}"
            continue
        for x in r:
            if isinstance(x, Exception):
                errors.setdefault(n, f"{type(x).__name__}: {str(x)[:120]}")
            elif x.get("rate") is not None:
                rows.append(x)
    return rows, errors


# ------------------------------------------------------------------ dated futures (basis)
MONTHS = {m: i + 1 for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}


def deribit_expiry(name: str) -> float | None:
    """BTC-27NOV26 -> 2026-11-27 08:00 UTC (Deribit's expiry time)."""
    m = re.fullmatch(r"[A-Z]+-(\d{1,2})([A-Z]{3})(\d{2})", name)
    if not m:
        return None
    return datetime(2000 + int(m.group(3)), MONTHS[m.group(2)], int(m.group(1)), 8, tzinfo=timezone.utc).timestamp()


async def _deribit_futures(c, symbols):
    out = []
    for cur in ("BTC", "ETH"):
        if cur not in symbols:
            continue
        d = await _get(c, "https://www.deribit.com/api/v2/public/get_book_summary_by_currency",
                       params={"currency": cur, "kind": "future"})
        for r in d["result"]:
            exp = deribit_expiry(r["instrument_name"])
            if exp and _f(r.get("mark_price")) and _f(r.get("estimated_delivery_price")):
                out.append({"venue": "deribit", "symbol": cur, "instrument": r["instrument_name"], "expiry": exp,
                            "price": _f(r["mark_price"]), "spot": _f(r["estimated_delivery_price"])})
    return out


async def _book(c, product_id: str) -> tuple[float | None, float | None]:
    d = await _get(c, "https://api.coinbase.com/api/v3/brokerage/market/product_book",
                   params={"product_id": product_id, "limit": 1})
    b, a = (d.get("pricebook") or {}).get("bids") or [], (d.get("pricebook") or {}).get("asks") or []
    return (_f(b[0]["price"]) if b else None), (_f(a[0]["price"]) if a else None)


async def _cde_futures(c, symbols, spot_mid: dict[str, float]):
    """Coinbase Derivatives (US) dated futures, priced at the live book's mid - the last trade on a thin
    contract can be hours old. Rows keep bid/ask so the report can drop wide books."""
    want = []
    for p in await _coinbase_products(c, "EXPIRING"):
        f = p.get("future_product_details") or {}
        sym = f.get("contract_root_unit")
        if f.get("venue") != "cde" or p.get("display_name", "").endswith(" PERP") or sym not in symbols:
            continue
        if spot_mid.get(sym):
            want.append((p["product_id"], sym, datetime.fromisoformat(f["contract_expiry"].replace("Z", "+00:00")).timestamp()))
    books = await asyncio.gather(*(_book(c, pid) for pid, _, _ in want), return_exceptions=True)
    out = []
    for (pid, sym, exp), bk in zip(want, books):
        if isinstance(bk, Exception) or not bk[0] or not bk[1]:
            continue
        out.append({"venue": "coinbase_cde", "symbol": sym, "instrument": pid, "expiry": exp, "price": (bk[0] + bk[1]) / 2,
                    "bid": bk[0], "ask": bk[1], "spread_bps": round((bk[1] / bk[0] - 1) * 1e4, 1), "spot": spot_mid[sym]})
    return out


async def dated_futures(c: httpx.AsyncClient, spot_mid: dict[str, float], symbols=SYMBOLS) -> tuple[list[dict], dict]:
    res = await asyncio.gather(_deribit_futures(c, symbols), _cde_futures(c, symbols, spot_mid), return_exceptions=True)
    rows, errors = [], {}
    for n, r in zip(("deribit", "coinbase_cde"), res):
        if isinstance(r, Exception):
            errors[n] = f"{type(r).__name__}: {str(r)[:120]}"
        else:
            rows += r
    return rows, errors


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": "edge-scout/0.1 (research)"})


def now() -> float:
    return time.time()
