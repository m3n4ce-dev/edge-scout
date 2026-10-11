"""M3's paper position (docs/M3_BITNOMIAL.md): short Bitnomial perps, long Coinbase Derivatives perps, $1,000 each in
BTC, ETH, SOL and XRP, from the recorded funding alone. Written before the forward window."""
from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

COINS = ("BTC", "ETH", "SOL", "XRP")
NOTIONAL = 1000.0
COST_BP, COST_BP_SENSITIVITY = 25.0, 40.0
YEAR = 365.25 * 86400
WINDOW_DAYS = 14
KILL_NET_PCT = 4.0


def _ts(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def load(paths: list[Path]) -> tuple[dict, dict]:
    """(CDE: coin -> [(t, hourly rate)], Bitnomial: coin -> {interval_end_ts: 8h rate}) from the scan files."""
    cde, bn = defaultdict(list), defaultdict(dict)
    for p in sorted(paths):
        with p.open() as f:
            for line in f:
                r = json.loads(line)
                if r.get("kind") == "funding":
                    for x in r["rows"]:
                        if x.get("venue") == "coinbase_cde" and x.get("symbol") in COINS and x.get("rate") is not None:
                            cde[x["symbol"]].append((r["t"], x["rate"] * 3600 / (x.get("interval_s") or 3600)))
                elif r.get("kind") == "bitnomial_funding":
                    for x in r["rows"]:
                        if x.get("coin") in COINS and x.get("rate") is not None and x.get("interval_end"):
                            bn[x["coin"]].setdefault(_ts(x["interval_end"]), x["rate"])   # any product: one formula
    return cde, bn


def paper(cde: dict, bn: dict, start: float, end: float, cost_bp: float = COST_BP) -> dict:
    out, days = {}, max((end - start) / 86400, 1e-9)
    for coin in COINS:
        got = sum(rate * NOTIONAL for t, rate in bn.get(coin, {}).items() if start < t <= end)
        series = sorted(cde.get(coin, []))
        paid, daily = 0.0, defaultdict(lambda: [0.0, 0.0])
        h = (int(start // 3600) + 1) * 3600
        i = 0
        last = None
        while h <= end:
            while i < len(series) and series[i][0] <= h:
                last = series[i][1]
                i += 1
            if last is not None:
                paid += last * NOTIONAL
                daily[int((h - 1) // 86400)][1] += last * NOTIONAL    # (an hour ending at 00:00 is the day before)
            h += 3600
        for t, rate in bn.get(coin, {}).items():
            if start < t <= end:
                daily[int((t - 1) // 86400)][0] += rate * NOTIONAL
        cost = cost_bp / 1e4 * NOTIONAL
        net = got - paid - cost
        good = sum(1 for g, p in daily.values() if g - p > 0)
        out[coin] = {"bitnomial_received": round(got, 4), "cde_paid": round(paid, 4), "cost": round(cost, 4),
                     "net": round(net, 4), "net_pct_per_year": round(100 * net / NOTIONAL * YEAR / (days * 86400), 3),
                     "days_positive": f"{good}/{len(daily)}",
                     "cde_above_bitnomial_days": sum(1 for g, p in daily.values() if p > g)}
    total = sum(v["net"] for v in out.values())
    out["portfolio_net_pct_per_year"] = round(100 * total / (NOTIONAL * len(COINS)) * YEAR / (days * 86400), 3)
    return out


def held(cde: dict, bn: dict, start: float, end: float) -> dict:
    """Reported, not decided on (docs/M3_BITNOMIAL.md, note of 2026-10-11): the window's gross funding gap per year,
    the same with each round trip's cost spread over a one-year hold instead of the 14-day window, and the variant
    hedged with spot instead of the CDE perp (Bitnomial's funding received; no funding paid on the long leg, which
    ties up the full notional in cash)."""
    g, years = paper(cde, bn, start, end, cost_bp=0.0), max((end - start) / YEAR, 1e-12)
    out = {}
    for coin in COINS:
        x = g[coin]
        gap = 100 * (x["bitnomial_received"] - x["cde_paid"]) / NOTIONAL / years
        out[coin] = {"gross_gap_pct_per_year": round(gap, 3),
                     "held_one_year_net_pct": {f"{bp:g}bp": round(gap - bp / 100, 3) for bp in (COST_BP, COST_BP_SENSITIVITY)},
                     "spot_hedge_received_pct_per_year": round(100 * x["bitnomial_received"] / NOTIONAL / years, 3)}
    n = len(COINS)
    out["portfolio_gross_gap_pct_per_year"] = round(sum(out[c]["gross_gap_pct_per_year"] for c in COINS) / n, 3)
    out["portfolio_held_one_year_net_pct_at_25bp"] = round(out["portfolio_gross_gap_pct_per_year"] - COST_BP / 100, 3)
    return out


def run(data: Path, now: float | None = None) -> dict:
    cde, bn = load(sorted(data.glob("scan-*.jsonl")))
    first_bn = None
    for p in sorted(data.glob("scan-*.jsonl")):
        with p.open() as f:
            for line in f:
                if '"bitnomial_funding"' in line:
                    first_bn = json.loads(line)["t"]
                    break
        if first_bn:
            break
    if first_bn is None:
        raise ValueError("no Bitnomial funding recorded yet")
    now = time.time() if now is None else now
    end = min(now, first_bn + WINDOW_DAYS * 86400)
    off = sum(1 for c in COINS for t, r in bn.get(c, {}).items() if first_bn < t <= end and abs(r - 1e-4) > 1e-12)
    n = sum(1 for c in COINS for t in bn.get(c, {}) if first_bn < t <= end)
    return {"window_from": time.strftime("%Y-%m-%d %H:%M", time.gmtime(first_bn)),
            "window_to": time.strftime("%Y-%m-%d %H:%M", time.gmtime(end)), "complete": end >= first_bn + WINDOW_DAYS * 86400,
            "bitnomial_off_floor": f"{off}/{n}", "at_25bp": paper(cde, bn, first_bn, end),
            "at_40bp": paper(cde, bn, first_bn, end, COST_BP_SENSITIVITY),
            "reported_not_decided": held(cde, bn, first_bn, end)}
