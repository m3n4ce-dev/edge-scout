"""Summarize recorded scans: how often each kind of edge beats its costs, by how much, for how long."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

from . import analysis, venues
from .scan import DATA


def read(files: list[Path] | None = None) -> list[dict]:
    out = []
    for p in files or sorted(DATA.glob("scan-*.jsonl")):
        for line in p.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def _streak_s(snaps: list[tuple[float, bool]]) -> float:
    best = cur = 0.0
    start = None
    for t, ok in snaps:
        if ok:
            start = t if start is None else start
            cur = t - start
            best = max(best, cur)
        else:
            start, cur = None, 0.0
    return best


def summarize(recs: list[dict], fees: dict | None = None) -> dict:
    fees = fees or analysis.load_fees()
    spot = [r for r in recs if r["kind"] == "spot"]
    fund = [r for r in recs if r["kind"] == "funding"]
    futs = [r for r in recs if r["kind"] == "futures"]
    out: dict = {"snapshots": len(spot), "hours": round((spot[-1]["t"] - spot[0]["t"]) / 3600, 2) if len(spot) > 1 else 0}

    # spot: per symbol, across snapshots
    per = defaultdict(list)
    for s in spot:
        for o in analysis.spot_spreads(s["rows"], fees):
            per[o["symbol"]].append((s["t"], o))
    rows = []
    for sym, xs in sorted(per.items()):
        os = [o for _, o in xs]
        pos = [o["gross_pct"] for o in os if o["gross_pct"] > 0]
        top = max(os, key=lambda o: o["gross_pct"])
        rows.append({"symbol": sym, "n": len(os), "gross_pos_share": round(len(pos) / len(os), 3),
                     "gross_median_pos": round(median(pos), 4) if pos else None, "gross_max": top["gross_pct"],
                     "max_route": f"{top['buy']}->{top['sell']}",
                     "net_pos_share_entry_taker": round(sum(o["net_entry_taker"] > 0 for o in os) / len(os), 4),
                     "net_pos_share_t10k_taker": round(sum(o["net_t10k_taker"] > 0 for o in os) / len(os), 4),
                     "net_pos_share_t10k_maker": round(sum(o["net_t10k_maker"] > 0 for o in os) / len(os), 4),
                     "longest_t10k_maker_s": round(_streak_s([(t, o["net_t10k_maker"] > 0) for t, o in xs]), 1)})
    out["spot"] = rows

    # funding: mean annualized per venue x symbol, and the cross-venue spread
    acc = defaultdict(list)
    spreads = defaultdict(list)
    for f in fund:
        fr = analysis.funding_rows(f["rows"], venues.US_TRADABLE)
        for r in fr:
            acc[(r["symbol"], r["venue"], r["us"])].append(r["ann_pct"])
        for s in analysis.funding_spreads(fr):
            spreads[s["symbol"]].append(s)
    out["funding"] = [{"symbol": s, "venue": v, "us": us, "mean_ann_pct": round(mean(x), 3), "min": round(min(x), 3),
                       "max": round(max(x), 3)} for (s, v, us), x in sorted(acc.items())]
    out["funding_spread"] = [{"symbol": s, "mean_ann_pct": round(mean(x["spread_ann_pct"] for x in xs), 3),
                              "max_ann_pct": max(x["spread_ann_pct"] for x in xs),
                              "usual_route": max(set((x["short"], x["long"]) for x in xs),
                                                 key=lambda k: sum((x["short"], x["long"]) == k for x in xs))}
                             for s, xs in sorted(spreads.items())]
    tier = fees.get("tier", "entry")
    us_carry = []
    for (s, v, us), x in sorted(acc.items()):
        if v == "coinbase_cde":
            rt = 2 * analysis.spot_fee(fees, "coinbase", "maker", tier) + 2 * fees["futures"]["coinbase_cde"]["taker"]
            us_carry.append({"symbol": s, "funding_ann_pct": round(mean(x), 3),
                             "net_ann_pct_30d_hold": round(analysis.carry(mean(x), rt, 30), 3),
                             "net_ann_pct_90d_hold": round(analysis.carry(mean(x), rt, 90), 3)})
    out["us_carry"] = us_carry

    # basis: latest snapshot of dated futures
    if futs:
        b = analysis.basis_rows(futs[-1]["rows"], futs[-1]["t"], fees, tier)
        out["basis"] = sorted(b, key=lambda r: (r["symbol"], r["venue"], r["days"]))
    out["cash_yield_pct"] = fees.get("cash_yield_pct")
    out["tier"] = tier
    errs = defaultdict(int)
    for r in recs:
        for k in r.get("errors") or {}:
            errs[k] += 1
    out["unreachable"] = dict(errs)
    return out


def print_summary(s: dict) -> None:
    print(f"\n=== {s['snapshots']} spot snapshots over {s['hours']} h | your fee tier: {s['tier']} ===")
    print("\nSPOT across US venues (buy cheapest ask, sell richest bid elsewhere; inventory already on both sides)")
    print(f"{'sym':<5} {'gross>0':>8} {'med gross':>9} {'max gross':>9} {'route of max':<22} {'net>0 entry':>11} {'net>0 $10K':>10} {'+maker':>7} {'longest':>8}")
    for r in s["spot"]:
        print(f"{r['symbol']:<5} {r['gross_pos_share']:>8.1%} {str(r['gross_median_pos']):>9} {r['gross_max']:>9.3f} {r['max_route']:<22} "
              f"{r['net_pos_share_entry_taker']:>11.1%} {r['net_pos_share_t10k_taker']:>10.1%} {r['net_pos_share_t10k_maker']:>7.1%} {r['longest_t10k_maker_s']:>7.0f}s")
    print("\nFUNDING, % per year (positive = longs pay shorts). US = a US resident may trade it.")
    by = defaultdict(dict)
    for r in s["funding"]:
        by[r["symbol"]][r["venue"] + ("*" if r["us"] else "")] = r["mean_ann_pct"]
    vs = sorted({v for d in by.values() for v in d})
    print(f"{'sym':<5} " + " ".join(f"{v:>15}" for v in vs))
    for sym, d in sorted(by.items()):
        print(f"{sym:<5} " + " ".join(f"{d.get(v, float('nan')):>15.2f}" for v in vs))
    print("(* = US-tradable)")
    for r in s["funding_spread"]:
        print(f"  {r['symbol']:<5} cross-venue spread mean {r['mean_ann_pct']:>6.2f}%/yr, max {r['max_ann_pct']:>6.2f}% (usually short {r['usual_route'][0]}, long {r['usual_route'][1]})")
    if s["us_carry"]:
        print("\nUS CARRY: Coinbase spot long + Coinbase perp short, after fees (%/yr)")
        for r in s["us_carry"]:
            print(f"  {r['symbol']:<5} funding {r['funding_ann_pct']:>6.2f} | net if held 30 d {r['net_ann_pct_30d_hold']:>7.2f} | 90 d {r['net_ann_pct_90d_hold']:>7.2f}")
    if s.get("basis"):
        print(f"\nBASIS: buy spot, short the dated future, hold to expiry (%/yr; idle cash earns ~{s['cash_yield_pct']}%)")
        for r in s["basis"]:
            book = f" book {r['spread_bps']}bp" if r.get("spread_bps") is not None else ""
            print(f"  {r['venue']:<13} {r['instrument']:<18} {r['days']:>6.1f} d  basis {r['basis_pct']:>6.3f}%  gross {r['ann_gross_pct']:>6.2f}%/yr  net {r['ann_net_pct']:>7.2f}%/yr (fees {r['fees_pct']}%){book}")
    if s["unreachable"]:
        print("\nunreachable / failing:", ", ".join(f"{k} x{v}" for k, v in s["unreachable"].items()))
