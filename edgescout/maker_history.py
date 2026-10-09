"""M2-pre (docs/M2_MAKER_HISTORY.md): what Kalshi's makers earned on the BTC/ETH hourly markets closing at 12:00 ET,
from the public trade tape. Read-only, no keys; requests paced; resumable (one cached file per event).

    python -m edgescout.maker_history pull            fetch the registered events' markets and trades (slow, paced)
    python -m edgescout.maker_history report [--json out.json]
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import random
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = ("KXBTCD", "KXETHD")
FIRST, LAST = date(2026, 8, 11), date(2026, 10, 8)
HOUR_ET = 12
SPLIT = date(2026, 9, 10)                                # the second half starts here
PRICE_BUCKETS = ((0.01, 0.10), (0.10, 0.30), (0.30, 0.50), (0.50, 0.70), (0.70, 0.90), (0.90, 1.00))
TIME_BUCKETS = ((0, 300), (300, 900), (900, 3600), (3600, 10 ** 9))
FAVOURITE = 0.70
BOOT, SEED = 2000, 20261009
PACE_S = 0.25
DATA = Path(__file__).resolve().parent.parent / "data" / "maker_history"
MONTHS = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()


def event_ticker(series: str, d: date, hour: int = HOUR_ET) -> str:
    return f"{series}-{d.year % 100:02d}{MONTHS[d.month - 1]}{d.day:02d}{hour:02d}"


def events() -> list[tuple[str, date]]:
    out, d = [], FIRST
    while d <= LAST:
        out += [(event_ticker(s, d), d) for s in SERIES]
        d += timedelta(days=1)
    return out


def _ts(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


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


def pull_event(c: httpx.Client, ev: str) -> dict:
    ms = _get(c, "/markets", event_ticker=ev, limit=1000).get("markets") or []
    out = {"event": ev, "pulled_at": time.time(), "markets": []}
    for m in ms:
        if float(m.get("volume_fp") or 0) <= 0:
            continue
        trades, cursor = [], None
        for _ in range(500):
            d = _get(c, "/markets/trades", ticker=m["ticker"], limit=1000, **({"cursor": cursor} if cursor else {}))
            trades += d.get("trades") or []
            cursor = d.get("cursor")
            if not cursor:
                break
        out["markets"].append({"ticker": m["ticker"], "result": m.get("result"), "status": m.get("status"),
                               "close": m.get("close_time"), "floor": m.get("floor_strike"),
                               "trades": [[t["created_time"], t["taker_side"], float(t["yes_price_dollars"]),
                                           float(t["no_price_dollars"]), float(t["count_fp"])] for t in trades]})
    return out


def pull() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=20) as c:
        for ev, _ in events():
            p = DATA / f"{ev}.json.gz"
            if p.exists():
                continue
            try:
                e = pull_event(c, ev)
            except Exception as ex:                      # noqa: BLE001 - logged; the next run retries it
                print(f"{ev}: {type(ex).__name__}: {str(ex)[:120]}", flush=True)
                continue
            p.write_bytes(gzip.compress(json.dumps(e).encode()))
            print(f"{ev}: {len(e['markets'])} markets, {sum(len(m['trades']) for m in e['markets'])} trades", flush=True)


# ---------------------------------------------------------------- M2-pre-b: the LIP series (registered 2026-10-09)
LIP_SERIES = {"KXRT": "entertainment", "KXNFLLADDERREC": "nfl_props", "KXNFLLADDERRECYDS": "nfl_props",
              "KXNFLFFPTSLADDER": "nfl_props", "KXAAAGASDOH": "gas", "KXAAAGASDCO": "gas", "KXAAAGASDIN": "gas",
              "KXAAAGASDMI": "gas", "KXAAAGASDNC": "gas", "KXAAAGASDSC": "gas", "KXAAAGASDTN": "gas",
              "KXTRUMPAPPROVE": "politics", "KXRAIN": "weather", "KXYTVIEWSW": "youtube"}
TAPE_START = "2026-08-10T00:00:00Z"
LIP_DATA = DATA.parent / "maker_history_lip"


def lip_events(c: httpx.Client) -> list[tuple[str, str]]:
    out = []
    for s in LIP_SERIES:
        cursor = None
        for _ in range(20):
            d = _get(c, "/events", series_ticker=s, status="settled", limit=200, **({"cursor": cursor} if cursor else {}))
            out += [(e["event_ticker"], s) for e in d.get("events") or []]
            cursor = d.get("cursor")
            if not cursor:
                break
    return out


def pull_lip() -> None:
    LIP_DATA.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=20) as c:
        evs = lip_events(c)
        print(f"{len(evs)} settled events", flush=True)
        for ev, s in evs:
            p = LIP_DATA / f"{ev}.json.gz"
            if p.exists():
                continue
            try:
                e = pull_event(c, ev)
            except Exception as ex:                      # noqa: BLE001 - logged; the next run retries it
                print(f"{ev}: {type(ex).__name__}: {str(ex)[:120]}", flush=True)
                continue
            e["series"] = s
            for m in e["markets"]:                       # only the public tape's period counts
                m["trades"] = [t for t in m["trades"] if t[0] >= TAPE_START]
            p.write_bytes(gzip.compress(json.dumps(e).encode()))
            print(f"{ev}: {len(e['markets'])} markets, {sum(len(m['trades']) for m in e['markets'])} trades", flush=True)


def report_lip() -> dict:
    by: dict = {"all": {}, "first_half": {}, "second_half": {}}
    cat: dict = {}
    for p in sorted(LIP_DATA.glob("*.json.gz")):
        e = json.loads(gzip.decompress(p.read_bytes()))
        rows = maker_rows(e)
        if not rows:
            continue
        last = max((m["close"] for m in e["markets"] if m.get("close")), default=None)
        if last is None:
            continue
        ev = e["event"]
        by["all"][ev] = rows
        by["first_half" if last < SPLIT.isoformat() else "second_half"][ev] = rows
        cat.setdefault(LIP_SERIES.get(e.get("series"), "?"), {})[ev] = rows
    out = {"events_with_trades": len(by["all"])}
    for part, rbe in by.items():
        out[part] = {"all_makers": contrast(rbe, lambda r: True),
                     "favourite_maker": contrast(rbe, lambda r: r["price"] >= FAVOURITE),
                     "maker_by_price": {f"{lo:.2f}-{hi:.2f}": contrast(rbe, lambda r, lo=lo, hi=hi: lo <= r["price"] < hi)
                                        for lo, hi in PRICE_BUCKETS},
                     "all_takers_after_fee": contrast(rbe, lambda r: True, key="taker_value")}
    out["by_category"] = {k: {"all_makers": contrast(v, lambda r: True),
                              "favourite_maker": contrast(v, lambda r: r["price"] >= FAVOURITE)} for k, v in cat.items()}
    hi = out["all"]["all_makers"]["hi90"]
    out["verdict"] = "proceed to the forward LIP simulation" if hi is not None and hi > -0.5 else "stop"
    return out


# ---------------------------------------------------------------- M2-fwd (registered 2026-10-09 23:55 UTC)
FWD_FROM, FWD_TO = "2026-10-10T00:00:00Z", "2026-11-07T00:00:00Z"
FWD_DATA = DATA.parent / "maker_history_fwd"
FWD_MIN_EVENTS = 40


def pull_fwd() -> None:
    """The LIP series' events settled in the forward window, trades from its start only."""
    FWD_DATA.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=20) as c:
        for ev, s in lip_events(c):
            p = FWD_DATA / f"{ev}.json.gz"
            if p.exists():
                continue
            try:
                e = pull_event(c, ev)
            except Exception as ex:                      # noqa: BLE001 - logged; the next run retries it
                print(f"{ev}: {type(ex).__name__}: {str(ex)[:120]}", flush=True)
                continue
            last = max((m["close"] for m in e["markets"] if m.get("close")), default="")
            if not FWD_FROM <= last < FWD_TO:
                continue                                 # outside the window: not kept
            e["series"] = s
            for m in e["markets"]:
                m["trades"] = [t for t in m["trades"] if t[0] >= FWD_FROM]
            p.write_bytes(gzip.compress(json.dumps(e).encode()))
            print(f"{ev}: {len(e['markets'])} markets, {sum(len(m['trades']) for m in e['markets'])} trades", flush=True)


def _boot95(per_event: dict) -> dict:
    ev = [sum(v * n for v, n in xs) / sum(n for _, n in xs) for xs in per_event.values() if xs]
    if not ev:
        return {"events": 0, "mean_cents": None, "lo95": None, "hi95": None}
    rng = random.Random(SEED)
    ms = sorted(sum(rng.choice(ev) for _ in ev) / len(ev) for _ in range(BOOT))
    return {"events": len(ev), "mean_cents": round(100 * sum(ev) / len(ev), 3),
            "lo95": round(100 * ms[int(0.025 * BOOT)], 3), "hi95": round(100 * ms[int(0.975 * BOOT) - 1], 3)}


def report_fwd(now: float | None = None) -> dict:
    if (time.time() if now is None else now) < _ts(FWD_TO) + 86400:
        raise ValueError("the forward window isn't complete until a day after 2026-11-07")
    h1, h2 = {}, {}
    for p in sorted(FWD_DATA.glob("*.json.gz")):
        e = json.loads(gzip.decompress(p.read_bytes()))
        rows = maker_rows(e)
        h1[e["event"]] = [(r["value"], r["n"]) for r in rows if 0.70 <= r["price"] < 0.90]
        if e.get("series") == "KXRAIN":
            h2[e["event"]] = [(r["value"], r["n"]) for r in rows]
    out = {"H1_favourites_70_90": _boot95(h1), "H2_rain_all_makers": _boot95(h2)}
    for k, v in out.items():
        v["verdict"] = ("inconclusive (too few events)" if v["events"] < FWD_MIN_EVENTS else
                        "pass" if (v["lo95"] or 0) > 0 else "fail")
    return out


def taker_fee(p: float, n: float) -> float:
    """Per contract, Kalshi's quadratic taker fee rounded up per trade of n contracts."""
    return math.ceil(round(0.07 * n * p * (1 - p) * 100, 9)) / 100 / n if n > 0 else 0.0


def maker_rows(e: dict) -> list[dict]:
    """Every trade from the maker's side: its price, its value per contract, contracts, seconds to close."""
    out = []
    for m in e["markets"]:
        if m.get("result") not in ("yes", "no") or not m.get("close"):
            continue
        close = _ts(m["close"])
        for created, taker_side, yp, np_, n in m["trades"]:
            side = "no" if taker_side == "yes" else "yes"
            price = np_ if side == "no" else yp
            if not 0 < price < 1 or n <= 0:
                continue
            win = 1.0 if m["result"] == side else 0.0
            tp = 1 - price
            out.append({"price": price, "value": win - price, "n": n, "to_close": close - _ts(created),
                        "taker_value": (1 - win) - tp - taker_fee(tp, n)})
    return out


def _boot(per_event: dict) -> dict:
    ev = [sum(v * n for v, n in xs) / sum(n for _, n in xs) for xs in per_event.values() if xs]
    if not ev:
        return {"events": 0, "mean_cents": None, "lo90": None, "hi90": None}
    rng = random.Random(SEED)
    ms = sorted(sum(rng.choice(ev) for _ in ev) / len(ev) for _ in range(BOOT))
    return {"events": len(ev), "mean_cents": round(100 * sum(ev) / len(ev), 3),
            "lo90": round(100 * ms[int(0.05 * BOOT)], 3), "hi90": round(100 * ms[int(0.95 * BOOT) - 1], 3)}


def contrast(rows_by_event: dict, keep, key: str = "value") -> dict:
    g = {ev: [(r[key], r["n"]) for r in rows if keep(r)] for ev, rows in rows_by_event.items()}
    out = _boot(g)
    out["contracts"] = round(sum(n for xs in g.values() for _, n in xs))
    return out


def report() -> dict:
    by: dict = {"all": {}, "first_half": {}, "second_half": {}}
    for ev, d in events():
        p = DATA / f"{ev}.json.gz"
        if not p.exists():
            continue
        rows = maker_rows(json.loads(gzip.decompress(p.read_bytes())))
        by["all"][ev] = rows
        by["first_half" if d < SPLIT else "second_half"][ev] = rows
    out = {"events_pulled": len(by["all"]), "events_registered": len(events())}
    for part, rbe in by.items():
        out[part] = {
            "maker_by_price": {f"{lo:.2f}-{hi:.2f}": contrast(rbe, lambda r, lo=lo, hi=hi: lo <= r["price"] < hi)
                               for lo, hi in PRICE_BUCKETS},
            "maker_by_time_to_close": {f"{lo}-{hi}s": contrast(rbe, lambda r, lo=lo, hi=hi: lo <= r["to_close"] < hi)
                                       for lo, hi in TIME_BUCKETS},
            "favourite_maker": contrast(rbe, lambda r: r["price"] >= FAVOURITE),
            "all_makers": contrast(rbe, lambda r: True),
            "all_takers_after_fee": contrast(rbe, lambda r: True, key="taker_value")}
    f, h1, h2 = (out[p]["favourite_maker"] for p in ("all", "first_half", "second_half"))
    out["verdict"] = ("go to the forward simulation" if all(x["mean_cents"] is not None and x["mean_cents"] > 0.5
                                                            for x in (h1, h2)) and (f["lo90"] or 0) > 0
                      else "stop on these series")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(prog="edgescout.maker_history")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("pull")
    pl.add_argument("--study", choices=["crypto", "lip", "fwd"], default="crypto")
    r = sub.add_parser("report")
    r.add_argument("--study", choices=["crypto", "lip", "fwd"], default="crypto")
    r.add_argument("--json")
    a = ap.parse_args()
    if a.cmd == "pull":
        {"lip": pull_lip, "fwd": pull_fwd}.get(a.study, pull)()
    else:
        out = {"lip": report_lip, "fwd": report_fwd}.get(a.study, report)()
        print(json.dumps(out, indent=1))
        if a.json:
            Path(a.json).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
