# M2-pre: what Kalshi's makers earned on BTC/ETH hourly markets (registered 2026-10-09 22:35 UTC, before any trade was pulled)

**Why.** Three independent research passes (2026-10-09) converged on one lead: on Kalshi, resting (maker) orders have earned what takers lost, about +1.1% against −1.1% across 72M trades to Nov 2025. Makers buying at 50¢ or more did best, and the effect is strongest in crypto. Those samples are old and mostly not about our markets. This study measures it on **the markets we record**, from Kalshi's public trade tape, before any forward test or money.

**The data.** Kalshi's public `GET /markets/trades` (every trade, with `taker_side`, prices and size) for the hourly "above K" events of **KXBTCD and KXETHD that close at 12:00 ET**. That's one event per coin per day, from 2026-08-11 to 2026-10-08 (the tape before 2026-08-10 is outside the public endpoint's range). Each event's settled markets give the result. Raw pulls are kept in `data/maker_history/`, one file per event.

**What a maker earned on a trade.** The maker is the taker's counterparty:
- the taker bought YES at p, so the maker bought NO at 1 − p;
- the taker bought NO, so the maker bought YES at its price.

Per contract, the maker's value is 1 if its side won, minus its price, minus a maker fee. The fee is **zero** on these series: their `fee_type` is `quadratic`, taker only. The taker's value is the negative, minus the taker fee ceil(0.07·p·(1 − p)·n)/n for the trade's n contracts.

**The contrasts (fixed now):**
1. **Maker value per contract** (contract-weighted within an event, then averaged across events), by the maker's price: 1–10¢, 10–30¢, 30–50¢, 50–70¢, 70–90¢, 90–99¢.
2. **The same by time to close:** at most 5 min, 5–15 min, 15–60 min, more than 60 min.
3. **The favourite contrast:** makers buying at 70¢ or more, all times.

**Intervals.** Event-clustered bootstrap (2,000 resamples, seed 20261009), 90%. Each contrast is reported for the whole period and for each half (08-11..09-09 and 09-10..10-08).

**Reading it:**
- **Go on to a forward maker simulation** (queue position, fills, markouts, the liquidity incentive program's share) only if contrast 3 is above +0.5¢ per contract in **both** halves, with a 90% lower bound above 0 on the whole period.
- **A pass is not a strategy.** These are the average filled resting order's returns, professionals' included. Ours would be a subset: later in the queue, adversely selected. The forward simulation decides that.
- **Otherwise** report it and stop this line on these series.

Nothing is traded; no account or key is used.

## Result (2026-10-09 22:56 UTC; all 118 registered events pulled, ~40M contracts)

| Contrast (maker value, ¢ per contract) | Whole period | 08-11..09-09 | 09-10..10-08 |
|---|---|---|---|
| **Favourite makers (≥ 70¢)** | **+0.05** (90%: −1.86 to +1.77) | −1.07 | +1.21 |
| All makers | −0.95 | −0.27 | −1.66 |
| All takers, after the taker fee | −0.17 | −0.83 | +0.52 |

- **By the maker's price** (whole period): 1–10¢ −0.41, 10–30¢ −1.52, 30–50¢ −0.85, 50–70¢ −1.22, 70–90¢ −0.40, 90–99¢ +0.40. Every one of these intervals includes 0.
- **By time to close:** ≤ 5 min −0.83 (−1.67 to −0.08); 5–15 min −0.35; 15–60 min −0.90 (−1.82 to −0.02).

**Verdict, per the registration: stop on these series.** The favourite contrast is not above +0.5¢ in both halves, and its lower bound is below 0.

**Reading.** On Kalshi's hourly BTC/ETH markets, takers beat resting orders by about 1¢ gross: they react to the underlying's moves. The taker fee is also about 1¢, so both sides lose on average. The published maker edge (sports, longshots, data to 2025) does not appear here. It is consistent with M1's design: an edge in these markets, if any, is in *selective* taking against a better model, not in resting quotes.

---

# M2-pre-b: the same, on the series Kalshi pays liquidity incentives on (registered 2026-10-09 22:57 UTC, before any of these trades was pulled)

**Why.** The published maker edge comes from retail-heavy categories, and Kalshi's liquidity incentive program (LIP) pays its pools there. The public `GET /incentive_programs` on 2026-10-09 listed 5,901 active programs. The largest daily pools are Rotten Tomatoes scores, NFL player-prop ladders, AAA state gas prices, Trump approval, rain and YouTube views. If makers there don't lose much on fills, the LIP reward can make quoting pay. Rewards can't be measured from history (they need order-book snapshots), so this study measures only the **fill side**.

**The series** (all `fee_type` quadratic: no maker fee):
- KXRT;
- KXNFLLADDERREC, KXNFLLADDERRECYDS, KXNFLFFPTSLADDER;
- KXAAAGASDOH, KXAAAGASDCO, KXAAAGASDIN, KXAAAGASDMI, KXAAAGASDNC, KXAAAGASDSC, KXAAAGASDTN;
- KXTRUMPAPPROVE, KXRAIN, KXYTVIEWSW.

**The events.** Every settled event of these series. Only trades created on or after 2026-08-10 (the public tape's start) count. Each event is dated by its last market's close. The halves split at 2026-09-10.

**The contrasts:** maker value per contract as in M2-pre (makers pay no fee). By the maker's price buckets, by category (entertainment, NFL props, gas, politics, weather, YouTube), the favourite contrast (≥ 70¢), and all makers. Same intervals.

**Reading it** (the rule differs from M2-pre because LIP rewards would come on top):
- **Proceed to a forward LIP simulation** (order-book share, fills, markouts) if the all-maker 90% interval's **upper** bound is above −0.5¢ per contract, i.e. makers can't be shown to lose half a cent or more. Its favourite and category results pick where to look first.
- **Stop** if the whole interval is below −0.5¢: rewards would then need to pay more than half a cent on every filled contract, which the median pool won't.
