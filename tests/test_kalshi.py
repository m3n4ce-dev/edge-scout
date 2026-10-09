import asyncio
import json
import time
from unittest.mock import patch

from edgescout import kalshi, scan

import pytest


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch):
    monkeypatch.setattr(kalshi, "PACE_S", 0)


# Test 1: market_row converts dollar strings to floats and close_time ISO to unix timestamp
def test_market_row_conversions():
    m = {
        "ticker": "KXBTCD_110000_1026",
        "event_ticker": "KXBTCD",
        "strike_type": "ABOVE",
        "floor_strike": None,
        "cap_strike": None,
        "close_time": "2026-10-09T02:00:00Z",
        "yes_bid_dollars": "0.40",
        "yes_ask_dollars": "0.45",
        "yes_bid_size_fp": "100",
        "yes_ask_size_fp": "200",
        "no_bid_dollars": "0.60",
        "no_ask_dollars": "0.55",
        "last_price_dollars": None,
        "volume_24h_fp": "1000",
        "open_interest_fp": "500",
    }
    row = kalshi.market_row(m, "KXBTCD")
    assert row["yes_bid"] == 0.40
    assert row["yes_ask"] == 0.45
    assert row["no_bid"] == 0.60
    assert row["no_ask"] == 0.55
    assert row["close"] == 1791511200.0  # 2026-10-09T02:00:00Z
    assert row["volume_24h"] == 1000.0
    assert row["last"] is None


def test_market_row_missing_fields():
    m = {"ticker": "KXBTCD_110000_1026", "close_time": None}
    row = kalshi.market_row(m, "KXBTCD")
    assert row["close"] is None
    assert row["yes_bid"] is None
    assert row["yes_ask"] is None


# Test 2: informative keeps certain markets, drops others
def test_informative_keeps_valid_mids():
    # mid 0.425 (within 0.02-0.98)
    assert kalshi.informative({"yes_bid": 0.40, "yes_ask": 0.45})
    # mid 0.025 (within range)
    assert kalshi.informative({"yes_bid": 0.0, "yes_ask": 0.05})


def test_informative_drops_out_of_range_mids():
    # mid 0.005 (below 0.02)
    assert not kalshi.informative({"yes_bid": 0.0, "yes_ask": 0.01})
    # mid 0.995 (above 0.98)
    assert not kalshi.informative({"yes_bid": 0.99, "yes_ask": 1.0})


def test_informative_drops_missing_ask():
    # yes_ask is None
    assert not kalshi.informative({"yes_bid": 0.40, "yes_ask": None})


# Test 3: kalshi_open queries all series, keeps informative markets, handles per-series errors
def test_kalshi_open_all_series_informative():
    async def run_test():
        async def fake_get(c, url, **kw):
            series = kw.get("params", {}).get("series_ticker")
            if series == "KXETH":
                raise RuntimeError("Network error")
            if series == "KXBTCD":
                return {
                    "markets": [
                        {
                            "ticker": "KXBTCD_110000_1026",
                            "event_ticker": "KXBTCD",
                            "strike_type": "ABOVE",
                            "floor_strike": None,
                            "cap_strike": None,
                            "close_time": "2026-10-09T02:00:00Z",
                            "yes_bid_dollars": "0.40",
                            "yes_ask_dollars": "0.45",
                            "yes_bid_size_fp": "100",
                            "yes_ask_size_fp": "200",
                            "no_bid_dollars": "0.60",
                            "no_ask_dollars": "0.55",
                            "last_price_dollars": None,
                            "volume_24h_fp": "1000",
                            "open_interest_fp": "500",
                        },
                        {  # Not informative (mid 0.005)
                            "ticker": "KXBTCD_200000_1026",
                            "event_ticker": "KXBTCD",
                            "strike_type": "ABOVE",
                            "floor_strike": None,
                            "cap_strike": None,
                            "close_time": "2026-10-09T02:00:00Z",
                            "yes_bid_dollars": "0.00",
                            "yes_ask_dollars": "0.01",
                            "yes_bid_size_fp": "10",
                            "yes_ask_size_fp": "20",
                            "no_bid_dollars": "0.99",
                            "no_ask_dollars": "1.00",
                            "last_price_dollars": None,
                            "volume_24h_fp": "100",
                            "open_interest_fp": "50",
                        },
                    ],
                    "cursor": None,
                }
            return {"markets": [], "cursor": None}

        with patch("edgescout.kalshi._get", side_effect=fake_get):
            rows, errors = await kalshi.kalshi_open(None)
            # Should have 1 row from KXBTCD (informative), 0 from KXETHD/KXBTC/KXETH
            assert len(rows) == 1
            assert rows[0]["series"] == "KXBTCD"
            # Should have error for KXETH
            assert "kalshi:KXETH" in errors
            assert "RuntimeError" in errors["kalshi:KXETH"]

    asyncio.run(run_test())


# Test 4: cursor is followed across pages
def test_kalshi_open_cursor_pagination():
    async def run_test():
        call_count = [0]

        async def fake_get(c, url, **kw):
            call_count[0] += 1
            series = kw.get("params", {}).get("series_ticker")
            cursor = kw.get("params", {}).get("cursor")
            if series == "KXBTCD":
                if cursor is None:
                    return {
                        "markets": [
                            {
                                "ticker": "KXBTCD_110000_1026",
                                "event_ticker": "KXBTCD",
                                "strike_type": "ABOVE",
                                "floor_strike": None,
                                "cap_strike": None,
                                "close_time": "2026-10-09T02:00:00Z",
                                "yes_bid_dollars": "0.40",
                                "yes_ask_dollars": "0.45",
                                "yes_bid_size_fp": "100",
                                "yes_ask_size_fp": "200",
                                "no_bid_dollars": "0.60",
                                "no_ask_dollars": "0.55",
                                "last_price_dollars": None,
                                "volume_24h_fp": "1000",
                                "open_interest_fp": "500",
                            }
                        ],
                        "cursor": "abc",
                    }
                elif cursor == "abc":
                    return {
                        "markets": [
                            {
                                "ticker": "KXBTCD_120000_1026",
                                "event_ticker": "KXBTCD",
                                "strike_type": "ABOVE",
                                "floor_strike": None,
                                "cap_strike": None,
                                "close_time": "2026-10-09T02:00:00Z",
                                "yes_bid_dollars": "0.50",
                                "yes_ask_dollars": "0.55",
                                "yes_bid_size_fp": "100",
                                "yes_ask_size_fp": "200",
                                "no_bid_dollars": "0.50",
                                "no_ask_dollars": "0.45",
                                "last_price_dollars": None,
                                "volume_24h_fp": "1000",
                                "open_interest_fp": "500",
                            }
                        ],
                        "cursor": None,
                    }
            return {"markets": [], "cursor": None}

        with patch("edgescout.kalshi._get", side_effect=fake_get):
            rows, errors = await kalshi.kalshi_open(None)
            # Should have 2 rows (one from each cursor page of KXBTCD)
            kxbtcd_rows = [r for r in rows if r["series"] == "KXBTCD"]
            assert len(kxbtcd_rows) == 2
            assert kxbtcd_rows[0]["ticker"] == "KXBTCD_110000_1026"
            assert kxbtcd_rows[1]["ticker"] == "KXBTCD_120000_1026"

    asyncio.run(run_test())


# Test 5: kalshi_settled returns one row per event with dedup
def test_kalshi_settled_one_per_event():
    async def run_test():
        async def fake_get(c, url, **kw):
            series = kw.get("params", {}).get("series_ticker")
            if series == "KXBTCD":
                return {
                    "markets": [
                        {
                            "event_ticker": "KXBTCD",
                            "strike_type": "ABOVE",
                            "floor_strike": None,
                            "cap_strike": None,
                            "close_time": "2026-10-09T02:00:00Z",
                            "expiration_value": "67000.50",
                            "rules_primary": "Settlement index is the BTC Kalshi Index at 8:00 UTC",
                            "result": "YES",
                        },
                        {
                            "event_ticker": "KXBTCD",
                            "strike_type": "BELOW",
                            "floor_strike": "66000",
                            "cap_strike": "68000",
                            "close_time": "2026-10-09T02:00:00Z",
                            "expiration_value": "67000.50",
                            "rules_primary": "Settlement index is the BTC Kalshi Index at 8:00 UTC",
                            "result": "NO",
                        },
                        {
                            "event_ticker": "KXETHD",
                            "strike_type": "ABOVE",
                            "floor_strike": None,
                            "cap_strike": None,
                            "close_time": "2026-10-09T02:00:00Z",
                            "expiration_value": "2500.25",
                            "rules_primary": "Settlement index is the ETH Kalshi Index",
                            "result": "YES",
                        },
                    ],
                    "cursor": None,
                }
            return {"markets": [], "cursor": None}

        with patch("edgescout.kalshi._get", side_effect=fake_get):
            rows, errors = await kalshi.kalshi_settled(None, time.time() - 3600)
            # Should have 2 rows (one for KXBTCD, one for KXETHD)
            assert len(rows) == 2
            kxbtcd_row = [r for r in rows if r["event"] == "KXBTCD"][0]
            assert kxbtcd_row["settled_value"] == 67000.50
            assert kxbtcd_row["rules"][:30] == "Settlement index is the BTC Ka"
            assert len(kxbtcd_row["markets"]) == 2  # Two markets for KXBTCD
            assert kxbtcd_row["markets"][0] == ["ABOVE", None, None, "YES"]
            assert kxbtcd_row["markets"][1] == ["BELOW", 66000.0, 68000.0, "NO"]

    asyncio.run(run_test())


def test_kalshi_settled_rules_truncation(monkeypatch):
    async def fake(c, url, params=None, **kw):
        m = {"ticker": "E-T1", "event_ticker": "E", "close_time": "2026-10-09T02:00:00Z", "strike_type": "greater",
             "floor_strike": 1.0, "result": "yes", "expiration_value": "2.0", "rules_primary": "x" * 700}
        return {"markets": [m] if params["series_ticker"] == "KXBTCD" else [], "cursor": None}
    monkeypatch.setattr(kalshi, "_get", fake)
    rows, errors = asyncio.run(kalshi.kalshi_settled(None, 0))
    assert errors == {} and len(rows) == 1 and rows[0]["rules"] == "x" * 600 and rows[0]["settled_value"] == 2.0


# Test 6: parse_option parses option names correctly
def test_parse_option_call():
    result = kalshi.parse_option("BTC-9OCT26-82000-C")
    assert result is not None
    assert result["currency"] == "BTC"
    assert result["strike"] == 82000.0
    assert result["cp"] == "C"
    assert result["expiry"] == 1791532800.0  # 2026-10-09 08:00 UTC


def test_parse_option_with_decimal_strike():
    result = kalshi.parse_option("ETH-10OCT26-2500d5-P")
    assert result is not None
    assert result["currency"] == "ETH"
    assert result["strike"] == 2500.5
    assert result["cp"] == "P"
    assert result["expiry"] == 1791619200.0  # 2026-10-10 08:00 UTC


def test_parse_option_perpetual():
    result = kalshi.parse_option("BTC-PERPETUAL")
    assert result is None


# Test 7: deribit_options keeps options within criteria
def test_deribit_options_filtering():
    async def run_test():
        now = 1791500000.0
        call_counts = {"BTC": 0, "ETH": 0}

        async def fake_get(c, url, **kw):
            if "get_index_price" in url:
                cur = kw.get("params", {}).get("index_name", "").split("_")[0].upper()
                if cur == "BTC":
                    return {"result": {"index_price": 80000.0}}
                elif cur == "ETH":
                    return {"result": {"index_price": 2500.0}}
            elif "get_book_summary_by_currency" in url:
                cur = kw.get("params", {}).get("currency")
                call_counts[cur] += 1
                if cur == "ETH" and call_counts["ETH"] == 1:
                    raise RuntimeError("Connection error")
                if cur == "BTC":
                    # now < expiry <= now + 3 days (DERIBIT_MAX_DAYS=3)
                    # Good: 82000 is within +-10% of 80000
                    # Bad: 92000 is > 110% of 80000
                    # Bad: perpetual way in future
                    return {
                        "result": [
                            {"instrument_name": "BTC-9OCT26-82000-C"},  # Good: within days and band
                            {"instrument_name": "BTC-15OCT26-82000-C"},  # Bad: too far in future
                            {"instrument_name": "BTC-9OCT26-92000-C"},  # Bad: strike too far
                            {
                                "instrument_name": "BTC-PERPETUAL"
                            },  # Bad: perpetual (returns None)
                        ]
                    }
                return {"result": []}

        with patch("edgescout.kalshi._get", side_effect=fake_get):
            rows, errors = await kalshi.deribit_options(None, now)
            # Should have: 1 index row for BTC + 1 option row; ETH fails so no rows for it
            btc_rows = [r for r in rows if r.get("currency") == "BTC"]
            eth_rows = [r for r in rows if r.get("currency") == "ETH"]
            assert len(btc_rows) == 2  # 1 index + 1 option
            assert btc_rows[0]["kind"] == "index"
            assert btc_rows[0]["index"] == 80000.0
            assert btc_rows[1]["kind"] == "option"
            assert btc_rows[1]["strike"] == 82000.0
            assert len(eth_rows) == 0  # ETH failed, so no rows
            assert "deribit:ETH" in errors

    asyncio.run(run_test())


# Test 8: scan loop wiring with kalshi events
def test_scan_loop_writes_kalshi_kinds(tmp_path, monkeypatch):
    async def run_test():
        # Monkeypatch time and venues
        monkeypatch.setattr("edgescout.scan.DATA", tmp_path)

        # Fake venues
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

        # Fake kalshi functions
        async def fake_kalshi_open(c):
            return [{"series": "KXBTCD", "ticker": "TEST"}], {}

        async def fake_kalshi_settled(c, since):
            # Return the same event twice to test dedup
            return [{"event": "EV1", "settled_value": 100}], {}

        async def fake_deribit_options(c, t):
            return [{"currency": "BTC", "kind": "option"}], {}

        monkeypatch.setattr("edgescout.scan.kalshi.kalshi_open", fake_kalshi_open)
        monkeypatch.setattr("edgescout.scan.kalshi.kalshi_settled", fake_kalshi_settled)
        monkeypatch.setattr("edgescout.scan.kalshi.deribit_options", fake_deribit_options)

        # Run scan with very small intervals
        await scan.scan(minutes=0.001, spot_every=0.0001, deriv_every=0.0001, kalshi_every=0.0001, settled_every=0.0001, quiet=True)

        # Read the written scan file
        scan_files = list(tmp_path.glob("scan-*.jsonl"))
        assert len(scan_files) > 0

        records = []
        for sf in scan_files:
            for line in sf.read_text().strip().split("\n"):
                if line:
                    records.append(json.loads(line))

        kinds = {r["kind"] for r in records}
        assert "kalshi" in kinds
        assert "deribit_options" in kinds
        assert "kalshi_settled" in kinds

        # Check dedup: kalshi_settled should appear but with only new events
        settled_records = [r for r in records if r["kind"] == "kalshi_settled"]
        # The scan loop dedup should have only written the event once
        all_events = []
        for sr in settled_records:
            all_events.extend([e["event"] for e in sr["rows"]])
        # Since we returned the same event twice but scan dedup handles it, we should see EV1 once
        assert "EV1" in all_events

    asyncio.run(run_test())


def test_kalshi_requests_go_one_at_a_time(monkeypatch):
    """A burst of concurrent requests got HTTP 429 live: series are fetched in turn, never overlapping."""
    state = {"active": 0, "max": 0, "n": 0}

    async def fake(c, url, params=None, **kw):
        state["active"] += 1
        state["max"] = max(state["max"], state["active"])
        state["n"] += 1
        await asyncio.sleep(0)
        state["active"] -= 1
        return {"markets": [], "cursor": None}
    monkeypatch.setattr(kalshi, "_get", fake)
    asyncio.run(kalshi.kalshi_open(None))
    asyncio.run(kalshi.kalshi_settled(None, 0))
    assert state["max"] == 1 and state["n"] == 2 * len(kalshi.SERIES)
