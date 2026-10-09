"""edge-scout: measure where crypto prices disagree, and whether any of it survives real costs.

    python -m edgescout scan --minutes 60        record public market data (no keys, nothing traded)
    python -m edgescout report                   summarize everything recorded so far
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .report import print_summary, read, summarize
from .scan import scan


def main() -> None:
    ap = argparse.ArgumentParser(prog="edgescout")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="record public market data")
    s.add_argument("--minutes", type=float, default=60)
    s.add_argument("--spot-every", type=float, default=10)
    s.add_argument("--deriv-every", type=float, default=60)
    s.add_argument("--quiet", action="store_true")
    m = sub.add_parser("m1", help="M1: Kalshi vs options-implied probabilities (docs/M1_KALSHI.md)")
    m.add_argument("--holdout", action="store_true", help="the holdout window (refused until it's complete)")
    m.add_argument("--json", help="also write the result here")
    r = sub.add_parser("report", help="summarize recorded scans")
    r.add_argument("--file", nargs="+")
    r.add_argument("--json", help="also write the summary here")
    a = ap.parse_args()
    if a.cmd == "scan":
        asyncio.run(scan(a.minutes, a.spot_every, a.deriv_every, a.quiet))
    elif a.cmd == "m1":
        from .m1 import run
        from .scan import DATA
        out = run(DATA, holdout=a.holdout)
        print(json.dumps(out, indent=1))
        if a.json:
            Path(a.json).write_text(json.dumps(out, indent=1))
    else:
        s = summarize(read([Path(f) for f in a.file] if a.file else None))
        print_summary(s)
        if a.json:
            Path(a.json).write_text(json.dumps(s, indent=1))


if __name__ == "__main__":
    main()
