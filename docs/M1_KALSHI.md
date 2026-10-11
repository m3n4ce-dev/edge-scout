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

---

# M1b: Kalshi vs Polymarket on the same event (registered 2026-10-09 02:00 UTC, before any Polymarket data)

**The question.** Polymarket's daily BTC/ETH markets resolve at 12:00 ET, on Binance's BTC/USDT (ETH/USDT) 1-minute candle close. Kalshi's hourly "above" event for 12:00 ET resolves at the same moment, on CF Benchmarks' 60-second average. Do the two venues price the same event differently by more than fees?

**Access.** International Polymarket's markets are flagged `restricted`: its terms bar US persons. For the owner (US), Polymarket is a **price signal** here, not a venue, unless the owner confirms access through Polymarket's US exchange. The tradable leg is Kalshi.

**What's recorded** (`edgescout/polymarket.py`, every 60 s, public data):
- the "above" and range markets for BTC/ETH closing within 36 h, with a mid between 2¢ and 98¢: YES best bid/ask, spread, liquidity, 24-hour volume, the fee schedule (0.07 × p × (1 − p), taker only);
- each settled event once, with every market's result.

**The pairing.** A Polymarket "above S" market matches the Kalshi "above" market of the same coin closing at the same moment with floor S − 0.01. Kalshi's YES is ≥ S, Polymarket's is > S, so the two differ only by a tie at S. Both are sampled at the M1 leads (in practice the last hour: 45, 30, 15 and 5 minutes, since Kalshi's hourly events open an hour ahead). Each lead uses each venue's last quote within the 2 minutes before it. Both outcomes are kept: each venue settles on its own index.

**The estimands** (event-clustered, as in M1):
1. **The rule on Kalshi alone:** the M1 rule, with Polymarket's mid in place of the options-implied probability (θ = 3¢, Kalshi's taker fee, at least 10 contracts shown). This is the version a US resident could trade.
2. **Boxes (research only:** they need both venues). YES on one venue plus NO on the other, each at its ask with its taker fee, kept when the cost is at most 99¢. Its value is the payoff, which is 1 unless the two indexes land on opposite sides of S, minus the cost. Count them, and value them per box.
3. **Basis:** how often matched markets settle differently, and the mean Polymarket-minus-Kalshi mid.

**Windows and verdict** (as M1, from the first recorded Polymarket row):
- **Development:** to the end of the 7th full UTC day.
- **Holdout:** the 14 days after, refused until complete.
- **The decision is on estimand 1.** Kill if its holdout mean value per contract ≤ 0, or its 90% interval includes 0 with a point estimate under 1¢. Proceed only if the interval is above 0 with at least 100 events traded. Otherwise inconclusive.
- **Boxes are reported, never a verdict,** while Polymarket isn't accessible to the owner.

**Approximations, kept and reported:**
- Kalshi's fee is rounded up per contract here (it's rounded per order in fact): conservative.
- Polymarket's fee isn't rounded.
- Quoted sizes on Polymarket aren't recorded (only its liquidity), so boxes assume the shown Kalshi size only.

---

## Development notes (2026-10-11 UTC)

Written 2026-10-11 08:11 UTC, inside the development window (which runs to 2026-10-17 00:00 UTC). Two bugs were found by reading the development data, as the registration allows ("A bug fix is allowed and recorded"). The frozen text above is not edited. **No rule parameter changed:** θ (3¢), the price buckets, the leads, the 10-contract minimum, both fee formulas, θ for the boxes (1¢), the windows, and the bootstrap (2,000 resamples, seed 20261009) are as registered. **The holdout is untouched:** `--holdout` was never run (it is refused until the holdout is complete, and the holdout starts after the development window ends); every number below is development data. The analysis code is `edgescout/m1.py`.

### 1. M1: the digital was differenced with a fixed step (fixed)

- **What was wrong.** `implied_above` computes P(S_T > K) = −dC/dK by a central difference on Black-76 prices. The step was a fixed ±0.5% of K. The estimand is a derivative, so the step has to be small against the width of the price distribution at the strike, and at the short leads it was not. Kalshi's hourly events are sampled down to 5 minutes, where the standard deviation of ln S_T is σ√T. At 50% vol that is about 0.15% of K at 5 minutes and 0.46% at 45 minutes. A ±0.5% window is then about ±3 sd (5 min) to ±1 sd (45 min): the quotient is the digital averaged over that window, which pulls P_imp toward ½. Flat 50% vol, 5 minutes, true N(d2) against the fixed step: −1 sd 0.841 against 0.654; −½ sd 0.691 against 0.577; at the money 0.500 against 0.500 (by symmetry); +½ sd 0.308 against 0.423; +1 sd 0.159 against 0.347; +2 sd 0.023 against 0.200; +3 sd 0.001 against 0.082. This is a numerical error in the estimate of the registered quantity, not a modelling choice. (The registration's "±0.5% of K" described the step; the intent was the derivative.)
- **How it was found.** In the development read: the sampled leads reach down to 5 minutes, and the digital was checked against its closed form there.
- **What changed.** The step now follows the distribution: h = K · min(0.5%, max(0.001%, 5% · √w0)), with w0 the total variance at the strike, found first (`_w_f`). The smile, the time interpolation between expiries, the forward, the Black-76 prices and the clamp to [0, 1] are as registered. The 0.5% cap binds only at horizons of months; at one day the new step is about 0.13% of K (it was 0.5%) and the two agree to 0.002. Tests (`tests/test_m1.py`): a flat smile at 5 minutes matches N(d2) to 0.002 from −3 sd to +3 sd; the same from 5 minutes to a day; a skewed smile matches N(d2) − vega·dσ/dK at 5 minutes and at a day (the original skew test is kept); a regression test that the fixed step is off by more than 0.05 within 1 sd of the strike at 5 minutes and the new step is not; the step's bounds.
- **Effect on the development read** (one snapshot, 12,219 samples, same data before and after). Mean |ΔP_imp| 0.0195 (largest 0.3815); by lead, 5/15/30/45/60/180/360 min: 0.0185, 0.0210, 0.0207, 0.0178, 0.0806, 0.0408, 0.0146. The calibration buckets use Kalshi's mid only and are unchanged. The rule: trades 1,347 → 813; value per contract mean −0.04638 → −0.01170 (90% interval −0.04123 to 0.01767); implied minus mid mean −0.00789 → −0.01076. Recorded for the audit trail; the fix follows from the arithmetic above, not from these numbers.

### 2. M1b: why the cross-venue read found only 5 samples from 2 pairs (diagnosed; the pairing made robust)

- **What was suspected.** That the pairing (same coin, same close moment, Kalshi floor S − 0.01) failed to match markets that exist on both venues: closes that differ by a second, a strike mismatch such as 120000 against 119999.99, missing leads, or settled-outcome keys that do not line up.
- **How it was found.** A throwaway script counted the candidates surviving each stage on the development data (as of 2026-10-11 08:10 UTC; the window starts at the first Polymarket row, 2026-10-09 02:04 UTC). Polymarket "above" markets recorded with a close in the window: 28, which are the 12:00 ET closes of Oct 9 to Oct 12. Of those, 15 have a settled Polymarket outcome (the Oct 11 and Oct 12 closes are still open). All 15 have Kalshi markets at exactly the same close, to the second; allowing a 60-second difference finds the same 15. A Kalshi market with floor S − 0.01 was recorded for 7 of the 15. Kalshi's floors are all of the form x.99, so the strike relation is as registered; the other 8 exist on Kalshi at the same close (checked against Kalshi's public market list on 2026-10-11: for example KXBTCD-26OCT0912-T79999.99, closing 2026-10-09T16:00:00Z) but were never recorded. Kalshi outcomes were settled for all 7, and the Polymarket settled-outcome keys matched for all 7. Per lead, a quote from each venue within the 2 minutes before: Polymarket 2, 2, 1, 1 of the 7 at 45, 30, 15 and 5 minutes; Kalshi 5, 4, 3, 2; both 2, 2, 1, 0. That is 5 samples from 2 pairs (ETH above 2500, on Oct 9 and Oct 10).
- **The cause is not the matching.** Both venues record a market only while its mid is within 2¢ to 98¢, and Kalshi's hourly event opens about an hour before its close. Polymarket lists 11 strikes per coin per day (BTC 2,000 apart, ETH 100 apart). In the last hour a strike is inside both bands only if the price is within about 2 sd of it (the standard deviation of ln S at 50% vol is 0.15% at 5 minutes and 0.46% at 45 minutes, so about $120 to $370 on BTC and $4 to $12 on ETH). At most one or two strikes per coin per day qualify; expect about one pair per coin per day, not dozens. The recorder itself is complete: both feeds ran at a 60-second cadence with no gap over 75 seconds; there was one Polymarket error in the window (a read timeout); the daily events are fetched (in a live listing they sit at positions 0 to 17 of the crypto-tag events, inside the recorder's 5 pages of 100). If Polymarket's quotes were also kept outside 2¢ to 98¢, the 44 strikes listed by the 4 settled in-window events would give at most 26 (strike, lead) cells with a Kalshi quote (8, 7, 6 and 5 at 45, 30, 15 and 5 minutes), many of them one-sided Kalshi books, against 5 now.
- **What changed.** Closes are matched within `CLOSE_TOL_S` = 60 seconds rather than by float equality, in a new `kalshi_counterpart`: the same moment stated to the second differently by the two venues must still pair. The nearest close wins, then the nearest floor. The pairing's meaning is unchanged: the same coin, the same close moment, Kalshi floor S − 0.01 (Kalshi ≥ S, Polymarket > S). On the recorded data the venues' closes already agree exactly, so the development read is unchanged (5 samples, 2 pairs). Tests (`tests/test_polymarket.py`) on synthetic rows: pairing at every lead when both venues quote through the hour; closes 1 s to 60 s apart pair; closes 61 s, 5 minutes and an hour apart do not; floors other than S − 0.01 do not pair; a lead is lost when either venue has no quote in the 2 minutes before it (the band case above); the nearest close and floor are chosen.
- **Not changed.** The recorder, because the 2¢ to 98¢ band is part of what the registration says is recorded, for both venues. The number of pairs this design can yield is low and is stated here; widening what is recorded would be a new, dated version of the registration, not a bug fix.

### 3. M1: calibration used the "mid" of one-sided books (fixed 08:16 UTC)

- **What was wrong.** Kalshi shows an empty side of a book as a placeholder price with no size: an ask of $1.00 or a bid of $0.00. The calibration buckets and the implied-minus-mid gap averaged bid and ask without checking for that, so a book with a 50¢ bid and no ask counted as a 75¢ "mid". Of the 12,219 development samples, 1,885 are two-sided. The rest are one-sided: 5,189 with no ask and 5,145 with no bid. The registered quantity is the outcome minus the market's mid, and a one-sided book has none. This is a parsing error, not a choice.
- **How it was found.** The development read's calibration was implausible: the 10–30¢ bucket came in 10.8¢ below its mid and 70–90¢ 11.2¢ above, on 2 days. The subagent's breakdown by book type traced it to the one-sided books.
- **What changed.** `two_sided(s)` requires a bid and an ask strictly inside (0, 1) with size on both. Calibration and the implied-minus-mid gap use only those samples, and the read reports how many there are. **The rule is unaffected:** it already needed 10 contracts shown on the side it buys, so all 813 trades were on two-sided books.
- **Effect on the development read** (same data). Calibration, outcome minus mid by bucket:
  | Bucket | Before | After |
  |---|---|---|
  | 2–10¢ | −2.6¢ | −2.6¢ |
  | 10–30¢ | −10.8¢ | −3.6¢ |
  | 30–70¢ | −7.6¢ | +1.2¢ |
  | 70–90¢ | +11.2¢ | +5.3¢ |
  | 90–98¢ | +3.5¢ | +3.4¢ |

  Implied minus mid: −1.08¢ before, +0.54¢ after. Recorded for the audit trail. The fix follows from what the placeholder is, not from these numbers.
