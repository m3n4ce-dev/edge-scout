"""Record public market data at fixed intervals into data/scan-YYYY-MM-DD.jsonl (one JSON object per line):
{"t": unix, "kind": "spot" | "funding" | "futures", "rows": [...], "errors": {...}}. Measuring first, over
days, is the point: a gap seen once is an anecdote; how often it beats costs, and for how long, is a result."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from . import venues

DATA = Path(__file__).resolve().parent.parent / "data"


def _write(kind: str, rows: list, errors: dict) -> None:
    DATA.mkdir(exist_ok=True)
    path = DATA / f"scan-{time.strftime('%Y-%m-%d', time.gmtime())}.jsonl"
    with path.open("a") as f:
        f.write(json.dumps({"t": round(time.time(), 3), "kind": kind, "rows": rows, "errors": errors}) + "\n")


async def scan(minutes: float, spot_every: float = 10, deriv_every: float = 60, quiet: bool = False) -> None:
    end = time.time() + minutes * 60
    last_deriv = 0.0
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
            await asyncio.sleep(max(0.0, spot_every - (time.time() - t0)))
