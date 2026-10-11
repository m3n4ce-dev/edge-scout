"""M4's recorder (edgescout/lip.py) against a fake Kalshi API: the fixed sample, the program rows (centi-cents to
dollars), the series' fee types once, and the books cut at twice the target."""
import asyncio
import time

import httpx
import pytest

from edgescout import lip


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch):
    monkeypatch.setattr(lip, "PACE_S", 0)


def test_the_sample_is_a_fixed_rule_of_the_ticker():
    ts = [f"KXSER-{k}" for k in range(20000)]
    got = [t for t in ts if lip.sampled(t)]
    assert 0.03 < len(got) / len(ts) < 0.04
    assert got == [t for t in ts if lip.sampled(t)]


def test_program_row_converts_centi_cents():
    r = lip.program_row({"id": "a", "market_ticker": "KXRT-X-50", "incentive_type": "liquidity",
                         "start_date": "2026-10-10T00:00:00Z", "end_date": "2026-10-17T00:00:00Z",
                         "period_reward": 3000000, "target_size_fp": "1000.00", "discount_factor_bps": 5000})
    assert r["reward"] == 300.0 and r["target"] == 1000.0 and r["discount"] == 0.5 and r["series"] == "KXRT"
    assert lip.program_row({"incentive_type": "volume", "start_date": "2026-10-10T00:00:00Z",
                            "end_date": "2026-10-11T00:00:00Z"}) is None


def test_side_sorts_best_first_and_cuts_at_twice_the_target():
    lv = [["0.0100", "10"], ["0.4500", "150"], ["0.4400", "100"], ["0.4300", "500"], ["0.4200", "999"]]
    assert lip.side(lv, 200) == [[0.45, 150.0], [0.44, 100.0], [0.43, 500.0]]
    assert lip.side(None, 100) == []


def test_run_records_programs_series_and_books(monkeypatch):
    now = time.time()
    iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
    tickers = [f"KXSER-{k}" for k in range(400)]
    inside = [t for t in tickers if lip.sampled(t)]
    assert inside
    progs = [{"id": f"id-{t}", "market_ticker": t, "incentive_type": "liquidity", "start_date": iso(now - 60),
              "end_date": iso(now + 3600), "period_reward": 100000, "target_size_fp": "300.00",
              "discount_factor_bps": 5000} for t in tickers]
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        if req.url.path.endswith("/incentive_programs"):
            assert req.url.params["status"] == "active"
            return httpx.Response(200, json={"incentive_programs": progs, "next_cursor": ""})
        if "/series/" in req.url.path:
            return httpx.Response(200, json={"series": {"fee_type": "quadratic", "fee_multiplier": 1}})
        if req.url.path.endswith("/orderbook"):
            return httpx.Response(200, json={"orderbook_fp": {"yes_dollars": [["0.40", "500"]],
                                                              "no_dollars": [["0.55", "700"]]}})
        return httpx.Response(404)

    out = []

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            task = asyncio.create_task(lip.run(c, time.time() + 0.5, lambda k, r, e: out.append((k, r, e)),
                                               every=10, programs_every=3600))
            await asyncio.sleep(0.3)
            task.cancel()

    asyncio.run(go())
    kinds = {k: (r, e) for k, r, e in out}
    assert {r["ticker"] for r in kinds["lip_programs"][0]} == set(inside)
    assert kinds["lip_series"][0] == [{"series": "KXSER", "fee_type": "quadratic", "fee_multiplier": 1.0}]
    books, errs = kinds["lip_books"]
    assert not errs and {b["ticker"] for b in books} == set(inside)
    assert books[0]["yes"] == [[0.4, 500.0]] and books[0]["no"] == [[0.55, 700.0]]
    assert sum(1 for p in calls if "/series/" in p) == 1
