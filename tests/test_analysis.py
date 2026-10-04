import json
import time

import pytest

from edgescout import analysis, report, venues

FEES = {"tier": "entry", "cash_yield_pct": 4.0,
        "spot": {"a": {"entry": {"maker": 0.6, "taker": 1.2}, "t10k": {"maker": 0.25, "taker": 0.4}},
                 "b": {"entry": {"maker": 0.4, "taker": 0.8}, "t10k": {"maker": 0.2, "taker": 0.35}},
                 "coinbase": {"entry": {"maker": 0.6, "taker": 1.2}, "t10k": {"maker": 0.25, "taker": 0.4}}},
        "futures": {"coinbase_cde": {"maker": 0.02, "taker": 0.02}, "deribit": {"maker": 0.0, "taker": 0.05}}}


def test_annualize():
    assert analysis.annualize(0.0001, 8 * 3600) == pytest.approx(10.95, abs=0.01)     # 0.01%/8h = 10.95%/yr
    assert analysis.annualize(0.0000125, 3600) == pytest.approx(10.95, abs=0.01)


def test_spot_spread_is_net_of_both_legs():
    rows = [{"venue": "a", "symbol": "BTC", "bid": 100.0, "ask": 100.1},
            {"venue": "b", "symbol": "BTC", "bid": 101.0, "ask": 101.2}]
    o, = analysis.spot_spreads(rows, FEES)
    assert (o["buy"], o["sell"]) == ("a", "b")
    assert o["gross_pct"] == pytest.approx((101.0 / 100.1 - 1) * 100, abs=1e-4)
    assert o["net_entry_taker"] == pytest.approx(o["gross_pct"] - 1.2 - 0.8, abs=1e-4)
    assert o["net_t10k_maker"] == pytest.approx(o["gross_pct"] - min(0.25 + 0.35, 0.4 + 0.2), abs=1e-4)


def test_funding_spreads_and_carry():
    fr = analysis.funding_rows([{"venue": "coinbase_cde", "symbol": "LINK", "rate": 0.00002, "interval_s": 3600},
                                {"venue": "okx", "symbol": "LINK", "rate": 0.0, "interval_s": 28800}], {"coinbase_cde"})
    s, = analysis.funding_spreads(fr)
    assert s["short"] == "coinbase_cde" and s["long"] == "okx" and not s["us_both"]
    assert analysis.carry(17.52, 1.24, 30) == pytest.approx(17.52 - 1.24 * 365 / 30)


def test_basis_rows_skip_wide_books_and_short_dates():
    now = time.time()
    rows = [{"venue": "coinbase_cde", "symbol": "BTC", "instrument": "X", "expiry": now + 30 * 86400, "price": 101.0,
             "spot": 100.0, "spread_bps": 5},
            {"venue": "coinbase_cde", "symbol": "BTC", "instrument": "WIDE", "expiry": now + 30 * 86400, "price": 101.0,
             "spot": 100.0, "spread_bps": 250},
            {"venue": "deribit", "symbol": "BTC", "instrument": "SOON", "expiry": now + 3600, "price": 100.1, "spot": 100.0}]
    b, = analysis.basis_rows(rows, now, FEES, "entry")
    assert b["instrument"] == "X" and b["ann_gross_pct"] == pytest.approx(1.0 * 365 / 30, rel=1e-3)
    assert b["ann_net_pct"] == pytest.approx((1.0 - (2 * 0.6 + 0.02)) * 365 / 30, rel=1e-3)


def test_deribit_expiry():
    from datetime import datetime, timezone

    assert venues.deribit_expiry("BTC-27NOV26") == datetime(2026, 11, 27, 8, tzinfo=timezone.utc).timestamp()
    assert venues.deribit_expiry("BTC-PERPETUAL") is None


def test_report_summarizes_recordings(tmp_path, monkeypatch):
    t = 1_790_000_000.0
    recs = []
    for i in range(6):
        recs.append({"t": t + 10 * i, "kind": "spot", "errors": {}, "rows": [
            {"venue": "a", "symbol": "BTC", "bid": 100.0, "ask": 100.1},
            {"venue": "b", "symbol": "BTC", "bid": 100.05 if i < 3 else 103.0, "ask": 100.2 if i < 3 else 103.1}]})
    recs.append({"t": t + 60, "kind": "funding", "errors": {"binance": "451"}, "rows": [
        {"venue": "coinbase_cde", "symbol": "BTC", "rate": 0.00001, "interval_s": 3600},
        {"venue": "hyperliquid", "symbol": "BTC", "rate": 0.0000125, "interval_s": 3600}]})
    path = tmp_path / "scan-x.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in recs))
    s = report.summarize(report.read([path]), FEES)
    btc, = s["spot"]
    assert btc["n"] == 6 and btc["net_pos_share_entry_taker"] == 0.5 and btc["longest_t10k_maker_s"] == 20
    assert s["us_carry"][0]["symbol"] == "BTC" and s["unreachable"] == {"binance": 1}
    report.print_summary(s)
