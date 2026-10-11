"""M1's analysis on synthetic data: the implied digital against closed forms, time interpolation, the fee, the rule."""
import math

import pytest

from edgescout import m1

T0 = 1_800_000_000.0


def flat(expiry, forward, iv, strikes):
    return [{"kind": "option", "expiry": expiry, "strike": k, "cp": "C", "mark_iv": iv * 100, "forward": forward}
            for k in strikes]


def test_a_flat_smile_gives_the_lognormal_digital():
    f, iv, tau = 80000.0, 0.5, 1 / 365.25
    sm = m1.smiles(flat(T0 + 86400, f, iv, [70000, 80000, 90000]))
    for k in (76000.0, 80000.0, 84000.0):
        w = iv * iv * tau
        want = m1.ncdf((math.log(f / k) - w / 2) / math.sqrt(w))
        assert m1.implied_above(sm, k, T0, T0 + 86400) == pytest.approx(want, abs=2e-3)


def test_total_variance_is_interpolated_between_bracketing_expiries():
    f = 80000.0
    rows = flat(T0 + 3600 * 6, f, 0.4, [70000, 90000]) + flat(T0 + 3600 * 30, f, 0.6, [70000, 90000])
    sm = m1.smiles(rows)
    target = T0 + 3600 * 18                                          # halfway in time
    wa, wb = 0.4 ** 2 * 6 / (365.25 * 24), 0.6 ** 2 * 30 / (365.25 * 24)
    w = (wa + wb) / 2
    want = m1.ncdf((math.log(f / 82000) - w / 2) / math.sqrt(w))
    assert m1.implied_above(sm, 82000.0, T0, target) == pytest.approx(want, abs=2e-3)


def test_a_skewed_smile_gives_n_d2_minus_vega_times_the_skew():
    """digital = N(d2) - vega * dsigma/dK: a downside skew (sigma falling with K) RAISES P(above) at the money."""
    f, tau = 80000.0, 1 / 365.25
    rows = [{"kind": "option", "expiry": T0 + 86400, "strike": k, "cp": "P", "mark_iv": v, "forward": f}
            for k, v in ((70000, 70.0), (80000, 50.0), (90000, 40.0))]
    sm = m1.smiles(rows)
    p = m1.implied_above(sm, 80000.0, T0, T0 + 86400)
    s0 = 0.5 * math.sqrt(tau)
    d1 = s0 / 2
    vega = f * math.exp(-d1 * d1 / 2) / math.sqrt(2 * math.pi) * math.sqrt(tau)
    h = 0.005 * f
    slope = (sm[0].vol(f + h) - sm[0].vol(f - h)) / (2 * h)
    want = m1.ncdf(d1 - s0) - vega * slope
    assert p == pytest.approx(want, abs=2e-3) and p > m1.ncdf(d1 - s0)


def _fixed_step_implied_above(sm, strike, t, target):
    """The first version of implied_above (a fixed +-0.5% of K), kept here only to show the bug it had."""
    h = 0.005 * strike
    w1, f1 = m1._w_f(sm, strike - h, t, target)
    w2, f2 = m1._w_f(sm, strike + h, t, target)
    f = (f1 + f2) / 2
    p = -(m1.black76_call(f, strike + h, w2) - m1.black76_call(f, strike - h, w1)) / (2 * h)
    return min(1.0, max(0.0, p))


def _sd_strikes(f, w, zs):
    return [(z, f * math.exp(z * math.sqrt(w))) for z in zs]


def test_a_flat_smile_at_a_short_horizon_gives_the_lognormal_digital_across_the_distribution():
    """5 minutes ahead at 50% vol the price's sd is ~0.15% of K: the step must follow it, from -3 sd to +3 sd."""
    f, iv, lead = 80000.0, 0.5, 300.0
    sm = m1.smiles(flat(T0 + 86400, f, iv, [70000, 80000, 90000]))     # the nearest expiry is a day away: scaled to 5 min
    w = iv * iv * lead / m1.YEAR
    for z, k in _sd_strikes(f, w, [-3, -2.5, -2, -1.5, -1, -0.5, 0, 0.5, 1, 1.5, 2, 2.5, 3]):
        want = m1.ncdf((math.log(f / k) - w / 2) / math.sqrt(w))
        assert m1.implied_above(sm, k, T0, T0 + lead) == pytest.approx(want, abs=2e-3), z


def test_the_digital_is_right_across_horizons_from_five_minutes_to_a_day():
    f, iv = 80000.0, 0.6
    sm = m1.smiles(flat(T0 + 3 * 86400, f, iv, [70000, 80000, 90000]))
    for lead in (300.0, 900.0, 3600.0, 6 * 3600.0, 86400.0):
        w = iv * iv * lead / m1.YEAR
        for z, k in _sd_strikes(f, w, [-2, -1, 0, 1, 2]):
            want = m1.ncdf((math.log(f / k) - w / 2) / math.sqrt(w))
            assert m1.implied_above(sm, k, T0, T0 + lead) == pytest.approx(want, abs=2e-3), (lead, z)


def test_a_skewed_smile_at_a_short_horizon_gives_n_d2_minus_vega_times_the_skew():
    """sigma(K) = a - b ln(K/F) (a straight line in log-moneyness, so dsigma/dK = -b/K exactly): at the 5-minute lead
    the digital is N(d2) - vega * dsigma/dK. A sharp skew (b = 30: vol falls 0.3 per 1% of strike) makes that shift
    ~2 points; with a realistic skew it is ~0.03 points at this horizon (vega ~ sqrt(T)), invisible either way."""
    f, a, b, lead = 80000.0, 0.5, 30.0, 300.0
    ks = [f * math.exp(x / 5000) for x in range(-60, 61)]
    rows = [{"kind": "option", "expiry": T0 + 86400, "strike": k, "cp": "P",
             "mark_iv": 100 * (a - b * math.log(k / f)), "forward": f} for k in ks]
    sm = m1.smiles(rows)
    tau = lead / m1.YEAR
    for z in (-1.0, 0.0, 1.0):
        k = f * math.exp(z * a * math.sqrt(tau))
        sigma = a - b * math.log(k / f)
        s = sigma * math.sqrt(tau)
        d1 = (math.log(f / k) + s * s / 2) / s
        vega = f * math.exp(-d1 * d1 / 2) / math.sqrt(2 * math.pi) * math.sqrt(tau)
        want = m1.ncdf(d1 - s) - vega * (-b / k)
        got = m1.implied_above(sm, k, T0, T0 + lead)
        assert got == pytest.approx(want, abs=3e-3), z
        assert abs(want - m1.ncdf(d1 - s)) > 0.01                       # the skew term is not negligible here


def test_the_old_fixed_step_was_wrong_at_the_short_leads_and_the_new_one_is_not():
    """The regression: +-0.5% of K averages the digital over ~3 sd each way at 5 minutes and pulls it toward 1/2."""
    f, iv, lead = 80000.0, 0.5, 300.0
    sm = m1.smiles(flat(T0 + 86400, f, iv, [70000, 80000, 90000]))
    w = iv * iv * lead / m1.YEAR
    worst_old = 0.0
    for z, k in _sd_strikes(f, w, [-1, -0.5, 0.5, 1]):
        want = m1.ncdf((math.log(f / k) - w / 2) / math.sqrt(w))
        old = _fixed_step_implied_above(sm, k, T0, T0 + lead)
        assert abs(old - want) > 0.05, z                                # near the money (within 1 sd): off by 5+ points
        assert abs(m1.implied_above(sm, k, T0, T0 + lead) - want) < 2e-3, z
        worst_old = max(worst_old, abs(old - want))
    assert worst_old > 0.15                                             # at +-1 sd: 0.84 became 0.65, 0.16 became 0.35
    # at a one-day horizon the old step was fine (sd 2.6% of K), and the new one agrees with it
    w = iv * iv * 86400 / m1.YEAR
    for z, k in _sd_strikes(f, w, [-1, 0, 1]):
        assert (m1.implied_above(sm, k, T0, T0 + 86400)
                == pytest.approx(_fixed_step_implied_above(sm, k, T0, T0 + 86400), abs=2e-3))


def test_the_step_is_bounded_so_a_wide_or_degenerate_distribution_still_works():
    f = 80000.0
    sm = m1.smiles(flat(T0 + 40 * 86400, f, 2.0, [70000, 90000]))       # absurdly wide: the step caps at 0.5% of K
    assert 0.0 <= m1.implied_above(sm, 80000.0, T0, T0 + 30 * 86400) <= 1.0
    sm = m1.smiles(flat(T0 + 86400, f, 0.0001, [70000, 90000]))         # nearly no variance: the step floors at 1e-5 K
    p = m1.implied_above(sm, 80000.0, T0, T0 + 60)
    assert p is not None and 0.0 <= p <= 1.0


def test_the_fee_matches_kalshis_schedule():
    assert m1.kalshi_fee(0.5) == 0.02                                 # 0.07 * 0.25 = 1.75 cents -> 2
    assert m1.kalshi_fee(0.1) == 0.01                                 # 0.63 cents -> 1
    assert m1.kalshi_fee(0.03) == 0.01


def test_the_rule_takes_the_side_the_options_favour_net_of_fee():
    s = {"p_imp": 0.60, "yes_ask": 0.52, "yes_bid": 0.50, "yes_ask_size": 50, "yes_bid_size": 50, "outcome": 1}
    t = m1.trade(s)
    assert t["side"] == "yes" and t["value"] == pytest.approx(1 - 0.52 - 0.02)
    s = {**s, "p_imp": 0.40, "outcome": 0}
    t = m1.trade(s)
    assert t["side"] == "no" and t["price"] == pytest.approx(0.50) and t["value"] == pytest.approx(1 - 0.50 - 0.02)
    assert m1.trade({**s, "p_imp": 0.52}) is None                     # inside fee + theta: no trade
    assert m1.trade({**s, "p_imp": 0.40, "yes_bid_size": 3}) is None  # too little shown


def test_samples_join_quotes_options_and_outcomes_at_each_lead():
    close = T0 + 7200
    k = {"series": "KXBTCD", "ticker": "X-T80000", "event": "X", "strike_type": "greater", "floor": 80000.0,
         "cap": None, "close": close, "yes_bid": 0.45, "yes_ask": 0.47, "yes_bid_size": 20, "yes_ask_size": 20}
    d = {"kalshi": {"X-T80000": [(close - 3700, k), (close - 1900, k), (close - 400, k)]},
         "options": {"BTC": [(close - 3700, flat(T0 + 86400, 80000.0, 0.5, [70000, 90000]))]},
         "settled": {"X": {"event": "X", "markets": [["greater", 80000.0, None, "yes"]]}}}
    ss = m1.samples(d, T0, T0 + 86400)
    leads = sorted(s["lead_s"] for s in ss)
    assert leads == [300, 1800, 3600] and all(s["outcome"] == 1 for s in ss)
    assert [s["p_imp"] is not None for s in sorted(ss, key=lambda s: -s["lead_s"])] == [True, False, False]   # stale


def test_the_holdout_is_refused_until_complete():
    d = {"kalshi": {"a": [(T0, {})]}, "options": {}, "settled": {}}
    s, e = m1.window(d, False)
    assert s == T0 and e == (T0 // 86400 + 1 + m1.DEV_DAYS) * 86400
    with pytest.raises(ValueError):
        m1.window(d, True, now=e + 10 * 86400)
    hs, he = m1.window(d, True, now=e + 16 * 86400)
    assert hs == e and he == e + m1.HOLDOUT_DAYS * 86400


def test_one_sided_books_have_no_mid():
    """Kalshi's empty side is a placeholder ($1.00 ask / $0.00 bid, no size): calibration and the implied-minus-mid
    gap use two-sided books only; the rule (which needs 10 contracts shown on its side) is unaffected."""
    base = {"event": "E", "outcome": 1, "p_imp": 0.6, "yes_bid_size": 50.0, "yes_ask_size": 50.0}
    two = dict(base, yes_bid=0.40, yes_ask=0.44)
    no_ask = dict(base, yes_bid=0.50, yes_ask=1.00, yes_ask_size=0.0)
    no_bid = dict(base, yes_bid=0.0, yes_ask=0.30, yes_bid_size=0.0)
    assert m1.two_sided(two) and not m1.two_sided(no_ask) and not m1.two_sided(no_bid)
    out = m1.evaluate([two, no_ask, no_bid])
    assert out["samples"] == 3 and out["two_sided"] == 1
    cal = out["calibration_outcome_minus_mid"]
    assert cal["0.30-0.70"]["samples"] == 1 and cal["0.70-0.90"]["samples"] == 0 and cal["0.10-0.30"]["samples"] == 0
    assert out["implied_minus_mid"]["mean"] == pytest.approx(0.6 - 0.42)
