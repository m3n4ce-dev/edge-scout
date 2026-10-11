# M4: quoting for Kalshi's liquidity incentives (registered 2026-10-11 08:09 UTC, before any of its order books were recorded)

**Why.** M2-pre-b measured only the fill side. On the series Kalshi pays liquidity incentives on, makers lost 1.21¢ per contract (event-weighted; +0.29¢ contract-weighted). Its stop rule assumed the pools pay under half a cent per filled contract. That assumption was never checked.

It was checked on 2026-10-11 against Kalshi's public program history: `GET /incentive_programs`, 302,808 programs, `period_reward` in centi-cents per Kalshi's API reference. Over 08-10..10-08, the pools on those 14 series were about **$1.63M**, against **80.4M** traded contracts. That's **about 2.0¢ per contract** overall, from 0.3¢ (rain) to 6.4¢ (gas) by series.

That's an upper bound on what was paid: a snapshot without the target size on both sides pays nothing. It also says nothing about what a newcomer would capture. This study measures that.

**Kalshi's rules** (its published Liquidity Incentive Program page, read 2026-10-11):
- **Snapshots:** the order book is snapshotted once a second.
- **Credit:** each side's depth counts up to the program's target size. The reference price is the first level, walking down from the best bid, where the cumulative size reaches a fifth of the target. Orders at or better than it get full credit; below it, the discount factor applies per tick.
- **Score:** both sides are scored separately.
- **Exclusion:** a snapshot is excluded unless both sides hold the target size.
- **Payout:** reward = your share of the score × the period reward × the fraction of snapshots not excluded.
- **Eligibility:** most regular US members are eligible. Kalshi affiliates, brokers' customers and non-US users are not.

**Looked at before registering** (not part of any window):
- the program list and pool sizes;
- one read of 150 live programs' books at 07:55 UTC. 129 had the target on both sides, and one market had 230,543 contracts resting at its best YES bid. That's why the simulation counts queue position: behind a queue that deep, a new order earns nothing.

## What's recorded (`edgescout/lip.py`, a task beside the 24/7 scan; public data, no keys)

- **The sample.** A fixed rule, not a choice: a market is in it when the SHA-256 of its ticker falls in the first 35 of 1,000 buckets. That's about 150 of the ~4,300 markets with live programs. New markets enter by the same rule as programs roll over.
- **The programs.** The sampled markets' active liquidity programs (period, reward, target size, discount factor), refreshed hourly. Each series' fee type is recorded once.
- **The books.** Every 120 s, both sides' bids for each sampled market with a running program, from the best down until the cumulative size passes twice the target.
- **Afterwards.** Each window's trade tape and results come from the public API (`python -m edgescout.m4 pull`).

## The rule (`edgescout/m4.py`; fixed now)

On each sampled market, while a program runs on it, at each recorded book:
- **Size:** rest **Q = target / 5** contracts on each side, a YES bid and a NO bid.
- **Price:** one tick better than the best bid on both sides when the spread is at least 3 ticks, which puts us first in the queue. Otherwise, at the best bid, behind the size already there.
- **Inventory:** no quoting on a side whose net position (YES − NO, or NO − YES) has reached the target size.
- **Duration:** a quote lasts until the next book, at most 300 s.

**Reward.** Per book, each side's credit is scored as Kalshi scores it, with our order inserted. Our share of a side is our credit over everyone's. A book counts only if both sides, ours included, hold the target. The program pays its period reward pro rata to time, times the time-weighted mean of (share_yes + share_no) / 2, with our resting size shrinking as fills come in until the next book.

**Fills,** from the public tape between two books:
- a taker selling YES (taker_side "no") at a YES price at or below our YES bid fills us first when we're first in the queue;
- when we're at the best bid, we're filled after the size ahead of us has traded at that price, or by any print through our price;
- NO works the same way.

**Value.** Each fill is valued at the market's result if it settled, else at the last recorded mid. Maker fees are charged where the series has them: 0.0175 × p(1 − p) × the series' multiplier, rounded up per fill. Any fee type not known to be taker-only or maker-fee is charged 0.07 × p(1 − p), which is conservative.

**Capital** is the cash a resting bid or an open position ties up. A YES and a NO in the same market net to $1, as Kalshi nets them. **Return on capital** = net ÷ the time integral of capital, per year.

## Windows and the verdict

- **Development:** from the first recorded book to the end of the 7th full UTC day after it. Methods are debugged on it, and a bug fix is allowed and recorded. Nothing in the rule (Q, the 3-tick improvement rule, the inventory cap, the 300 s limit, the fee treatment, the thresholds below) changes after looking at its results.
- **Holdout:** the next 14 UTC days. The tape is pulled and the report refused until the holdout is over.
- **Kill,** if the holdout's return on capital is below **20%/yr** or its 90% market-clustered bootstrap lower bound is at or below 0 (2,000 resamples, seed 20261011).
- **Proceed,** to a live pilot proposal for the owner, only if it's at least 20%/yr with the lower bound above 0, at least **60 markets** were simulated, and net was positive in at least half the series with 3 or more markets. The owner decides; it needs a Kalshi account and API key, which are the owner's to open.
- **Inconclusive** otherwise. Report it as such; don't extend it, don't re-tune.

**Reported, not decided on:**
- the reward, fill P&L and fees separately;
- the fraction of time that was eligible, the mean share, and the fraction of time first in the queue;
- results by series, and by tercile of ex-ante pool per quote dollar;
- how many markets were marked rather than settled.

## Approximations, named

- **Other quoters don't react to ours** (pennying, cancelling, resizing). That's optimistic, and the biggest unknown; only a live pilot measures it.
- **Timing:** we sample a book every 120 s while Kalshi scores every second, and between books our quote doesn't move. The error can go either way.
- **Fill counts:** every print at or through our price fills us when we're first, the most adverse-selection exposure. At the best bid, the size ahead of us shrinks only by trades, not by cancels, which means fewer fills.
- **Unsettled positions** are marked at the last recorded mid, so a long-dated market's outcome risk isn't realized inside the window.
- **Overlapping programs:** when two programs on a market overlap, the larger target and the smaller discount factor apply. The tick is 1¢.
- **Capital** has no extra cash buffer, and payout timing is ignored. Rewards are paid as account credits.

**What it can't show:** our real queue position, latency, the API's limits on placing orders, or the incumbents' response. Nothing is traded.
