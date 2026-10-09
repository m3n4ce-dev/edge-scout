"""M1's analysis, written before any data (docs/M1_KALSHI.md is the registration; this is its code).

    python -m edgescout m1                 the development window (the first 7 UTC days after recording starts)
    python -m edgescout m1 --holdout       the next 14 days - refused until they're complete

Pieces, each tested on synthetic data (tests/test_m1.py):
- `implied_above`: the options-implied probability that the price is above K at a time T, from Deribit's recorded
  smiles - IV against log-moneyness per expiry, total variance and the forward interpolated in time between the two
  expiries bracketing T, and the digital as -dC/dK by a central difference on Black-76 prices (r = 0);
- `kalshi_fee`: the taker fee per contract, ceil(0.07 p (1 - p) * 100) / 100;
- `samples`: each "above K" market at the registered lead times before its close (the last quote at or before),
  joined with its event's outcome and the implied probability from the last options snapshot at or before;
- `evaluate`: calibration by price bucket, and the registered rule's value per contract, event-clustered.
"""
from __future__ import annotations

import bisect
import json
import math
import random
import time
from collections import defaultdict
from pathlib import Path

YEAR = 365.25 * 86400
LEADS = (6 * 3600, 3 * 3600, 3600, 45 * 60, 30 * 60, 15 * 60, 5 * 60)   # each where a quote exists (hourly events
                                                                      # have none hours before; daily ones have all)
BUCKETS = ((0.02, 0.10), (0.10, 0.30), (0.30, 0.70), (0.70, 0.90), (0.90, 0.98))
THETA = 0.03
MIN_SIZE = 10
MAX_OPTIONS_AGE_S = 300                 # an options snapshot older than this at the sample time: no implied probability
BOOT, SEED = 2000, 20261009
DEV_DAYS, HOLDOUT_DAYS = 7, 14
ABOVE = ("KXBTCD", "KXETHD")
CURRENCY = {"KXBTCD": "BTC", "KXETHD": "ETH", "KXBTC": "BTC", "KXETH": "ETH"}


def ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def black76_call(f: float, k: float, w: float) -> float:
    """Call value with forward f, strike k and total variance w = sigma^2 T (r = 0)."""
    if w <= 0:
        return max(f - k, 0.0)
    s = math.sqrt(w)
    d1 = (math.log(f / k) + w / 2) / s
    return f * ncdf(d1) - k * ncdf(d1 - s)


def kalshi_fee(p: float) -> float:
    """Kalshi's taker fee per contract at price p (dollars), rounded up to the cent."""
    return math.ceil(round(0.07 * p * (1 - p) * 100, 9)) / 100


class Smile:
    """One expiry's smile: IV (a fraction) against log-moneyness ln(K/F), linear between points, flat beyond."""

    def __init__(self, expiry: float, forward: float, points: list[tuple[float, float]]):
        self.expiry, self.forward = expiry, forward
        pts = sorted(points)
        self.k = [p[0] for p in pts]
        self.iv = [p[1] for p in pts]

    def vol(self, strike: float) -> float:
        x = math.log(strike / self.forward)
        k, v = self.k, self.iv
        if x <= k[0]:
            return v[0]
        if x >= k[-1]:
            return v[-1]
        i = bisect.bisect_right(k, x)
        t = (x - k[i - 1]) / (k[i] - k[i - 1])
        return v[i - 1] + t * (v[i] - v[i - 1])


def smiles(options: list[dict]) -> list[Smile]:
    """Deribit option rows of one snapshot -> one Smile per expiry (OTM marks; a strike's call and put averaged)."""
    by: dict = defaultdict(lambda: defaultdict(list))
    fwd: dict = defaultdict(list)
    for o in options:
        if o.get("kind") != "option" or not o.get("mark_iv") or not o.get("forward"):
            continue
        by[o["expiry"]][o["strike"]].append(o["mark_iv"] / 100)
        fwd[o["expiry"]].append(o["forward"])
    out = []
    for e, strikes in by.items():
        f = sum(fwd[e]) / len(fwd[e])
        pts = [(math.log(k / f), sum(v) / len(v)) for k, v in strikes.items()]
        if len(pts) >= 2:
            out.append(Smile(e, f, pts))
    return sorted(out, key=lambda s: s.expiry)


def _w_f(sm: list[Smile], strike: float, t: float, target: float) -> tuple[float, float]:
    """Total variance at `strike` and the forward, for the time `target`, seen at time t."""
    tau = (target - t) / YEAR
    before = [s for s in sm if s.expiry <= target]
    after = [s for s in sm if s.expiry > target]
    if before and after:
        a, b = before[-1], after[0]
        ta, tb = (a.expiry - t) / YEAR, (b.expiry - t) / YEAR
        wa, wb = a.vol(strike) ** 2 * ta, b.vol(strike) ** 2 * tb
        x = (target - a.expiry) / (b.expiry - a.expiry)
        return wa + x * (wb - wa), a.forward + x * (b.forward - a.forward)
    s = after[0] if after else before[-1]                 # one side only: the nearest smile, scaled to the time
    return s.vol(strike) ** 2 * tau, s.forward


def implied_above(sm: list[Smile], strike: float, t: float, target: float) -> float | None:
    """P(S_target > strike) implied by the smiles seen at t: -dC/dK by a central difference (+-0.5% of K)."""
    if not sm or target <= t:
        return None
    h = 0.005 * strike
    w1, f1 = _w_f(sm, strike - h, t, target)
    w2, f2 = _w_f(sm, strike + h, t, target)
    f = (f1 + f2) / 2
    p = -(black76_call(f, strike + h, w2) - black76_call(f, strike - h, w1)) / (2 * h)
    return min(1.0, max(0.0, p))


# ---------------------------------------------------------------- the recorded data
def load(paths: list[Path]) -> dict:
    kal, opts, settled = defaultdict(list), defaultdict(list), {}
    for p in sorted(paths):
        with p.open() as f:
            for line in f:
                r = json.loads(line)
                k = r.get("kind")
                if k == "kalshi":
                    for x in r["rows"]:
                        kal[x["ticker"]].append((r["t"], x))
                elif k == "deribit_options":
                    by = defaultdict(list)
                    for x in r["rows"]:
                        by[x.get("currency")].append(x)
                    for cur, rows in by.items():
                        opts[cur].append((r["t"], rows))
                elif k == "kalshi_settled":
                    for e in r["rows"]:
                        settled[e["event"]] = e
    return {"kalshi": kal, "options": opts, "settled": settled}


def _outcomes(settled: dict) -> dict:
    """(event, strike_type, floor, cap) -> 1 / 0."""
    out = {}
    for ev, e in settled.items():
        for st, fl, cp, res in e.get("markets") or []:
            if res in ("yes", "no"):
                out[(ev, st, fl, cp)] = 1 if res == "yes" else 0
    return out


def samples(d: dict, start: float, end: float) -> list[dict]:
    """Each 'above K' market at each registered lead before its close, for closes in [start, end)."""
    outcome = _outcomes(d["settled"])
    snap_t = {cur: [t for t, _ in v] for cur, v in d["options"].items()}
    cache: dict = {}
    out = []
    for ticker, quotes in d["kalshi"].items():
        last = quotes[-1][1]
        if last.get("series") not in ABOVE or last.get("strike_type") != "greater":
            continue
        close = last.get("close")
        if close is None or not start <= close < end:
            continue
        y = outcome.get((last["event"], "greater", last.get("floor"), last.get("cap")))
        if y is None:
            continue
        times = [t for t, _ in quotes]
        for lead in LEADS:
            at = close - lead
            i = bisect.bisect_right(times, at) - 1
            if i < 0 or at - times[i] > 120:                     # no quote within 2 minutes before the lead
                continue
            q = quotes[i][1]
            cur = CURRENCY[last["series"]]
            j = bisect.bisect_right(snap_t.get(cur, []), at) - 1
            p_imp = None
            if j >= 0 and at - snap_t[cur][j] <= MAX_OPTIONS_AGE_S:
                key = (cur, j)
                if key not in cache:
                    cache[key] = smiles(d["options"][cur][j][1])
                p_imp = implied_above(cache[key], q["floor"], at, close)
            out.append({"event": last["event"], "ticker": ticker, "lead_s": lead, "t": at, "close": close,
                        "strike": q["floor"], "yes_bid": q.get("yes_bid") or 0.0, "yes_ask": q.get("yes_ask"),
                        "yes_bid_size": q.get("yes_bid_size") or 0.0, "yes_ask_size": q.get("yes_ask_size") or 0.0,
                        "outcome": y, "p_imp": p_imp})
    return out


def trade(s: dict, theta: float = THETA, min_size: float = MIN_SIZE) -> dict | None:
    """The registered rule on one sample: YES at the ask or NO at the no-ask (1 - yes_bid) when the implied
    probability clears price + fee by theta, with at least min_size contracts shown. Its value per contract."""
    p = s.get("p_imp")
    if p is None:
        return None
    ya, yb = s.get("yes_ask"), s.get("yes_bid") or 0.0
    if ya is not None and 0 < ya < 1 and s["yes_ask_size"] >= min_size and p - ya - kalshi_fee(ya) >= theta:
        return {"side": "yes", "price": ya, "value": s["outcome"] - ya - kalshi_fee(ya)}
    na = 1 - yb
    if yb > 0 and s["yes_bid_size"] >= min_size and (1 - p) - na - kalshi_fee(na) >= theta:
        return {"side": "no", "price": na, "value": (1 - s["outcome"]) - na - kalshi_fee(na)}
    return None


def _boot(per_event: dict[str, list[float]], boots: int = BOOT, seed: int = SEED) -> dict:
    """Mean per event, then across events; a 90% event-clustered bootstrap interval."""
    ev = [sum(v) / len(v) for v in per_event.values() if v]
    if not ev:
        return {"events": 0, "mean": None, "lo90": None, "hi90": None}
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(ev) for _ in ev) / len(ev) for _ in range(boots))
    return {"events": len(ev), "mean": round(sum(ev) / len(ev), 5), "lo90": round(means[int(0.05 * boots)], 5),
            "hi90": round(means[int(0.95 * boots) - 1], 5)}


def evaluate(ss: list[dict]) -> dict:
    cal = {}
    for lo, hi in BUCKETS:
        g: dict = defaultdict(list)
        for s in ss:
            if s["yes_ask"] is None:
                continue
            mid = (s["yes_bid"] + s["yes_ask"]) / 2
            if lo <= mid < hi:
                g[s["event"]].append(s["outcome"] - mid)
        cal[f"{lo:.2f}-{hi:.2f}"] = {"samples": sum(len(v) for v in g.values()), **_boot(g)}
    g: dict = defaultdict(list)
    sides: dict = defaultdict(int)
    for s in ss:
        tr = trade(s)
        if tr:
            g[s["event"]].append(tr["value"])
            sides[tr["side"]] += 1
    have = sum(1 for s in ss if s["p_imp"] is not None)
    gap: dict = defaultdict(list)
    for s in ss:
        if s["p_imp"] is not None and s["yes_ask"] is not None:
            gap[s["event"]].append(s["p_imp"] - (s["yes_bid"] + s["yes_ask"]) / 2)
    return {"samples": len(ss), "with_implied": have, "calibration_outcome_minus_mid": cal,
            "implied_minus_mid": _boot(gap), "rule": {"trades": sum(sides.values()), "sides": dict(sides),
                                                      "value_per_contract": _boot(g)}}


def window(d: dict, holdout: bool, now: float | None = None) -> tuple[float, float]:
    """The registered windows: development = from the first recorded Kalshi quote to the end of the 7th full UTC day
    after it; holdout = the 14 days after that."""
    first = min((q[0][0] for q in d["kalshi"].values() if q), default=None)
    if first is None:
        raise ValueError("no Kalshi data recorded yet")
    day0 = (first // 86400 + 1) * 86400
    dev_end = day0 + DEV_DAYS * 86400
    if not holdout:
        return first, dev_end
    end = dev_end + HOLDOUT_DAYS * 86400
    if (time.time() if now is None else now) < end + 86400:      # settlements arrive after the close
        raise ValueError(f"the holdout isn't complete until {time.strftime('%Y-%m-%d', time.gmtime(end + 86400))}")
    return dev_end, end


def run(data: Path, holdout: bool = False) -> dict:
    d = load(sorted(data.glob("scan-*.jsonl")))
    start, end = window(d, holdout)
    ss = samples(d, start, end)
    return {"window": "holdout" if holdout else "development",
            "from": time.strftime("%Y-%m-%d %H:%M", time.gmtime(start)),
            "to": time.strftime("%Y-%m-%d %H:%M", time.gmtime(end)), **evaluate(ss)}
