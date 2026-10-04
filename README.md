# edge-scout

Measures where major-crypto prices disagree (BTC, ETH, SOL, XRP, LINK, ARB, AVAX) and whether any of it survives real costs, on venues a US resident can actually trade.

It reads public market data only. It has no API keys and trades nothing. What it does:

- **Spot:** best bid/ask on Coinbase, Kraken, Gemini, Bitstamp and Binance.US. It reports how often buying on one venue and selling on another beats both legs' fees.
- **Funding:** perpetual funding on Coinbase's US perps plus Hyperliquid, OKX, dYdX, Deribit and Kraken Futures, normalized to % per year. It also reports the US-legal carry trade: Coinbase spot long + Coinbase perp short, after fees.
- **Basis:** dated futures (Coinbase Derivatives, Deribit) vs spot, annualized, before and after fees, compared with what idle cash earns.

## Quick start

```bash
python3 -m venv --without-pip .venv && ../meme_traderv1/.venv/bin/pip --python .venv/bin/python install -r requirements.txt
.venv/bin/python -m edgescout scan --minutes 60     # record (data/scan-YYYY-MM-DD.jsonl)
.venv/bin/python -m edgescout report               # summarize everything recorded
.venv/bin/python -m pytest -q                       # tests
```

To run it 24/7 as a user service: `scripts/install-service.sh` (stop with `systemctl --user stop edge-scout`).

## Where it stands

- [docs/RESEARCH_PLAN.md](docs/RESEARCH_PLAN.md): candidates, first measurements, milestones, and what would kill each idea.
- [docs/ACCESS.md](docs/ACCESS.md): which venues answer from the US, and which a US resident may trade.

First scan, 2026-10-04:
- **Spot:** gaps between US venues are hundredths of a percent, against 0.8–2.4% of round-trip fees at entry tier. Dead, as expected.
- **Dated futures:** about cash yield.
- **Best lead:** high funding on a few US perps (LINK ~17%/yr, AVAX ~13%/yr). That's worth tracking for persistence before anyone trades it.

Fees live in `config/fees.yaml`. Every conclusion depends on them, so keep them current.
