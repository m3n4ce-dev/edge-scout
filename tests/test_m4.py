"""M4's quoting simulation on synthetic books and trades (docs/M4_LIP.md): Kalshi's LIP scoring, queue position,
fills from the tape, netting, fees, valuation and the windows."""
import pytest

from edgescout import m4

T0 = 1_800_000_000.0 - (1_800_000_000.0 % 86400)
DAY = 86400.0


def prog(reward=86.4, target=1000.0, discount=0.5, start=T0, end=T0 + DAY):
    return {"id": "p", "ticker": "KXT-1", "start": start, "end": end, "reward": reward, "target": target,
            "discount": discount}


def book(t, yes, no):
    return {"ticker": "KXT-1", "t": t, "yes": yes, "no": no}


# ---------------------------------------------------------------- scoring, as Kalshi's rules state it
def test_reference_price_and_discount_per_tick():
    # 300 at 50c reaches target/5 = 200: the reference is 50c; 700 more count at 48c, two ticks below: 0.5 ** 2
    ours, every, total = m4.credit([[0.50, 300.0], [0.48, 1000.0]], None, 0, 1000, 0.5, False)
    assert (ours, total) == (0.0, 1300.0)
    assert every == pytest.approx(300 + 700 * 0.25)


def test_depth_counts_only_to_the_target():
    ours, every, _ = m4.credit([[0.50, 5000.0]], None, 0, 1000, 0.5, False)
    assert every == pytest.approx(1000.0)


def test_first_in_the_queue_moves_the_reference_to_its_price():
    # our 200 at 51c reach target/5 alone: the reference is 51c, and the 800 that count behind us at 50c are a tick
    # below it (x 0.5)
    ours, every, total = m4.credit([[0.50, 5000.0]], 0.51, 200, 1000, 0.5, True)
    assert ours == pytest.approx(200.0) and every == pytest.approx(200 + 800 * 0.5) and total == 5200.0


def test_behind_a_queue_bigger_than_the_target_earns_nothing():
    ours, every, _ = m4.credit([[0.98, 230543.0]], 0.98, 200, 1000, 0.5, False)
    assert ours == 0.0 and every == pytest.approx(1000.0)


def test_behind_a_small_queue_counts_up_to_the_target():
    ours, every, _ = m4.credit([[0.50, 900.0], [0.49, 5000.0]], 0.50, 200, 1000, 0.5, False)
    assert ours == pytest.approx(100.0) and every == pytest.approx(1000.0)


# ---------------------------------------------------------------- the reward
def deep():
    return [[0.40, 5000.0]], [[0.55, 5000.0]]            # spread 5 ticks: we go first on both sides


def test_reward_is_pool_rate_times_time_times_share():
    y, n = deep()
    r = m4.simulate([book(T0, y, n)], [prog()], [], None, 0.0, T0, T0 + 120)
    # $86.40/day = $0.001/s; first in queue on each side: 200 of (200 + 800 x 0.5) = 1/3
    assert r["reward"] == pytest.approx(0.001 * 120 / 3)
    assert r["fills"] == 0 and r["eligible_s"] == 120 and r["improve_s"] == 120


def test_a_book_without_the_target_on_both_sides_pays_nothing():
    r = m4.simulate([book(T0, [[0.40, 5000.0]], [[0.55, 300.0]])], [prog()], [], None, 0.0, T0, T0 + 120)
    assert r["reward"] == 0.0 and r["eligible_s"] == 0


def test_no_program_running_means_no_quote():
    y, n = deep()
    r = m4.simulate([book(T0, y, n)], [prog(start=T0 + 3600)], [], None, 0.0, T0, T0 + 120)
    assert r["quoted_s"] == 0 and r["reward"] == 0


def test_a_quote_lasts_at_most_max_gap():
    y, n = deep()
    r = m4.simulate([book(T0, y, n), book(T0 + 3600, y, n)], [prog()], [], None, 0.0, T0, T0 + 3600 + 120)
    assert r["quoted_s"] == pytest.approx(m4.MAX_GAP_S + 120)


# ---------------------------------------------------------------- fills
def test_first_in_queue_is_filled_by_a_print_at_or_below_its_bid():
    y, n = deep()                                        # we bid YES 41c (first) and NO 56c (first)
    trades = [[T0 + 10, "no", 0.40, 0.60, 50.0],        # a taker sold YES at 40c: hits our 41c bid first
              [T0 + 20, "yes", 0.45, 0.55, 30.0],       # a taker bought YES at 45c (NO 55c): hits our 56c NO bid
              [T0 + 30, "yes", 0.43, 0.57, 30.0]]       # a NO bid at 57c, above ours, took this one: not ours
    r = m4.simulate([book(T0, y, n)], [prog()], trades, "yes", 0.0, T0, T0 + 120)
    assert r["fills"] == pytest.approx(50.0 + 30.0)
    # 30 YES + 30 NO net to $1 each (1 - 0.41 - 0.56); the other 20 YES at 41c settle at $1
    assert r["fill_pnl"] == pytest.approx(30 * (1 - 0.41 - 0.56) + 20 * (1 - 0.41))


def test_a_print_above_our_bid_isnt_ours():
    y, n = deep()
    r = m4.simulate([book(T0, y, n)], [prog()], [[T0 + 5, "no", 0.45, 0.55, 50.0]], None, 0.0, T0, T0 + 120)
    assert r["fills"] == 0


def test_at_the_best_bid_the_size_ahead_trades_first():
    y, n = [[0.40, 100.0], [0.39, 5000.0]], [[0.59, 5000.0]]     # spread 1 tick: join both bests
    trades = [[T0 + 5, "no", 0.40, 0.60, 80.0],                 # 80 of the 100 ahead
              [T0 + 6, "no", 0.40, 0.60, 50.0],                 # 20 ahead, then 30 of ours
              [T0 + 7, "no", 0.39, 0.61, 500.0]]                # through our price: the rest of ours (170)
    r = m4.simulate([book(T0, y, n)], [prog()], trades, "no", 0.0, T0, T0 + 120)
    assert r["fills"] == pytest.approx(200.0)
    assert r["improve_s"] == 0
    assert r["fill_pnl"] == pytest.approx(-200 * 0.40)


def test_fills_shrink_the_resting_share():
    y, n = deep()
    full = m4.simulate([book(T0, y, n)], [prog()], [], None, 0.0, T0, T0 + 120)["reward"]
    half = m4.simulate([book(T0, y, n)], [prog()], [[T0 + 60, "no", 0.40, 0.60, 1000.0]], None, 0.0, T0, T0 + 120)
    # the YES side rests for half the interval: (s * 0.5 + s) / 2 of the pool instead of s
    assert half["reward"] == pytest.approx(full * 0.75)


def test_inventory_at_the_target_stops_that_side():
    y, n = deep()
    books = [book(T0 + 120 * k, y, n) for k in range(7)]
    trades = [[T0 + 120 * k + 1, "no", 0.40, 0.60, 1000.0] for k in range(7)]   # each fills our 200 YES
    r = m4.simulate(books, [prog()], trades, None, 0.0, T0, T0 + 7 * 120)
    assert r["fills"] == pytest.approx(1000.0) and r["open_net"] == pytest.approx(1000.0)


# ---------------------------------------------------------------- positions, fees, value
def test_yes_and_no_in_one_market_net_to_a_dollar():
    p = m4.Position()
    p.add("yes", 10, 0.40)
    p.add("no", 10, 0.50)
    assert p.net == 0 and p.cost == pytest.approx(0) and p.realized == pytest.approx(1.0)
    assert p.value(0.0) == pytest.approx(1.0) and p.value(1.0) == pytest.approx(1.0)


def test_an_open_position_is_marked_at_the_last_mid_when_unsettled():
    y, n = deep()                                        # last mid: (0.40 + 0.45) / 2
    r = m4.simulate([book(T0, y, n)], [prog()], [[T0 + 5, "no", 0.40, 0.60, 100.0]], None, 0.0, T0, T0 + 120)
    assert r["marked"] and r["fill_pnl"] == pytest.approx(100 * (0.425 - 0.41))


def test_fee_types():
    assert m4.fee_rate("quadratic", 1) == 0.0
    assert m4.fee_rate("quadratic_with_maker_fees", 1) == pytest.approx(0.0175)
    assert m4.fee_rate("something_new", None) == pytest.approx(0.07)
    assert m4.maker_fee(0.0175, 0.5, 100) == pytest.approx(0.44)          # ceil(43.75 cents)
    y, n = deep()
    r = m4.simulate([book(T0, y, n)], [prog()], [[T0 + 5, "no", 0.40, 0.60, 100.0]], "yes", 0.0175, T0, T0 + 120)
    assert r["fees"] == pytest.approx(m4.maker_fee(0.0175, 0.41, 100))
    assert r["net"] == pytest.approx(r["reward"] + r["fill_pnl"] - r["fees"])


def test_capital_counts_resting_bids_and_open_cost():
    y, n = deep()
    r = m4.simulate([book(T0, y, n)], [prog()], [], None, 0.0, T0, T0 + 120)
    assert r["capital_s"] == pytest.approx(120 * 200 * (0.41 + 0.56))


# ---------------------------------------------------------------- windows and the summary
def test_windows():
    d = {"books": {"KXT-1": [book(T0 + 3600, [], [])]}}
    s, e = m4.window(d, False)
    assert s == T0 + 3600 and e == T0 + 8 * DAY
    with pytest.raises(ValueError):
        m4.window(d, True, now=T0 + 20 * DAY)
    assert m4.window(d, True, now=T0 + 23 * DAY) == (T0 + 8 * DAY, T0 + 22 * DAY)


def test_evaluate_and_verdict():
    y, n = deep()
    d = {"books": {}, "programs": {}, "series": {"KXT": {"series": "KXT", "fee_type": "quadratic"}}}
    pulled = {}
    for k in range(70):
        tk = f"KXT-{k}"
        d["books"][tk] = [book(T0 + 120 * j, y, n) | {"ticker": tk} for j in range(5)]
        d["programs"][tk] = prog() | {"id": tk, "ticker": tk}
        pulled[tk] = {"ticker": tk, "trades": [], "result": None}
    e = m4.evaluate(d, pulled, T0, T0 + DAY)
    assert e["markets"] == 70 and e["fills"] == 0 and e["return_on_capital"]["lo90"] > 0
    assert e["series_with_3_markets_net_positive"] == "1/1"
    assert m4.verdict(e).startswith("proceed")
    e["return_on_capital"]["lo90"] = -1.0
    assert m4.verdict(e) == "kill"
