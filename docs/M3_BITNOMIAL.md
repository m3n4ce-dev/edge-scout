# M3: Bitnomial's fixed funding against Coinbase Derivatives' floating funding (registered 2026-10-09 23:40 UTC, before the forward window)

**The lead** (a research pass on 2026-10-09, re-checked by the builder from Bitnomial's own API):
- **Bitnomial** sets perp funding by rulebook: the average premium plus a clamp of (interest − premium) to ±0.05%, with the interest term fixed at 0.01% per 8 h. In 8,976 recorded intervals from 2026-05-17 to 2026-10-09, 99.8% paid exactly 0.01%/8h, which is 10.95%/yr from longs to shorts.
- **Coinbase Derivatives' (CDE)** perp funding floats hourly with its premium. Over 10-04..10-09 BTC averaged ~3.8%/yr and ETH ~2.6%/yr.

**The trade** (not placed; paper only):
- **The position:** short the Bitnomial perp and long the same notional of the CDE perp, so the price exposure cancels.
- **What it collects:** Bitnomial's fixed funding, minus CDE's floating funding.
- **Access:** both venues are CFTC-regulated and open to US persons. It would need accounts at both, each margined separately.

**What's recorded** (the 24/7 scan, public data):
- CDE funding every 60 s (since 2026-10-04);
- every Bitnomial perpetual's funding intervals every 30 minutes (`edgescout/bitnomial.py`; 34 products, 17 coins).

**The paper position** (`edgescout/m3.py`, `python -m edgescout m3`):
- **Size:** $1,000 notional in each of BTC, ETH, SOL and XRP (the coins listed on both venues), held for the whole window.
- **Bitnomial leg:** the short receives each 8-hour interval's recorded rate × notional.
- **CDE leg:** the long pays each hour's rate × notional. That rate is the last recorded CDE rate before the hour ends: the rate the hour settles at.
- **Costs:** a round trip of **25 bp** of notional per coin, charged once (the research put fees plus spreads at 15–25 bp). Sensitivity at 40 bp.
- **Not modelled, and named:**
  - the basis between the two perps' prices: delta-neutral, but the two marks can drift apart;
  - margin interest;
  - Coinbase's retail fee on these contracts, which is unverified until the owner checks it in the account.

**The window:** 14 UTC days from the first recorded Bitnomial row.

**Verdict at its end:**
- **Kill** if any of these holds:
  - the paper's annualized net return, on notional after costs, is below **4%/yr**;
  - Bitnomial funding is off its floor in more than 5% of intervals for these coins;
  - CDE funding exceeds Bitnomial's on more than 25% of days for a coin. That coin is dropped, and the rest are judged alone.
- **Proceed** (to a live pilot proposal for the owner) only if net is ≥ 4%/yr on notional, the coin-days are positive on at least 75% of days, **and** the owner has confirmed Coinbase's real fee.

**What this can't be.** It's cash-plus, not alpha: at $5,000 it's roughly $200–400 a year above cash, if it holds. Bitnomial's books are small, about $0.7M BTC open interest, which caps the size.

## Note (2026-10-11 08:18 UTC, day 2 of the window): the registered kill rule tests a 14-day hold

**The verdict is unchanged.** The rule above stays as registered and is applied at the window's end.

The rule charges the 25 bp round trip once, inside the 14-day window, and then annualizes the window's net. A round trip spread over 14 days costs 0.25% × 365 / 14 ≈ **6.5%/yr**, and Bitnomial's floor pays 10.95%/yr. So the rule passes only if Coinbase's funding stays below about 0.5%/yr for the whole window: the rule tests a two-week trade. The position this study is about would be held for months, and "$200–400 a year above cash" above assumed that. This is arithmetic, independent of the data, but it was noticed on the first interim read (day 2), so it's disclosed rather than changed.

**What's added, reported and not decided on** (`m3.held`, in `reported_not_decided`):
- the window's gross funding gap per year (received minus paid);
- the same with the round trip spread over a one-year hold (−0.25%/yr at 25 bp, −0.40%/yr at 40 bp);
- the variant hedged with spot instead of the Coinbase perp: Bitnomial's funding is received and nothing is paid on the long leg, which ties up the full notional in cash.

**Edge effect.** A short held at a funding timestamp receives that whole interval's payment. The window started 19 minutes before one, so early reads overstate the received leg; for example, 5 intervals in the first 32.6 hours where 4.1 would be expected. That fades over 14 days.
