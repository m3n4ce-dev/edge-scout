# Research plan: edges in major crypto (not memecoins)

## The premise, and the discipline

Price discrepancies between venues are real: perpetual funding rates differ, dated futures trade at a premium, and spot prices drift apart. The question is never "is there a gap". It's this: **does the gap beat every cost, often enough and long enough, on venues we're allowed to trade, at our fee tier?**

That takes measurement over weeks, not screenshots. The same rules as `meme_traderv1` apply:

- Measure net of all fees. Count costs both ways in, both ways out, and the cost of holding the capital (idle cash earns ~4%).
- Record first and conclude later. A gap seen once is an anecdote; its frequency × size × duration is a result.
- Write a hypothesis down and freeze it before judging it on data it hasn't seen. Paper-trade with realistic fills.
- A tiny live pilot comes next, and only with the owner's explicit OK. Scale only what survives.

## Constraints (measured 2026-10-04, from this machine in the US)

- **Where a US resident may trade:** Coinbase (spot; US perpetual-style and dated futures on Coinbase Derivatives), Kraken (spot; US perps via Bitnomial since June 2026), Gemini, Bitstamp, Binance.US, and Kalshi (prediction markets).
- **Data only:** OKX, Hyperliquid, dYdX and Deribit answer, but their terms bar US persons. Binance and Bybit refuse US addresses outright. See [ACCESS.md](ACCESS.md).
- **Retail fees are the binding constraint:**

  | Venue | Entry tier (maker / taker) | $10K–50K 30-day volume |
  |---|---|---|
  | Coinbase, Gemini | 0.60% / 1.20% | 0.25% / 0.40% (Coinbase), 0.40% / 0.80% (Gemini) |
  | Kraken | 0.40% / 0.80% | lower |

  Fees are kept in `config/fees.yaml`; anything not yet checked against the venue's schedule is marked unverified.

## First measurements (a 1-minute scan, 2026-10-04 ~05:00 UTC; `edgescout report` for the latest)

| Edge | Gross | After entry-tier fees | Verdict so far |
|---|---|---|---|
| Spot, same coin on two US venues | BTC 0.006%, ETH 0.009%, ARB 0.30% at best | Never positive (fees 0.8–2.4% round trip) | Dead at our tier, as expected. Keep recording for rare dislocations |
| Dated futures vs spot (buy spot, short the future, hold to expiry) | BTC/ETH 3–7%/yr; XRP Nov 10.6%/yr | −13% to +2.4%/yr | Roughly cash yield at best. Worth it only when basis spikes (bull markets: 15–25%/yr) |
| **US funding carry** (Coinbase spot long + Coinbase perp short) | LINK 16.6%/yr, AVAX 13.1%/yr; BTC/ETH ~1–2% | LINK +1.6%/yr (30-day hold), **+11.6%/yr (90-day hold)** | **Best lead.** Depends on funding staying high; needs weeks of data |
| Cross-venue funding spreads | 11–38%/yr between venues | n/a for US: at least one leg is offshore | Not tradable for a US person; useful as a signal |

## Candidates, best first

1. **US funding carry on alts with persistently high funding.** Hold spot on Coinbase, short the same amount of the Coinbase perp, and collect hourly funding while the price legs cancel.
   - *Measure:* funding persistence per coin over weeks. What share of hours stays above the break-even rate, and how fast does it mean-revert after spikes?
   - *Risks:* funding flips negative; the short leg needs margin (a sharp rally can liquidate an under-margined short); everything sits at one venue.
   - *Kill if:* expected net < cash yield after fees at our tier over a 30–90 day hold.
2. **Kalshi crypto price markets vs options-implied probabilities.** Kalshi lists daily and hourly BTC/ETH price brackets (e.g. "BTC ≥ $93,800 on Oct 4"). Deribit's options imply a probability for the same event, and retail-driven brackets may misprice tails.
   - *Measure:* record both, then compare Kalshi price vs implied probability vs the actual outcome over hundreds of markets.
   - *Kill if:* the bias doesn't beat Kalshi's fees (~0.07 × p × (1−p) per contract) after spread.
   - This is relative value, not riskless arbitrage, and it's US-legal on Kalshi. Kalshi vs Polymarket on the same event is a second angle (cross-venue arb bots claim 1–5% edges).
3. **Basis and funding spike alerts.** The always-on version of both trades is near cash yield. The money is in the extremes (funding 50–100%/yr, basis 20%+), which arrive in fast markets. Alert and act on US venues only.
4. **Liquid-staking token discounts** (jitoSOL, mSOL, stETH vs their redemption value). Episodic and on-chain; needs stake-pool exchange rates. Later.

**Not pursuing:**
- DEX–CEX and MEV arbitrage: that's professional searchers' turf (co-location, inventory, sub-100 ms).
- Triangular arbitrage: HFT.
- Funding arbitrage using offshore venues: not legal for a US person.

## Milestones

- **M0, now:** the recorder runs 24/7 (`scripts/edge-scout.service`). After 2 weeks, report funding persistence, basis behaviour and spot dislocations with real frequencies.
- **M1:** a Kalshi + Deribit recorder (option-implied probabilities for each bracket), then a calibration report. *Recording since 2026-10-09; the analysis and its verdict rules were registered before any data (docs/M1_KALSHI.md, `python -m edgescout m1`): a 7-day development window, then a 14-day holdout.*

**Funding persistence, 2026-10-09** (M0, 4.8 days: 10-04 00:00 to 10-09 01:00 UTC). The US-perp lead didn't persist. Daily mean funding, annualized:

| Contract | 10-04 | 10-05 | 10-06 | 10-07 | 10-08 | 10-09 (partial) |
|---|---|---|---|---|---|---|
| Coinbase LINK | 13.3% | 16.9% | 24.5% | 8.8% | 1.8% | −3.0% |
| Coinbase AVAX | 12.7% | 10.8% | 13.6% | 13.6% | −4.7% | 6.1% |
| Coinbase BTC / ETH | about 3–5% throughout, about cash | | | | | |
| Kraken SOL | 23.3% | −6.2% | 14.3% | −1.6% | 4.9% | 25.8% |

The longest stretch above 5%/yr was 54 hours (LINK). Spikes revert within days, and a 30–90 day carry at entry-tier fees doesn't clear cash on this evidence. Recording continues to the 2-week report.
- **M2:** freeze the best candidate (rules, sizes, costs, kill criteria) and paper-trade it with realistic fills.
- **M3:** a tiny live pilot, sized by the owner (e.g. $200–500), only with an explicit OK, reconciled against the paper model.
- **M4:** scale only what survives, re-measuring at each step.

## What more capital changes (and doesn't)

- **Fee tiers:** $10K+ of 30-day volume cuts Coinbase's taker fee from 1.20% to 0.40%. That moves carry and basis from "≈ cash" toward "a few % over cash". Never trade volume just to reach a tier.
- **Inventory on two venues:** cross-venue trades need capital parked on both sides.
- **Size:** none of the above changes the shape of an edge. A trade that loses at $500 loses at $50,000; it doesn't get better with size.

## Sources

- [Funding rate arbitrage in 2026 (CoinMarketCap Academy)](https://coinmarketcap.com/academy/article/crypto-delta-neutral-strategy-2026)
- [Funding rate arbitrage guide with calculations](https://arbitrageghost.medium.com/funding-rate-arbitrage-in-2026-the-complete-guide-with-real-calculations-40e6cf341e52)
- [Kraken US perpetual futures (CoinDesk, 2026-06-15)](https://www.coindesk.com/markets/2026/06/15/kraken-debuts-u-s-perpetual-futures-as-crypto-derivatives-move-onshore)
- [Coinbase US perpetual futures](https://www.coinbase.com/blog/perpetual-futures-have-arrived-in-the-us)
- [Kalshi vs Polymarket arbitrage guide](https://clawarbs.com/blog/kalshi-vs-polymarket-arbitrage/)
- [Polymarket/Kalshi arbitrage bot (GitHub)](https://github.com/ImMike/polymarket-arbitrage)
- [Multi-exchange arbitrage bots: why they fail](https://botversusbot.com/articles/multi-exchange-arbitrage-crypto-bots-2026/)
- [Coinbase fees 2026](https://www.datawallet.com/crypto/coinbase-fees)
- [Kraken Pro fees 2026](https://www.bitget.com/amp/academy/kraken-pro-fees-2026)
- [Gemini review 2026 (fees)](https://university.cex.io/gemini-review/)
- Venue APIs used: Coinbase Exchange and Advanced (market endpoints), Kraken and Kraken Futures, Gemini, Bitstamp, Binance.US, Hyperliquid, OKX, dYdX v4 indexer, Deribit, Kalshi.
