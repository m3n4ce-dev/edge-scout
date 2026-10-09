"""Record public market data at fixed intervals into data/scan-YYYY-MM-DD.jsonl (one JSON object per line):
{"t": unix, "kind": "spot" | "funding" | "futures", "rows": [...], "errors": {...}}. Measuring first, over
days, is the point: a gap seen once is an anecdote; how often it beats costs, and for how long, is a result."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from . import bitnomial, kalshi, polymarket, venues

DATA = Path(__file__).resolve().parent.parent / "data"


def _write(kind: str, rows: list, errors: dict) -> None:
    DATA.mkdir(exist_ok=True)
    path = DATA / f"scan-{time.strftime('%Y-%m-%d', time.gmtime())}.jsonl"
    with path.open("a") as f:
        f.write(json.dumps({"t": round(time.time(), 3), "kind": kind, "rows": rows, "errors": errors}) + "\n")


async def scan(minutes: float, spot_every: float = 10, deriv_every: float = 60, quiet: bool = False,
               kalshi_every: float = 60, settled_every: float = 600, bitnomial_every: float = 1800) -> None:
    end = time.time() + minutes * 60
    last_deriv = last_kalshi = 0.0
    last_settled = time.time() - kalshi.SETTLED_LOOKBACK_S
    settled_seen: set = set()                            # events already written: each settled event once
    last_bitnomial, funding_seen = 0.0, set()            # M3: Bitnomial funding, each (product, interval) once
    async with venues.client() as c:
        while time.time() < end:
            t0 = time.time()
            spot, err = await venues.spot_quotes(c)
            _write("spot", spot, err)
            if t0 - last_deriv >= deriv_every:
                last_deriv = t0
                mid = {}
                for r in spot:
                    if r["venue"] == "coinbase":
                        mid[r["symbol"]] = (r["bid"] + r["ask"]) / 2
                fund, ferr = await venues.perp_funding(c)
                _write("funding", fund, ferr)
                fut, uerr = await venues.dated_futures(c, mid)
                _write("futures", fut, uerr)
                if not quiet:
                    print(f"{time.strftime('%H:%M:%S')} spot {len(spot)} rows, funding {len(fund)}, futures {len(fut)}"
                          + (f" | unreachable: {', '.join({**err, **ferr, **uerr})}" if {**err, **ferr, **uerr} else ""),
                          flush=True)
            if kalshi_every and t0 - last_kalshi >= kalshi_every:      # M1: Kalshi beside Deribit's options
                last_kalshi = t0
                k, kerr = await kalshi.kalshi_open(c)
                _write("kalshi", k, kerr)
                o, oerr = await kalshi.deribit_options(c, t0)
                _write("deribit_options", o, oerr)
                p, perr = await polymarket.polymarket_open(c, t0)          # M1b: the same events on Polymarket
                _write("polymarket", p, perr)
                if t0 - last_settled >= settled_every:
                    st, serr = await kalshi.kalshi_settled(c, last_settled - kalshi.SETTLED_LOOKBACK_S)
                    new = [r for r in st if r["event"] not in settled_seen]
                    settled_seen.update(r["event"] for r in new)
                    _write("kalshi_settled", new, serr)
                    ps, pserr = await polymarket.polymarket_settled(c, last_settled - kalshi.SETTLED_LOOKBACK_S, t0)
                    pnew = [r for r in ps if ("pm", r["event"]) not in settled_seen]
                    settled_seen.update(("pm", r["event"]) for r in pnew)
                    _write("polymarket_settled", pnew, pserr)
                    last_settled = t0
            if bitnomial_every and t0 - last_bitnomial >= bitnomial_every:
                last_bitnomial = t0
                bn, berr = await bitnomial.bitnomial_funding(c, t0)
                new = [r for r in bn if (r["product_id"], r["interval_start"]) not in funding_seen]
                funding_seen.update((r["product_id"], r["interval_start"]) for r in new)
                _write("bitnomial_funding", new, berr)
            await asyncio.sleep(max(0.0, spot_every - (time.time() - t0)))
