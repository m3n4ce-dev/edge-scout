"""The arithmetic: what an observed price difference is worth after fees, in % per trade or % per year."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import yaml

YEAR_S = 365 * 86400
ROOT = Path(__file__).resolve().parent.parent


def load_fees(path: Path | None = None) -> dict:
    return yaml.safe_load((path or ROOT / "config" / "fees.yaml").read_text())


def annualize(rate: float, interval_s: float) -> float:
    """Funding paid per interval (fraction) -> % per year."""
    return rate * YEAR_S / interval_s * 100


def spot_fee(fees: dict, venue: str, side: str, tier: str) -> float:
    v = fees["spot"].get(venue) or {}
    return float((v.get(tier) or v.get("entry") or {}).get(side, 1.0))


def spot_spreads(rows: list[dict], fees: dict) -> list[dict]:
    """Per symbol: buy at the cheapest ask, sell at the richest bid on a DIFFERENT venue. Gross % and what is
    left after fees: both legs taking liquidity (entry and $10K tier), and one leg resting as maker (t10k).
    Ignores moving coins/cash between venues - the trade needs inventory on both sides already."""
    by = defaultdict(list)
    for r in rows:
        by[r["symbol"]].append(r)
    out = []
    for sym, rs in by.items():
        best = None
        for a in rs:
            for b in rs:
                if a["venue"] == b["venue"]:
                    continue
                g = (b["bid"] / a["ask"] - 1) * 100            # buy on a, sell on b
                if best is None or g > best[0]:
                    best = (g, a["venue"], b["venue"])
        if best is None:
            continue
        g, buy, sell = best
        out.append({
            "symbol": sym, "buy": buy, "sell": sell, "gross_pct": round(g, 4),
            "net_entry_taker": round(g - spot_fee(fees, buy, "taker", "entry") - spot_fee(fees, sell, "taker", "entry"), 4),
            "net_t10k_taker": round(g - spot_fee(fees, buy, "taker", "t10k") - spot_fee(fees, sell, "taker", "t10k"), 4),
            "net_t10k_maker": round(g - min(spot_fee(fees, buy, "maker", "t10k") + spot_fee(fees, sell, "taker", "t10k"),
                                            spot_fee(fees, buy, "taker", "t10k") + spot_fee(fees, sell, "maker", "t10k")), 4),
        })
    return out


def funding_rows(rows: list[dict], us: set[str]) -> list[dict]:
    return [{**r, "ann_pct": round(annualize(r["rate"], r["interval_s"]), 3), "us": r["venue"] in us} for r in rows]


def funding_spreads(frows: list[dict]) -> list[dict]:
    """Per symbol: short the perp with the highest funding, long the lowest - collects the difference while
    the two price legs cancel (needs margin on two venues)."""
    by = defaultdict(list)
    for r in frows:
        by[r["symbol"]].append(r)
    out = []
    for sym, rs in by.items():
        if len(rs) < 2:
            continue
        hi, lo = max(rs, key=lambda r: r["ann_pct"]), min(rs, key=lambda r: r["ann_pct"])
        out.append({"symbol": sym, "short": hi["venue"], "long": lo["venue"], "spread_ann_pct": round(hi["ann_pct"] - lo["ann_pct"], 3),
                    "us_both": hi["us"] and lo["us"]})
    return out


def carry(funding_ann_pct: float, round_trip_fee_pct: float, hold_days: float) -> float:
    """Long spot + short perp on one venue, held hold_days: funding minus the fees, both as % per year."""
    return funding_ann_pct - round_trip_fee_pct * 365 / hold_days


def basis_rows(rows: list[dict], now: float, fees: dict, tier: str) -> list[dict]:
    """Dated future vs spot: buy spot, short the future, hold to expiry. Fees: spot buy + spot sell at expiry
    (maker, the patient way) + the future's open (it cash-settles)."""
    out = []
    for r in rows:
        days = (r["expiry"] - now) / 86400
        if days < 1 or not r["spot"] or (r.get("spread_bps") or 0) > 100:     # no book, or wider than 1%
            continue
        basis = (r["price"] / r["spot"] - 1) * 100
        fut = float((fees["futures"].get(r["venue"]) or {}).get("taker", 0.05))
        spot_venue = "coinbase" if r["venue"] == "coinbase_cde" else "kraken"
        rt = 2 * spot_fee(fees, spot_venue, "maker", tier) + fut
        out.append({**r, "days": round(days, 1), "basis_pct": round(basis, 4), "ann_gross_pct": round(basis * 365 / days, 3),
                    "ann_net_pct": round((basis - rt) * 365 / days, 3), "fees_pct": round(rt, 3)})
    return out
