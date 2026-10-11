"""M4's simulation, written before any of its data (docs/M4_LIP.md is the registration; this is its code).

    python -m edgescout.m4 pull [--holdout]      the sampled markets' trade tape and results (public, paced, cached)
    python -m edgescout.m4 report [--holdout]    the registered quoting rule's reward, fills and return on capital

The rule, on each sampled market while a liquidity program runs on it, at each recorded book (lip.py):
- rest Q = target / 5 contracts on each side (a YES bid and a NO bid);
- one tick better than the best bid on both sides when the spread is at least 3 ticks (first in the queue), else at the
  best bid behind the size already there;
- not on a side whose inventory has reached the target size (net YES - NO, or NO - YES).

Reward: per book, each side's credit as Kalshi scores it - walking down from the best bid with our order inserted, the
reference price is the first level where the cumulative size reaches target / 5; orders count until the cumulative
size reaches the target; below the reference price the discount factor applies per tick. Our share of a side is our
credit over all credit; a book counts only if both sides (ours included) hold the target size. The program pays its
period reward pro rata to time, times the time-weighted mean of (share_yes + share_no) / 2 over its books, with our
resting size shrunk by fills until the next book.

Fills: between two books, from the public trade tape - a taker selling YES (taker_side "no") at a YES price at or below
our YES bid fills us first when we're first in the queue; at the best bid we're filled after the size ahead of us
trades at that price, or by any print through our price. NO likewise (taker_side "yes", the NO price). Each fill is
valued at the market's result if it settled, else at the last recorded mid; maker fees are charged where the series
has them.

Capital: the cash a resting order or an open position ties up (a resting bid's price, a position's cost; a YES and a
NO in the same market net out at $1). Return on capital = net / the time integral of capital, per year.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import random
import time
from collections import defaultdict
from pathlib import Path

import httpx

TICK = 0.01
MAX_GAP_S = 300                          # a book's quote lasts until the next book, at most this long
QUOTE_FRACTION = 0.2                     # Q = target / 5
IMPROVE_MIN_SPREAD = 3                   # ticks
MAKER_FEE_RATE = 0.0175                  # Kalshi's maker fee, on series whose fee type includes maker fees
UNKNOWN_FEE_RATE = 0.07                  # any other fee type: charged as Kalshi's taker rate (conservative)
YEAR = 365.25 * 86400
BOOT, SEED = 2000, 20261011
DEV_DAYS, HOLDOUT_DAYS = 7, 14
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
PACE_S = 0.4
EPS = 1e-9


def fee_rate(fee_type: str | None, multiplier: float | None) -> float:
    m = 1.0 if multiplier is None else multiplier
    if fee_type == "quadratic":
        return 0.0
    if fee_type and "maker" in fee_type:
        return MAKER_FEE_RATE * m
    return UNKNOWN_FEE_RATE * m


def maker_fee(rate: float, p: float, n: float) -> float:
    """Dollars for one fill of n contracts at p, rounded up to the cent."""
    if rate <= 0 or n <= 0:
        return 0.0
    return math.ceil(round(rate * n * p * (1 - p) * 100, 9)) / 100


def credit(levels: list, price: float | None, q: float, target: float, discount: float,
           first: bool) -> tuple[float, float, float]:
    """One side of one book, scored as Kalshi does, with our order of q at `price` inserted (first in its level, or
    last behind the size already there). Returns (our credit, all credit, all resting size)."""
    orders = []                                          # (price, size, ours), best first
    lv = sorted(((p, s) for p, s in levels if s > 0), key=lambda r: -r[0])
    placed = price is None or q <= 0
    for p, s in lv:
        if not placed and (price > p + EPS or (first and abs(price - p) < EPS)):
            orders.append((price, q, True))
            placed = True
        orders.append((p, s, False))
        if not placed and abs(price - p) < EPS:
            orders.append((price, q, True))
            placed = True
    if not placed:
        orders.append((price, q, True))
    total = sum(s for _, s, _ in orders)
    if total <= 0 or target <= 0:
        return 0.0, 0.0, total
    ref, cum = orders[-1][0], 0.0
    for p, s, _ in orders:
        cum += s
        if cum >= target / 5 - EPS:
            ref = p
            break
    ours = everyone = cum = 0.0
    for p, s, mine in orders:
        if cum >= target - EPS:
            break
        take = min(s, target - cum)
        cum += take
        ticks = max(0, round((ref - p) / TICK)) if p < ref - EPS else 0
        c = take * (discount ** ticks if ticks else 1.0)
        everyone += c
        if mine:
            ours += c
    return ours, everyone, total


def best(levels: list) -> float | None:
    return max((p for p, s in levels if s > 0), default=None)


class Position:
    """YES and NO contracts in one market, netted as Kalshi nets them (a pair is worth $1)."""

    def __init__(self):
        self.y = self.cy = self.n = self.cn = 0.0        # contracts and their total cost
        self.realized = 0.0

    def add(self, side: str, n: float, price: float) -> None:
        if side == "yes":
            self.y += n
            self.cy += n * price
        else:
            self.n += n
            self.cn += n * price
        m = min(self.y, self.n)
        if m > EPS:
            ay, an = self.cy / self.y, self.cn / self.n
            self.realized += m * (1 - ay - an)
            self.cy -= m * ay
            self.cn -= m * an
            self.y -= m
            self.n -= m

    @property
    def net(self) -> float:
        return self.y - self.n

    @property
    def cost(self) -> float:
        return self.cy + self.cn

    def value(self, yes_value: float) -> float:
        """Realized pair profits plus the open side at a YES value in [0, 1], minus what it cost."""
        return self.realized + self.y * yes_value + self.n * (1 - yes_value) - self.cost


def simulate(books: list[dict], programs: list[dict], trades: list, result: str | None, rate: float,
             start: float, end: float) -> dict:
    """One market over [start, end): books [{t, yes, no}], its programs [{start, end, reward, target, discount}],
    trades [[t, taker_side, yes_price, no_price, count]] sorted by time, its result ("yes"/"no"/None)."""
    books = sorted((b for b in books if start <= b["t"] < end), key=lambda b: b["t"])
    trades = sorted(tr for tr in trades if start <= tr[0] < end)
    pos = Position()
    reward = fees = capital_s = quoted_s = eligible_s = share_s = improve_s = 0.0
    fills = 0.0
    ti, last_mid = 0, None
    for i, b in enumerate(books):
        t = b["t"]
        nxt = books[i + 1]["t"] if i + 1 < len(books) else end
        dur = max(0.0, min(nxt, end, t + MAX_GAP_S) - t)
        while ti < len(trades) and trades[ti][0] < t:
            ti += 1
        live = [p for p in programs if p["start"] <= t < p["end"]]
        by, bn = best(b["yes"]), best(b["no"])
        if by is not None and bn is not None:
            last_mid = (by + (1 - bn)) / 2
        if not live or dur <= 0 or by is None or bn is None:
            capital_s += pos.cost * dur
            continue
        target = max(p["target"] or 0 for p in live)
        discount = min(p["discount"] for p in live)
        q = math.floor(target * QUOTE_FRACTION)
        spread = round((1 - by - bn) / TICK)
        first = spread >= IMPROVE_MIN_SPREAD
        py, pn = (by + TICK, bn + TICK) if first else (by, bn)
        quote_y, quote_n = pos.net < target - EPS, -pos.net < target - EPS
        ahead_y = 0.0 if first else sum(s for p, s in b["yes"] if abs(p - by) < EPS)
        ahead_n = 0.0 if first else sum(s for p, s in b["no"] if abs(p - bn) < EPS)
        rem = {"yes": q if quote_y else 0.0, "no": q if quote_n else 0.0}
        rest_int = {"yes": 0.0, "no": 0.0}               # integral of resting size over the interval
        t_prev = t
        j = ti
        while j < len(trades) and trades[j][0] < t + dur:
            tt, taker, yp, np_, n = trades[j]
            for s in ("yes", "no"):
                rest_int[s] += rem[s] * (tt - t_prev)
            t_prev = tt
            side, px, our = ("yes", yp, py) if taker == "no" else ("no", np_, pn)
            if rem[side] > EPS and px is not None:
                if first:
                    hit = n if px <= our + EPS else 0.0
                elif px < our - EPS:
                    hit = n
                elif abs(px - our) < EPS:
                    ahead = ahead_y if side == "yes" else ahead_n
                    x = min(ahead, n)
                    if side == "yes":
                        ahead_y -= x
                    else:
                        ahead_n -= x
                    hit = n - x
                else:
                    hit = 0.0
                f = min(rem[side], hit)
                if f > EPS:
                    rem[side] -= f
                    fills += f
                    pos.add(side, f, our)
                    fees += maker_fee(rate, our, f)
            j += 1
        for s in ("yes", "no"):
            rest_int[s] += rem[s] * (t + dur - t_prev)
        wy = rest_int["yes"] / (q * dur) if q and quote_y else 0.0
        wn = rest_int["no"] / (q * dur) if q and quote_n else 0.0
        oy, ay, ty = credit(b["yes"], py if quote_y else None, q, target, discount, first)
        on, an, tn = credit(b["no"], pn if quote_n else None, q, target, discount, first)
        eligible = ty >= target - EPS and tn >= target - EPS
        sy = oy / ay if ay > 0 else 0.0
        sn = on / an if an > 0 else 0.0
        share = ((sy * wy + sn * wn) / 2) if eligible else 0.0
        rate_per_s = sum(p["reward"] / (p["end"] - p["start"]) for p in live)
        reward += rate_per_s * dur * share
        quoted_s += dur
        eligible_s += dur if eligible else 0.0
        share_s += share * dur
        improve_s += dur if first else 0.0
        capital_s += (pos.cost + (rest_int["yes"] * py + rest_int["no"] * pn) / dur) * dur
    if result in ("yes", "no"):
        yes_value, marked = (1.0 if result == "yes" else 0.0), False
    else:
        yes_value, marked = (last_mid if last_mid is not None else 0.5), True
    fill_pnl = pos.value(yes_value)
    return {"reward": reward, "fill_pnl": fill_pnl, "fees": fees, "net": reward + fill_pnl - fees,
            "capital_s": capital_s, "fills": fills, "quoted_s": quoted_s, "eligible_s": eligible_s,
            "share_s": share_s, "improve_s": improve_s, "marked": marked, "open_net": pos.net}


# ---------------------------------------------------------------- the recorded data and the windows
DATA = Path(__file__).resolve().parent.parent / "data"
PULL = DATA / "m4"


def load(paths: list[Path]) -> dict:
    books: dict = defaultdict(list)
    programs: dict = {}
    series: dict = {}
    for path in paths:
        with path.open() as f:
            for line in f:
                if '"lip_' not in line[:60]:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                k = r.get("kind")
                if k == "lip_books":
                    for b in r["rows"]:
                        books[b["ticker"]].append(b)
                elif k == "lip_programs":
                    for p in r["rows"]:
                        programs[p["id"]] = p
                elif k == "lip_series":
                    for s in r["rows"]:
                        series[s["series"]] = s
    return {"books": books, "programs": programs, "series": series}


def window(d: dict, holdout: bool, now: float | None = None) -> tuple[float, float]:
    """Development: from the first recorded book to the end of the 7th full UTC day after it; holdout: the 14 days
    after that, refused until they're over."""
    first = min((b[0]["t"] for b in d["books"].values() if b), default=None)
    if first is None:
        raise ValueError("no LIP books recorded yet")
    dev_end = (first // 86400 + 1 + DEV_DAYS) * 86400
    if not holdout:
        return first, dev_end
    end = dev_end + HOLDOUT_DAYS * 86400
    if (time.time() if now is None else now) < end:
        raise ValueError(f"the holdout isn't over until {time.strftime('%Y-%m-%d', time.gmtime(end))}")
    return dev_end, end


def _get(c: httpx.Client, path: str, **params) -> dict:
    for attempt in range(6):
        time.sleep(PACE_S)
        r = c.get(f"{KALSHI}{path}", params=params)
        if r.status_code == 429:
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"rate-limited on {path}")


def pull(holdout: bool = False) -> None:
    d = load(sorted(DATA.glob("scan-*.jsonl")))
    start, end = window(d, holdout)
    if time.time() < end:
        raise SystemExit(f"the window runs to {time.strftime('%Y-%m-%d %H:%M', time.gmtime(end))} UTC: pull after it")
    PULL.mkdir(parents=True, exist_ok=True)
    tag = "holdout" if holdout else "development"
    with httpx.Client(timeout=20) as c:
        for ticker in sorted(d["books"]):
            p = PULL / f"{tag}-{ticker}.json.gz"
            if p.exists():
                continue
            try:
                trades, cursor = [], None
                for _ in range(1000):
                    x = _get(c, "/markets/trades", ticker=ticker, limit=1000, min_ts=int(start), max_ts=int(end),
                             **({"cursor": cursor} if cursor else {}))
                    trades += x.get("trades") or []
                    cursor = x.get("cursor")
                    if not cursor:
                        break
                m = _get(c, f"/markets/{ticker}").get("market") or {}
            except Exception as ex:                      # noqa: BLE001 - logged; the next run retries it
                print(f"{ticker}: {type(ex).__name__}: {str(ex)[:120]}", flush=True)
                continue
            rows = [[_t(x["created_time"]), x["taker_side"], float(x["yes_price_dollars"]), float(x["no_price_dollars"]),
                     float(x["count_fp"])] for x in trades]
            out = {"ticker": ticker, "pulled_at": time.time(), "result": m.get("result") or None,
                   "status": m.get("status"), "trades": rows}
            p.write_bytes(gzip.compress(json.dumps(out).encode()))
            print(f"{ticker}: {len(rows)} trades, result {out['result']}", flush=True)


def _t(iso: str) -> float:
    from datetime import datetime
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _ratio_boot(per: list[tuple[float, float]]) -> dict:
    """Return on capital (net / capital-years) over markets, with a market-clustered 90% bootstrap interval."""
    per = [(n, c) for n, c in per if c > 0]
    if not per:
        return {"markets": 0, "roc_pct_per_year": None, "lo90": None, "hi90": None}
    roc = lambda xs: 100 * sum(n for n, _ in xs) / (sum(c for _, c in xs) / YEAR)
    rng = random.Random(SEED)
    bs = sorted(roc([rng.choice(per) for _ in per]) for _ in range(BOOT))
    return {"markets": len(per), "roc_pct_per_year": round(roc(per), 2), "lo90": round(bs[int(0.05 * BOOT)], 2),
            "hi90": round(bs[int(0.95 * BOOT) - 1], 2)}


def evaluate(d: dict, pulled: dict, start: float, end: float) -> dict:
    progs_by: dict = defaultdict(list)
    for p in d["programs"].values():
        progs_by[p["ticker"]].append(p)
    per, by_series, rows = [], defaultdict(list), {}
    for ticker, books in d["books"].items():
        x = pulled.get(ticker)
        if x is None or not progs_by.get(ticker):
            continue
        s = d["series"].get(ticker.split("-")[0]) or {}
        r = simulate(books, progs_by[ticker], x["trades"], x.get("result"),
                     fee_rate(s.get("fee_type"), s.get("fee_multiplier")), start, end)
        if r["quoted_s"] <= 0:
            continue
        rows[ticker] = r
        per.append((r["net"], r["capital_s"]))
        by_series[ticker.split("-")[0]].append((r["net"], r["capital_s"]))
    tot = lambda k: round(sum(r[k] for r in rows.values()), 2)
    q = sum(r["quoted_s"] for r in rows.values())
    # ex-ante: a market's pool per dollar of its quote at its first book, in terciles
    ex = {}
    for ticker in rows:
        b0 = min((b for b in d["books"][ticker] if start <= b["t"] < end), key=lambda b: b["t"], default=None)
        live = [p for p in progs_by[ticker] if b0 and p["start"] <= b0["t"] < p["end"]]
        by, bn = (best(b0["yes"]), best(b0["no"])) if b0 else (None, None)
        if live and by is not None and bn is not None:
            tgt = max(p["target"] or 0 for p in live)
            day = sum(p["reward"] / (p["end"] - p["start"]) for p in live) * 86400
            ex[ticker] = day / max(0.01, tgt * QUOTE_FRACTION * (by + bn))
    order = sorted(ex, key=ex.get)
    terciles = {f"tercile_{k + 1}": _ratio_boot([(rows[t]["net"], rows[t]["capital_s"])
                                                  for t in order[k * len(order) // 3:(k + 1) * len(order) // 3]])
                for k in range(3)} if len(order) >= 3 else {}
    series = {s: {**_ratio_boot(v), "net": round(sum(n for n, _ in v), 2)} for s, v in by_series.items()}
    positive = [s for s, v in series.items() if v["markets"] >= 3]
    return {"markets": len(rows), "reward": tot("reward"), "fill_pnl": tot("fill_pnl"), "fees": tot("fees"),
            "net": tot("net"), "capital_dollar_years": round(sum(r["capital_s"] for r in rows.values()) / YEAR, 4),
            "fills": round(sum(r["fills"] for r in rows.values())),
            "eligible_fraction": round(sum(r["eligible_s"] for r in rows.values()) / q, 4) if q else None,
            "mean_share": round(sum(r["share_s"] for r in rows.values()) / q, 4) if q else None,
            "first_in_queue_fraction": round(sum(r["improve_s"] for r in rows.values()) / q, 4) if q else None,
            "marked_markets": sum(1 for r in rows.values() if r["marked"]),
            "return_on_capital": _ratio_boot(per), "by_ex_ante_pool_per_dollar": terciles,
            "series_with_3_markets_net_positive": f"{sum(1 for s in positive if series[s]['net'] > 0)}/{len(positive)}",
            "by_series": series}


def verdict(e: dict) -> str:
    r = e["return_on_capital"]
    if r["roc_pct_per_year"] is None:
        return "inconclusive: nothing simulated"
    if r["roc_pct_per_year"] < 20 or r["lo90"] <= 0:
        return "kill"
    a, b = (int(x) for x in e["series_with_3_markets_net_positive"].split("/"))
    if e["markets"] >= 60 and b and 2 * a >= b:
        return "proceed: a live pilot proposal for the owner"
    return "inconclusive"


def report(holdout: bool = False) -> dict:
    d = load(sorted(DATA.glob("scan-*.jsonl")))
    start, end = window(d, holdout)
    tag = "holdout" if holdout else "development"
    pulled = {}
    for p in PULL.glob(f"{tag}-*.json.gz"):
        x = json.loads(gzip.decompress(p.read_bytes()))
        pulled[x["ticker"]] = x
    e = evaluate(d, pulled, start, end)
    return {"study": "M4 Kalshi LIP quoting", "window": tag,
            "from": time.strftime("%Y-%m-%d %H:%M", time.gmtime(start)),
            "to": time.strftime("%Y-%m-%d %H:%M", time.gmtime(end)),
            "markets_recorded": len(d["books"]), "markets_pulled": len(pulled),
            **e, "verdict": verdict(e) if holdout else "development: no verdict"}


def main() -> None:
    ap = argparse.ArgumentParser(prog="edgescout.m4")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("pull", "report"):
        x = sub.add_parser(name)
        x.add_argument("--holdout", action="store_true")
        if name == "report":
            x.add_argument("--json")
    a = ap.parse_args()
    if a.cmd == "pull":
        pull(a.holdout)
    else:
        out = report(a.holdout)
        print(json.dumps(out, indent=1))
        if a.json:
            Path(a.json).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
