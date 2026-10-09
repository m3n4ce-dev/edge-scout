# M1: Kalshi crypto markets vs options-implied probabilities (registered 2026-10-09 01:40 UTC, before any data)

**The question.** Do Kalshi's hourly and daily BTC/ETH price markets misprice the event "the price is above K at close"? Misprice means relative to two things:
- what actually happens;
- the probability Deribit's options imply for the same event.

Does any mispricing survive Kalshi's fees and spread? Kalshi is legal for a US resident. Deribit is read only, for its prices.

**What's recorded** (`edgescout/kalshi.py`, in the 24/7 scan, every 60 s; public data, no keys, nothing traded):
- **Kalshi markets:** "above K" (KXBTCD, KXETHD) and ranges (KXBTC, KXETH), with a mid between 2¢ and 98¢: strike(s), close, both sides' best bid/ask and sizes.
- **Deribit options:** BTC and ETH, expiring within 3 days, strikes within ±10% of the index: mark IV, bid/ask, forward. Plus the index.
- **Settled events,** once each: the settlement index value, the rule text, every market's (strike, result).

The settlement rule is Kalshi's: the simple average of CF Benchmarks' Real-Time Index over the 60 seconds before the close.

**This document is frozen before any recorded data is analysed.** A change after that is a new, dated version, never an edit.

## The estimands

The unit of evidence is the **event** (one close time of one series). Strikes within an event share one outcome path, so they aren't independent. Every interval is an event-clustered bootstrap (2,000 resamples, seed 20261009).

1. **Calibration.** "Above K" markets are sampled at fixed lead times before the close: 6 h, 3 h, 1 h, 45, 30, 15 and 5 minutes. Each lead is used wherever a quote was recorded within the 2 minutes before it: an hourly event has no quote hours before its close, and a daily event has them all.

   Using the Kalshi mid at the last recorded quote at or before each lead, report the outcome rate minus the mean mid in each bucket: 2–10¢, 10–30¢, 30–70¢, 70–90¢, 90–98¢.
2. **Options-implied probability**, P_imp(S_T > K), for each sampled market:
   - **the smile:** per Deribit expiry, from the last recorded options at or before the sample time; IV against log-moneyness, linearly interpolated, flat beyond the last strike;
   - **the time:** total variance and the forward interpolated linearly in time between the two expiries bracketing the Kalshi close. If no expiry is earlier than the close, the nearest expiry's smile is used, with variance scaled by time;
   - **the digital:** −dC/dK by a central difference (±0.5% of K) on Black-76 call prices from the smile (r = 0).

   **Known approximations, kept and reported:**
   - Kalshi settles on a 60-second average of a different index (BRTI) than Deribit's;
   - Deribit's index-to-BRTI basis isn't recorded;
   - the average slightly lowers the variance.
3. **The rule to test** (what M2 would paper-trade). At each sampled lead:
   - **buy YES** at the ask if P_imp − ask − fee ≥ θ;
   - **buy NO** at the no-ask if (1 − P_imp) − no_ask − fee ≥ θ.

   The terms:
   - θ = 3¢;
   - fee is Kalshi's taker fee, ceil(0.07 × p × (1 − p) × 100) / 100 per contract at price p;
   - only quotes showing at least 10 contracts count.

   **Value per contract:** outcome − price − fee, averaged per event first, then across events.
4. **Secondary** (reported, not decided on): the same with Kalshi's own mid as the predictor in place of P_imp (pure calibration bias), the range markets, and the fill capacity (the sizes shown).

## The split and the verdict

- **Development:** the first 7 UTC days after the recorder starts. The methods above are debugged on them. Nothing in the rule (θ, buckets, leads, the minimum size) is changed after looking at their results. A bug fix is allowed and recorded.
- **Holdout:** the next 14 UTC days, untouched until they're complete.
- **Kill,** if on the holdout either holds:
  - the rule's mean value per contract is ≤ 0;
  - its 90% event-clustered interval includes 0 and its point estimate is under 1¢.
- **Proceed to M2** (paper with realistic fills) only if the holdout's 90% interval is above 0 **and** there are at least 100 events with a trade.
- **Inconclusive** otherwise. Report it as such; don't extend it, don't re-tune.

**Not part of this:** any order, any account, any key. A live pilot (M3) needs the owner's explicit OK, sized by the owner.
