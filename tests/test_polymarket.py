import asyncio
import json
from unittest.mock import patch

from edgescout import m1, polymarket, scan

import pytest


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch):
    monkeypatch.setattr(polymarket, "PACE_S", 0)


# Test 1: strike_of parses strike strings correctly
def test_strike_of_single_value():
    assert polymarket.strike_of("82,000") == (82000.0, None)


def test_strike_of_range():
    assert polymarket.strike_of("118,000-120,000") == (118000.0, 120000.0)


def test_strike_of_below():
    assert polymarket.strike_of("<76,000") == (None, 76000.0)


def test_strike_of_above():
    assert polymarket.strike_of(">96,000") == (96000.0, None)


def test_strike_of_downward_arrow():
    assert polymarket.strike_of("↓ 75,000") == (None, 75000.0)


def test_strike_of_garbage():
    assert polymarket.strike_of("abc") == (None, None)


# Test 2: kind_of parses event titles correctly
def test_kind_of_btc_above():
    assert polymarket.kind_of("Bitcoin above ___ on October 9?") == ("BTC", "above")


def test_kind_of_eth_price_range():
    assert polymarket.kind_of("Ethereum price on October 9?") == ("ETH", "range")


def test_kind_of_btc_up_down():
    assert polymarket.kind_of("Bitcoin Up or Down - October 8, 9PM ET") is None


def test_kind_of_solana_above():
    assert polymarket.kind_of("Solana above ___") is None


# Test 3: market_row converts fields correctly
def test_market_row_with_fees_enabled():
    e = {"slug": "event-slug", "endDate": None}
    m = {
        "slug": "market-slug",
        "groupItemTitle": "82,000",
        "endDate": "2026-10-09T12:00:00Z",
        "bestBid": "0.45",
        "bestAsk": "0.55",
        "spread": "0.10",
        "liquidityNum": "1000.0",
        "volume24hr": "5000.0",
        "feesEnabled": True,
        "feeSchedule": {"rate": "0.07", "exponent": "1.0"},
        "restricted": False,
    }
    row = polymarket.market_row(e, m, "BTC", "above")
    assert row["venue"] == "polymarket"
    assert row["currency"] == "BTC"
    assert row["kind"] == "above"
    assert row["market"] == "market-slug"
    assert row["yes_bid"] == 0.45
    assert row["yes_ask"] == 0.55
    assert row["fee_rate"] == 0.07
    assert row["close"] == 1791547200.0  # 2026-10-09T12:00:00Z
    assert row["low"] == 82000.0
    assert row["high"] is None


def test_market_row_fees_disabled():
    e = {"slug": "event-slug"}
    m = {
        "slug": "market-slug",
        "groupItemTitle": "82,000",
        "endDate": "2026-10-09T12:00:00Z",
        "bestBid": "0.45",
        "bestAsk": "0.55",
        "feesEnabled": False,
        "feeSchedule": {"rate": "0.07"},
    }
    row = polymarket.market_row(e, m, "BTC", "above")
    assert row["fee_rate"] == 0.0


# Test 4: informative filters markets by mid price
def test_informative_keeps_mid_in_range():
    # mid 0.50 is within [0.02, 0.98]
    assert polymarket.informative({"yes_bid": 0.49, "yes_ask": 0.51})


def test_informative_drops_bid_none():
    # yes_bid can be None, but yes_ask cannot
    assert not polymarket.informative({"yes_bid": None, "yes_ask": 0.001})


def test_informative_drops_mid_too_high():
    # mid 0.998 is above 0.98
    assert not polymarket.informative({"yes_bid": 0.997, "yes_ask": 0.998})


# Test 5: polymarket_open gets events and keeps informative BTC/ETH markets
def test_polymarket_open_success():
    async def run_test():
        async def fake_get(c, url, params=None, **kw):
            # Return a BTC "above" event with two markets
            return [
                {
                    "slug": "btc-above-82k",
                    "title": "Bitcoin above 82,000 on Oct 9?",
                    "endDate": "2026-10-09T12:00:00Z",
                    "markets": [
                        {
                            "slug": "m1",
                            "groupItemTitle": "82,000",
                            "bestBid": "0.49",
                            "bestAsk": "0.51",
                            "endDate": "2026-10-09T12:00:00Z",
                            "feesEnabled": True,
                            "feeSchedule": {"rate": "0.07"},
                            "spread": "0.02",
                            "liquidityNum": "1000",
                            "volume24hr": "5000",
                        },
                        {
                            "slug": "m2",
                            "groupItemTitle": "82,000",
                            "bestBid": "0.001",
                            "bestAsk": "0.002",
                            "endDate": "2026-10-09T12:00:00Z",
                            "feesEnabled": True,
                            "feeSchedule": {"rate": "0.07"},
                        },
                    ],
                },
            ]

        with patch("edgescout.polymarket._get", side_effect=fake_get):
            rows, errors = await polymarket.polymarket_open(None, 1791543600.0)
            assert len(rows) == 1  # Only informative market from BTC event
            assert rows[0]["currency"] == "BTC"
            assert rows[0]["kind"] == "above"
            assert rows[0]["low"] == 82000.0
            assert errors == {}

    asyncio.run(run_test())


def test_polymarket_open_network_error():
    async def run_test():
        async def fake_get(c, url, params=None, **kw):
            raise RuntimeError("Network error")

        with patch("edgescout.polymarket._get", side_effect=fake_get):
            rows, errors = await polymarket.polymarket_open(None, 1791543600.0)
            assert rows == []
            assert "polymarket" in errors
            assert "RuntimeError" in errors["polymarket"]

    asyncio.run(run_test())


# Test 6: _events pagination collects pages and respects offsets
def test_events_pagination():
    async def run_test():
        offsets_seen = []

        async def fake_get(c, url, params=None, **kw):
            offset = params.get("offset", 0) if params else 0
            offsets_seen.append(offset)
            if offset == 0:
                # First page: 100 events
                return [{"slug": f"ev-{i}", "title": "Bitcoin above 82000"} for i in range(100)]
            elif offset == 100:
                # Second page: 3 events (less than 100, so stop)
                return [{"slug": f"ev-{100+i}", "title": "Bitcoin above 82000"} for i in range(3)]
            return []

        with patch("edgescout.polymarket._get", side_effect=fake_get):
            evs = await polymarket._events(None)
            assert len(evs) == 103
            assert offsets_seen == [0, 100]

    asyncio.run(run_test())


# Test 7: _result parses outcome prices correctly
def test_result_yes():
    m = {"outcomePrices": '["1", "0"]'}
    assert polymarket._result(m) == "yes"


def test_result_no():
    m = {"outcomePrices": '["0", "1"]'}
    assert polymarket._result(m) == "no"


def test_result_tied():
    m = {"outcomePrices": '["0.5", "0.5"]'}
    assert polymarket._result(m) is None


def test_result_garbage():
    m = {"outcomePrices": "invalid"}
    assert polymarket._result(m) is None


def test_polymarket_settled_with_results():
    async def run_test():
        async def fake_get(c, url, params=None, **kw):
            return [
                {
                    "slug": "btc-above-82k",
                    "title": "Bitcoin above 82,000?",
                    "endDate": "2026-10-09T12:00:00Z",
                    "markets": [
                        {
                            "groupItemTitle": "82,000",
                            "outcomePrices": '["1", "0"]',
                            "description": "Settlement on Binance",
                        },
                    ],
                },
            ]

        with patch("edgescout.polymarket._get", side_effect=fake_get):
            rows, errors = await polymarket.polymarket_settled(None, 1791440000.0, 1791545000.0)
            assert len(rows) == 1
            assert rows[0]["event"] == "btc-above-82k"
            assert rows[0]["markets"] == [[82000.0, None, "yes"]]
            assert rows[0]["currency"] == "BTC"
            assert errors == {}

    asyncio.run(run_test())


# Test 8: m1.polymarket_fee calculation
def test_polymarket_fee():
    fee = m1.polymarket_fee(0.5, 0.07)
    assert fee == pytest.approx(0.0175)  # 0.07 * 0.5 * 0.5


def test_polymarket_fee_with_exponent():
    fee = m1.polymarket_fee(0.4, 0.07, 1)
    assert fee == pytest.approx(0.07 * 0.4 * 0.6)


# Test 9: m1.boxes calculates costs and values correctly
def test_boxes_profitable_case():
    s = {
        "k_ask": 0.30,
        "k_bid": 0.38,
        "k_ask_size": 50,
        "k_bid_size": 50,
        "p_bid": 0.60,
        "p_ask": 0.62,
        "p_fee_rate": 0.07,
        "p_fee_exponent": 1,
        "k_outcome": 1,
        "p_outcome": 1,
    }
    boxes = m1.boxes(s)
    # Should have at least box A (Kalshi YES + Polymarket NO)
    assert len(boxes) > 0
    # Find box with cost <= 0.99
    profitable = [b for b in boxes if b["value"] > 0]
    assert len(profitable) > 0


def test_boxes_unprofitable_case():
    s = {
        "k_ask": 0.62,
        "k_bid": 0.60,
        "k_ask_size": 50,
        "k_bid_size": 50,
        "p_bid": 0.60,
        "p_ask": 0.62,
        "p_fee_rate": 0.07,
        "p_fee_exponent": 1,
        "k_outcome": 1,
        "p_outcome": 1,
    }
    boxes = m1.boxes(s)
    # Cost should be too high
    assert all(b["value"] <= 0 for b in boxes)


def test_boxes_outcomes_disagree():
    s = {
        "k_ask": 0.30,
        "k_bid": 0.38,
        "k_ask_size": 50,
        "k_bid_size": 50,
        "p_bid": 0.60,
        "p_ask": 0.62,
        "p_fee_rate": 0.07,
        "p_fee_exponent": 1,
        "k_outcome": 1,
        "p_outcome": 0,
    }
    boxes = m1.boxes(s)
    # When outcomes disagree, value formula changes
    if boxes:
        # Box A: k_outcome + (1 - p_outcome) - cost = 1 + 1 - cost
        assert boxes[0]["value"] > 0


# Test 10: m1.cross_samples pairing logic
def test_cross_samples_pairing():
    # Build a test data dict with Kalshi and Polymarket quotes at the same times
    close = 1791547200.0
    k_q = (close - 1800, {"series": "KXBTCD", "event": "kev", "strike_type": "greater",
                          "floor": 81999.99, "cap": None, "close": close,
                          "yes_bid": 0.55, "yes_ask": 0.60, "yes_bid_size": 50, "yes_ask_size": 50})
    p_q = (close - 1800, {"kind": "above", "currency": "BTC", "event": "pev", "market": "m1",
                          "low": 82000.0, "high": None, "close": close,
                          "yes_bid": 0.55, "yes_ask": 0.60, "fee_rate": 0.07, "fee_exponent": 1})
    d = {
        "kalshi": {"KXBTCD_ticker": [k_q]},
        "polymarket": {"pm_market": [p_q]},
        "settled": {"kev": {"event": "kev", "markets": [["greater", 81999.99, None, "yes"]]}},
        "polymarket_settled": {"pev": {"event": "pev", "markets": [[82000.0, None, "yes"]]}},
    }
    samples = m1.cross_samples(d, close - 7200, close + 3600)
    assert len(samples) == 1
    assert samples[0]["kalshi"] == "KXBTCD_ticker"
    assert samples[0]["polymarket"] == "pm_market"
    assert samples[0]["k_outcome"] == 1
    assert samples[0]["p_outcome"] == 1


def test_cross_samples_no_pair_different_strike():
    close = 1791547200.0
    k_q = (close - 1800, {"series": "KXBTCD", "event": "kev", "strike_type": "greater",
                          "floor": 83999.99, "cap": None, "close": close,
                          "yes_bid": 0.55, "yes_ask": 0.60, "yes_bid_size": 50, "yes_ask_size": 50})
    p_q = (close - 1800, {"kind": "above", "currency": "BTC", "event": "pev", "market": "m1",
                          "low": 82000.0, "high": None, "close": close,
                          "yes_bid": 0.55, "yes_ask": 0.60, "fee_rate": 0.07, "fee_exponent": 1})
    d = {
        "kalshi": {"KXBTCD_ticker": [k_q]},
        "polymarket": {"pm_market": [p_q]},
        "settled": {"kev": {"event": "kev", "markets": [["greater", 83999.99, None, "yes"]]}},
        "polymarket_settled": {"pev": {"event": "pev", "markets": [[82000.0, None, "yes"]]}},
    }
    samples = m1.cross_samples(d, close - 7200, close + 3600)
    # Different strikes should not pair
    assert len(samples) == 0


# Test 10b: the pairing is by the same MOMENT, robustly, and a pair needs both venues' quotes at the lead
def _cross_fixture(k_close, p_close, k_quote_times, p_quote_times, floor=79999.99, strike=80000.0):
    """One Kalshi 'above' market and one Polymarket 'above' market, each with a quote at the given times."""
    def k(t):
        return (t, {"series": "KXBTCD", "ticker": "KX-T80000", "event": "kev", "strike_type": "greater",
                    "floor": floor, "cap": None, "close": k_close, "yes_bid": 0.45, "yes_ask": 0.48,
                    "yes_bid_size": 50, "yes_ask_size": 50})

    def p(t):
        return (t, {"kind": "above", "currency": "BTC", "event": "pev", "market": "pm80", "low": strike, "high": None,
                    "close": p_close, "yes_bid": 0.46, "yes_ask": 0.49, "fee_rate": 0.07, "fee_exponent": 1})
    return {"kalshi": {"KX-T80000": [k(t) for t in k_quote_times]},
            "polymarket": {"pm80": [p(t) for t in p_quote_times]},
            "settled": {"kev": {"event": "kev", "markets": [["greater", floor, None, "yes"]]}},
            "polymarket_settled": {"pev": {"event": "pev", "markets": [[strike, None, "yes"]]}}}


CLOSE = 1791561600.0                                               # 2026-10-09 16:00:00 UTC = 12:00 ET
HOUR = [CLOSE - 3600 + 60 * i for i in range(60)]                    # a quote every minute through the last hour


def test_cross_samples_pair_a_market_at_every_lead_when_both_venues_quote_through_the_hour():
    ss = m1.cross_samples(_cross_fixture(CLOSE, CLOSE, HOUR, HOUR), CLOSE - 86400, CLOSE + 86400)
    assert sorted(s["lead_s"] for s in ss) == [300, 900, 1800, 2700, 3600]     # 6 h and 3 h: Kalshi has no quote yet
    assert {s["kalshi"] for s in ss} == {"KX-T80000"} and {s["polymarket"] for s in ss} == {"pm80"}


@pytest.mark.parametrize("offset", [1, 3, 30, -45, 60])
def test_cross_samples_pair_closes_the_venues_state_a_few_seconds_apart(offset):
    """The same moment stated differently (16:00:00 on one venue, 16:00:03 on the other) is the same event: the match
    must not depend on the two timestamps being bit-identical."""
    ss = m1.cross_samples(_cross_fixture(CLOSE + offset, CLOSE, HOUR, HOUR), CLOSE - 86400, CLOSE + 86400)
    assert len(ss) == 5 and all(s["t"] == CLOSE - s["lead_s"] for s in ss)     # sampled at the Polymarket close's leads


@pytest.mark.parametrize("offset", [61, 300, 3600])
def test_cross_samples_do_not_pair_different_closes(offset):
    """Kalshi's neighbouring hourly event (an hour later) or minutes off is a different event, not the same moment."""
    d = _cross_fixture(CLOSE + offset, CLOSE, HOUR, HOUR)
    assert m1.cross_samples(d, CLOSE - 86400, CLOSE + 86400) == []


def test_cross_samples_need_the_floor_one_cent_below_the_polymarket_strike():
    for floor in (79899.99, 80099.99, 80000.99):
        assert m1.cross_samples(_cross_fixture(CLOSE, CLOSE, HOUR, HOUR, floor=floor), CLOSE - 86400, CLOSE + 86400) == []
    assert len(m1.cross_samples(_cross_fixture(CLOSE, CLOSE, HOUR, HOUR, floor=79999.99), CLOSE - 86400, CLOSE + 86400)) == 5


def test_cross_samples_lose_the_leads_where_either_venue_has_no_quote_in_the_two_minutes_before():
    """Each venue records a market only while its mid is within 2-98 cents, so a market whose price runs away stops
    being quoted: Polymarket's last quote 25 minutes before the close leaves the 45- and 30-minute leads only."""
    pm_quotes = [t for t in HOUR if t <= CLOSE - 25 * 60]
    ss = m1.cross_samples(_cross_fixture(CLOSE, CLOSE, HOUR, pm_quotes), CLOSE - 86400, CLOSE + 86400)
    assert sorted(s["lead_s"] for s in ss) == [1800, 2700, 3600]
    k_quotes = [t for t in HOUR if t >= CLOSE - 20 * 60]                  # Kalshi quotes only the last 20 minutes
    ss = m1.cross_samples(_cross_fixture(CLOSE, CLOSE, k_quotes, HOUR), CLOSE - 86400, CLOSE + 86400)
    assert sorted(s["lead_s"] for s in ss) == [300, 900]


def test_kalshi_counterpart_takes_the_nearest_close_then_the_nearest_floor():
    kal_by = {"KXBTCD": [(CLOSE + 40, 79999.99, "far"), (CLOSE + 2, 79999.99, "near"), (CLOSE + 2, 79999.97, "off"),
                         (CLOSE + 3600, 79999.99, "next hour"), (None, 79999.99, "no close"), (CLOSE, None, "no floor")],
              "KXETHD": [(CLOSE, 79999.99, "other coin")]}
    assert m1.kalshi_counterpart(kal_by, "KXBTCD", CLOSE, 80000.0) == "near"
    assert m1.kalshi_counterpart(kal_by, "KXBTCD", CLOSE + 7200, 80000.0) is None
    assert m1.kalshi_counterpart(kal_by, "KXBTCD", CLOSE, 81000.0) is None
    assert m1.kalshi_counterpart(kal_by, None, CLOSE, 80000.0) is None


# Test 11: m1.evaluate_cross generates statistics
def test_evaluate_cross():
    close = 1791547200.0
    k_q = (close - 1800, {"series": "KXBTCD", "event": "kev", "strike_type": "greater",
                          "floor": 81999.99, "cap": None, "close": close,
                          "yes_bid": 0.55, "yes_ask": 0.60, "yes_bid_size": 50, "yes_ask_size": 50})
    p_q = (close - 1800, {"kind": "above", "currency": "BTC", "event": "pev", "market": "m1",
                          "low": 82000.0, "high": None, "close": close,
                          "yes_bid": 0.55, "yes_ask": 0.60, "fee_rate": 0.07, "fee_exponent": 1})
    d = {
        "kalshi": {"KXBTCD_ticker": [k_q]},
        "polymarket": {"pm_market": [p_q]},
        "settled": {"kev": {"event": "kev", "markets": [["greater", 81999.99, None, "yes"]]}},
        "polymarket_settled": {"pev": {"event": "pev", "markets": [[82000.0, None, "yes"]]}},
    }
    samples = m1.cross_samples(d, close - 7200, close + 3600)
    result = m1.evaluate_cross(samples)
    assert "samples" in result
    assert "pairs" in result
    assert "outcomes_disagree" in result
    assert "boxes" in result
    assert result["samples"] == 1


# Test 12: m1.window with polymarket source
def test_window_polymarket_source():
    close = 1791547200.0
    p_q = (close, {"kind": "above", "currency": "BTC", "low": 82000.0})
    d = {
        "polymarket": {"pm_market": [p_q]},
    }
    start, end = m1.window(d, False, source="polymarket")
    assert start == close
    # end should be 7 days after the start of the next day
    assert end > start


# Test 13: scan wiring for polymarket
def test_scan_loop_writes_polymarket_kinds(tmp_path, monkeypatch):
    async def run_test():
        monkeypatch.setattr("edgescout.scan.DATA", tmp_path)

        async def fake_spot_quotes(c):
            return [{"venue": "coinbase", "symbol": "BTC", "bid": 80000.0, "ask": 80100.0}], {}

        async def fake_perp_funding(c):
            return [], {}

        async def fake_dated_futures(c, mid):
            return [], {}

        class FakeClientMgr:
            async def __aenter__(self):
                return None

            async def __aexit__(self, *args):
                pass

        monkeypatch.setattr("edgescout.scan.venues.spot_quotes", fake_spot_quotes)
        monkeypatch.setattr("edgescout.scan.venues.perp_funding", fake_perp_funding)
        monkeypatch.setattr("edgescout.scan.venues.dated_futures", fake_dated_futures)
        monkeypatch.setattr("edgescout.scan.venues.client", FakeClientMgr)

        async def fake_kalshi_open(c):
            return [], {}

        async def fake_kalshi_settled(c, since):
            return [], {}

        async def fake_deribit_options(c, t):
            return [], {}

        async def fake_polymarket_open(c, t):
            return [{"event": "btc-above-82k", "market": "m1", "currency": "BTC"}], {}

        async def fake_polymarket_settled(c, since, now):
            # Return the same event twice to test dedup
            return [{"event": "settled-ev1", "currency": "BTC"}], {}

        monkeypatch.setattr("edgescout.scan.kalshi.kalshi_open", fake_kalshi_open)
        monkeypatch.setattr("edgescout.scan.kalshi.kalshi_settled", fake_kalshi_settled)
        monkeypatch.setattr("edgescout.scan.kalshi.deribit_options", fake_deribit_options)
        monkeypatch.setattr("edgescout.scan.polymarket.polymarket_open", fake_polymarket_open)
        monkeypatch.setattr("edgescout.scan.polymarket.polymarket_settled", fake_polymarket_settled)

        await scan.scan(
            minutes=0.001,
            spot_every=0.0001,
            deriv_every=0.0001,
            kalshi_every=0.0001,
            settled_every=0.0001,
            quiet=True,
        )

        scan_files = list(tmp_path.glob("scan-*.jsonl"))
        assert len(scan_files) > 0

        records = []
        for sf in scan_files:
            for line in sf.read_text().strip().split("\n"):
                if line:
                    records.append(json.loads(line))

        kinds = {r["kind"] for r in records}
        assert "polymarket" in kinds
        assert "polymarket_settled" in kinds

        # Check dedup: polymarket_settled should appear with only new events
        settled_records = [r for r in records if r["kind"] == "polymarket_settled"]
        all_events = []
        for sr in settled_records:
            all_events.extend([e["event"] for e in sr["rows"]])
        # The same event returned twice should be written only once
        assert all_events.count("settled-ev1") == 1

    asyncio.run(run_test())
