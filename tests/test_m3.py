"""M3's paper position on synthetic funding: Bitnomial's fixed rate received, Coinbase's hourly rate paid, costs once."""
import pytest

from edgescout import m3

T0 = 1_800_000_000.0 - (1_800_000_000.0 % 86400)


def test_fixed_minus_floating_accrues_as_registered():
    bn = {c: {T0 + 8 * 3600 * k: 0.0001 for k in range(1, 4)} for c in m3.COINS}          # one day: 3 intervals
    cde = {c: [(T0 + 60, 0.0000040)] for c in m3.COINS}                                   # ~3.5%/yr hourly
    out = m3.paper(cde, bn, T0, T0 + 86400)
    btc = out["BTC"]
    assert btc["bitnomial_received"] == pytest.approx(3 * 0.0001 * 1000)
    assert btc["cde_paid"] == pytest.approx(24 * 0.0000040 * 1000)
    assert btc["net"] == pytest.approx(0.3 - 0.096 - 2.5)                                  # 25 bp round trip once
    assert btc["days_positive"] == "1/1" and btc["cde_above_bitnomial_days"] == 0


def test_a_negative_cde_rate_is_received_not_paid():
    bn = {c: {} for c in m3.COINS}
    cde = {c: [(T0, -0.00001)] for c in m3.COINS}
    out = m3.paper(cde, bn, T0, T0 + 3600 * 2, cost_bp=0)
    assert out["ETH"]["net"] == pytest.approx(2 * 0.00001 * 1000)


def test_bitnomial_intervals_outside_the_window_dont_count():
    bn = {c: {T0 - 1: 0.0001, T0 + 8 * 3600: 0.0001, T0 + 2 * 86400: 0.0001} for c in m3.COINS}
    out = m3.paper({}, bn, T0, T0 + 86400, cost_bp=0)
    assert out["SOL"]["bitnomial_received"] == pytest.approx(0.1)


def test_held_spreads_the_round_trip_over_a_year_and_reports_the_spot_hedge():
    bn = {c: {T0 + 8 * 3600 * k: 0.0001 for k in range(1, 4)} for c in m3.COINS}          # 10.95%/yr
    cde = {c: [(T0 + 60, 0.0000040)] for c in m3.COINS}                                   # 24 x 0.0004% a day
    out = m3.held(cde, bn, T0, T0 + 86400)
    yr = m3.YEAR / 86400
    gap = 100 * (0.3 - 0.096) / 1000 * yr
    assert out["BTC"]["gross_gap_pct_per_year"] == pytest.approx(gap, abs=1e-3)
    assert out["BTC"]["held_one_year_net_pct"]["25bp"] == pytest.approx(gap - 0.25, abs=1e-3)
    assert out["BTC"]["held_one_year_net_pct"]["40bp"] == pytest.approx(gap - 0.40, abs=1e-3)
    assert out["BTC"]["spot_hedge_received_pct_per_year"] == pytest.approx(100 * 0.3 / 1000 * yr, abs=1e-3)
    assert out["portfolio_held_one_year_net_pct_at_25bp"] == pytest.approx(gap - 0.25, abs=1e-3)
