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
