"""M3: Bitnomial's perpetual funding, recorded beside Coinbase Derivatives' (docs/M3_BITNOMIAL.md). Public data, no keys.

Bitnomial (CFTC-regulated, open to US persons) sets funding by rulebook: the average premium plus a clamp of
(interest - premium) to +-0.05%, with the interest term fixed at 0.01% per 8 h - so while the premium is small, longs
pay shorts exactly 0.01%/8h (10.95%/yr). Coinbase Derivatives' perp funding floats hourly with its premium. The gap is
what a short-Bitnomial / long-Coinbase position would collect, delta-neutral, if it persists net of costs.

Rows: one per product per funding interval (deduplicated by the caller): the rate, the interest term, the interval,
the mark and index as Bitnomial reports them. Products come from `bitnomial_perps.json` (Bitnomial's own specs).
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx

API = "https://bitnomial.com/exchange/api/v1/funding-rates/"
PERPS = json.loads((Path(__file__).with_name("bitnomial_perps.json")).read_text())
PACE_S = 0.25
LOOKBACK_S = 2 * 86400


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


def row(spec: dict, r: dict) -> dict:
    return {"venue": "bitnomial", "base": spec["base_symbol"], "symbol": spec["symbol"],
            "coin": spec["contract_size_unit"], "product_id": r.get("product_id"),
            "rate": _f(r.get("funding_rate")), "interest_rate": _f(r.get("interest_rate")),
            "interval_s": int(spec.get("funding_interval_hours") or 8) * 3600,
            "interval_start": r.get("interval_start"), "interval_end": r.get("interval_end"),
            "mark": _f(r.get("mark_price")), "index": _f(r.get("price_index"))}


async def bitnomial_funding(c: httpx.AsyncClient, now: float | None = None) -> tuple[list[dict], dict]:
    """The last LOOKBACK_S of funding intervals for every perpetual, one base at a time (paced)."""
    now = time.time() if now is None else now
    begin = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - LOOKBACK_S))
    rows, errors = [], {}
    for spec in PERPS.values():
        try:
            await asyncio.sleep(PACE_S)
            d = await _get(c, API, params={"base_symbol": spec["base_symbol"], "begin_time": begin, "limit": 50,
                                           "order": "asc"})
            rows += [row(spec, r) for r in d.get("data") or []]
        except Exception as ex:                          # noqa: BLE001 - reported per product, never raised
            errors[f"bitnomial:{spec['base_symbol']}"] = f"{type(ex).__name__}: {str(ex)[:120]}"
    return rows, errors
