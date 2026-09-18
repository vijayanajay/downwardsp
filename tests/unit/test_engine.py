"""Golden tests for the Phase 6 engine: hand-computed numbers, no tolerance.

Fixtures:
  - the standard synthetic market (same construction as test_setups.py):
    SHOCK fires Setup 5 on the FINAL session, so a full-range replay stays
    flat (the entry day lies beyond the data) — and a truncated replay with a
    directly-placed position exercises the money paths.
  - direct SimPosition + engine money settlement for every rupee.

These tests ARE the engine specification.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from nse_cash.backtest.engine import (BarCache, SimBook, _consume_pending_liquidation,
                                      _settle_position_money, run_backtest, warmup)
from nse_cash.backtest.fill_model import (SimPosition, force_exit,
                                          simulate_entry_day, simulate_open_day)
from nse_cash.backtest.tax_friction import STCGAccount
from nse_cash.core.config import SystemConfig
from nse_cash.core.types import ExitReason
from nse_cash.data.storage import MarketStore

CFG = SystemConfig()
F = CFG.friction
SLOT = CFG.capital.slot_capital
BUY_RATE = (F.stt_delivery + F.stamp_duty + F.nse_turnover + F.sebi_fee
            + F.slippage_per_side
            + (F.nse_turnover + F.sebi_fee) * F.gst_rate)

START = date(2026, 1, 1)


def _sessions(n: int) -> list[date]:
    days: list[date] = []
    d = START
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _store_with_signal(tmp_path, n: int = 260) -> MarketStore:
    """The standard synthetic market: SHOCK fires Setup 5 on the last session."""
    store = MarketStore(tmp_path / "engine.duckdb")
    sessions = _sessions(n)
    rng = np.random.default_rng(11)
    idx = pd.DataFrame({
        "index_name": ["NIFTY 50"] * n + ["NIFTY 500"] * n,
        "date": sessions * 2,
        "close": list(24_000.0 * np.cumprod(1.0 + rng.normal(0.0004, 0.007, n)))
        + list(4_600.0 * np.cumprod(1.0 + rng.normal(0.0004, 0.006, n))),
        "open": 0.0, "high": 0.0, "low": 0.0, "volume": 0.0,
    })
    store.upsert_market_indices(idx)

    for sym, base in (("SHOCK", 100.0), ("BANKX", 500.0)):
        rets = np.where(np.arange(n) % 2 == 1, 0.02, -0.005)
        closes = list(base * np.cumprod(1.0 + rets))
        closes[-1] = closes[-2] * 1.015
        opens = [closes[0]] + closes[:-1]
        highs = [max(o, c) * 1.002 for o, c in zip(opens, closes)]
        lows = [min(o, c) * 0.998 for o, c in zip(opens, closes)]
        if sym == "BANKX":
            # CR-2026-001: with the fixed predicates the Setup 3 gate (top-RS,
            # within 1.5% of the 52w high, <= 3% range) legitimately fired for
            # BANKX mid-year in a 2-symbol universe. Keep the decoy a decoy:
            # a ~5% intraday range fails Setup 3's consolidation cap forever.
            highs = [max(o, c) * 1.025 for o, c in zip(opens, closes)]
            lows = [min(o, c) * 0.975 for o, c in zip(opens, closes)]
        bars = pd.DataFrame({
            "symbol": sym, "date": sessions,
            "open": opens, "high": highs, "low": lows, "close": closes,
            "volume": [100_000] * n, "turnover": [10_000_000.0] * n,
            "deliverable_qty": [40_000] * n, "delivery_pct": [40.0] * n,
            "open_adj": opens, "high_adj": highs, "low_adj": lows,
            "close_adj": closes,
            "volume_adj": [100_000.0] * n, "delivery_adj": [40_000.0] * n,
        })
        # Delivery shock + green candle on the final day: Setup 5 signature.
        bars.loc[bars.index[-1], "delivery_adj"] = 200_000.0
        bars.loc[bars.index[-1], "deliverable_qty"] = 200_000
        bars.loc[bars.index[-1], "low"] = closes[-1] * 0.985
        bars.loc[bars.index[-1], "low_adj"] = closes[-1] * 0.985
        bars.loc[bars.index[-1], "high"] = closes[-1] * 1.021
        bars.loc[bars.index[-1], "high_adj"] = closes[-1] * 1.021
        store.upsert_daily_bars(bars)
    return store


# ---------------------------------------------------------------------------
# Unit: STCG account
# ---------------------------------------------------------------------------

class TestSTCGAccount:
    def test_flat_rule_matches_stcg_tax(self):
        from nse_cash.backtest.tax_friction import stcg_tax
        acc = STCGAccount(F, carry_forward=False)
        acc.add(10_000.0, date(2026, 5, 10))
        acc.add(-4_000.0, date(2026, 6, 11))
        acc.finalize()
        assert acc.tax_paid == pytest.approx(stcg_tax(6_000.0, F))
        assert acc.fy_summary[0]["taxable"] == pytest.approx(6_000.0)

    def test_loss_carried_forward_offsets_next_fy(self):
        acc = STCGAccount(F, carry_forward=True)
        acc.add(-50_000.0, date(2026, 1, 15))    # FY2025-26 loss
        acc.add(30_000.0, date(2026, 4, 10))     # FY2026-27 profit
        acc.finalize()
        assert acc.tax_paid == 0.0               # 30k - 50k carry = net loss
        assert acc.fy_summary[1]["brought_forward"] == pytest.approx(-50_000.0)
        assert acc.fy_summary[1]["taxable"] == pytest.approx(-20_000.0)

    def test_carry_forward_expires_after_8_fys(self):
        """A FY2010-11 loss of 80k offsets the next 8 FYs (2011-12 .. 2018-19)
        up to its size, then dies before FY2019-20."""
        acc = STCGAccount(F, carry_forward=True)
        acc.add(-80_000.0, date(2010, 6, 1))     # FY2010-11 loss lot
        acc.finalize()
        # FYs 2011-12 .. 2018-19, 10k profit each: 80k loss absorbs all 80k.
        for yr in range(2011, 2019):
            acc.add(10_000.0, date(yr, 7, 1))
            acc.finalize()
        assert acc.tax_paid == 0.0
        # FY2019-20 (9th after origin): lot expired -> full tax.
        acc.add(10_000.0, date(2019, 7, 1))
        acc.finalize()
        assert acc.tax_paid == pytest.approx(10_000.0 * F.stcg_tax_rate)

    def test_profit_year_resets_carry_forward(self):
        acc = STCGAccount(F, carry_forward=True)
        acc.add(-20_000.0, date(2025, 6, 1))
        acc.add(10_000.0, date(2025, 8, 1))
        acc.add(50_000.0, date(2026, 5, 1))
        acc.finalize()
        # FY25-26: -20k + 10k = -10k carried; FY26-27: 50k - 10k = 40k taxed
        assert acc.tax_paid == pytest.approx(40_000.0 * F.stcg_tax_rate)
        assert acc.fy_summary[-1]["brought_forward"] == pytest.approx(-10_000.0)

    def test_financial_year_boundary_split(self):
        acc = STCGAccount(F, carry_forward=False)
        acc.add(1_000.0, date(2026, 3, 31))
        acc.add(2_000.0, date(2026, 4, 1))       # next FY
        acc.finalize()
        assert len(acc.fy_summary) == 2
        assert acc.fy_summary[0]["fy"] == "FY2025-26"
        assert acc.fy_summary[1]["fy"] == "FY2026-27"
        assert acc.tax_paid == pytest.approx(3_000.0 * F.stcg_tax_rate)


# ---------------------------------------------------------------------------
# Unit: metrics math
# ---------------------------------------------------------------------------

class TestMetricsMath:
    def test_max_drawdown_and_duration(self):
        from nse_cash.backtest.metrics import max_drawdown
        mdd, dur = max_drawdown(pd.Series([100, 110, 99, 105, 120, 90, 115, 121]))
        assert mdd == pytest.approx(90 / 120 - 1)          # -25%
        # Peaks at 110 (i=1) and 120 (i=4): 2 underwater sessions each spell.
        assert dur == 2

    def test_no_drawdown_monotonic(self):
        from nse_cash.backtest.metrics import max_drawdown
        mdd, dur = max_drawdown(pd.Series([100, 101, 102, 103]))
        assert mdd == 0.0 and dur == 0

    def test_cagr_two_years(self):
        from nse_cash.backtest.metrics import cagr
        # 100 -> 121 over 504 sessions (2y): sqrt(1.21) - 1 = 10%
        assert cagr(pd.Series([100.0] + [100.0] * 502 + [121.0]),
                    pd.Series(range(504))) == pytest.approx(0.10, rel=1e-6)

    def test_sharpe_zero_variance_is_zero(self):
        from nse_cash.backtest.metrics import sharpe_ratio
        assert sharpe_ratio(pd.Series([100.0, 100.0, 100.0])) == 0.0

    def test_win_stats_profit_factor(self):
        from nse_cash.backtest.metrics import win_stats
        trades = pd.DataFrame({
            "entry_price": [100.0, 100.0, 100.0, None],  # 4th = gap rejection
            "realized_pnl": [200.0, -150.0, 50.0, 0.0],
        })
        ws = win_stats(trades)
        assert ws["trades"] == 3
        assert ws["win_rate"] == pytest.approx(2 / 3)
        assert ws["profit_factor"] == pytest.approx(250 / 150)
        assert ws["expectancy_net"] == pytest.approx(100 / 3)

    def test_monthly_returns_keys(self):
        from nse_cash.backtest.metrics import monthly_returns
        eq = pd.DataFrame({
            "date": list(pd.date_range("2026-01-01", periods=30).date)
            + list(pd.date_range("2026-02-01", periods=30).date),
            "equity": [100.0] * 30 + [110.0] * 30,
        })
        mr = monthly_returns(eq)
        assert "2026-02" in mr
        assert mr["2026-02"] == pytest.approx(0.10)


# ---------------------------------------------------------------------------
# Warm-up self-healing
# ---------------------------------------------------------------------------

class TestWarmup:
    def test_warmup_self_heals_universe_and_features(self, tmp_path):
        store = _store_with_signal(tmp_path)
        try:
            start = store.trading_dates()[-20]
            end = store.trading_dates()[-1]
            warm = warmup(store, start, end)
            dates = pd.to_datetime(warm.regime_df["date"]).dt.date
            assert dates.min() == start and dates.max() == end
            assert warm.nifty50_coverage > 0
            # PIT universe rebuilt for run dates.
            pit_dates = {pd.Timestamp(r[0]).date() for r in store.con.execute(
                "SELECT DISTINCT date FROM pit_universe").fetchall()}
            assert start in pit_dates
            # Features computed (final session at minimum).
            feat_dates = {pd.Timestamp(r[0]).date() for r in store.con.execute(
                "SELECT DISTINCT date FROM features").fetchall()}
            assert end in feat_dates
        finally:
            store.close()

    def test_warmup_idempotent(self, tmp_path):
        store = _store_with_signal(tmp_path)
        try:
            start = store.trading_dates()[-20]
            end = store.trading_dates()[-1]
            warmup(store, start, end)
            n_feat = store.con.execute("SELECT count(*) FROM features").fetchone()[0]
            n_gov = store.con.execute(
                "SELECT count(*) FROM governance").fetchone()[0]
            warmup(store, start, end)
            assert store.con.execute(
                "SELECT count(*) FROM features").fetchone()[0] == n_feat
            assert store.con.execute(
                "SELECT count(*) FROM governance").fetchone()[0] == n_gov
        finally:
            store.close()


# ---------------------------------------------------------------------------
# Bar cache
# ---------------------------------------------------------------------------

class TestBarCache:
    def test_bar_and_close(self, tmp_path):
        store = _store_with_signal(tmp_path)
        try:
            dates = store.trading_dates()
            cache = BarCache(store, dates[0], dates[-1])
            d = dates[-1]
            bar = cache.bar("SHOCK", d)
            assert bar is not None and float(bar["close"]) > 0
            assert cache.close("SHOCK", d) == float(bar["close"])
            assert cache.bar("NOSUCH", d) is None
            assert cache.close("SHOCK", date(1900, 1, 1)) is None
        finally:
            store.close()


# ---------------------------------------------------------------------------
# Engine golden scenarios
# ---------------------------------------------------------------------------

class TestEngine:
    def test_full_replay_stays_flat_when_entry_day_lies_beyond_data(self, tmp_path):
        """The SHOCK signal fires on the last bar; its entry day is the next
        session, which does not exist. The honest outcome: zero filled trades,
        equity = cash (capital + liquid-fund interest), no invented positions."""
        store = _store_with_signal(tmp_path)
        try:
            dates = store.trading_dates()
            result = run_backtest(store, CFG, dates[0], dates[-1])
            # Book stays flat all year: equity row == cash row every day.
            eq = result.equity
            assert (eq["equity"] == eq["cash"]).all()
            assert eq["equity"].iloc[-1] >= CFG.capital.base_capital
            assert result.interest_paid > 0
            filled = 0 if result.trades.empty else \
                int(result.trades["entry_price"].notna().sum())
            assert filled == 0
            assert result.kill_count == 0
        finally:
            store.close()

    def test_replay_equity_curve_is_one_row_per_session(self, tmp_path):
        store = _store_with_signal(tmp_path)
        try:
            dates = store.trading_dates()
            start = dates[-30]
            result = run_backtest(store, CFG, start, dates[-1])
            assert len(result.equity) == 30
            assert list(result.equity["date"]) == dates[-30:]
        finally:
            store.close()

    def test_money_settlement_t1_fill_then_breakeven_exit(self):
        """Engine money path, every rupee hand-computed. Entry at 100.0,
        T1 fills at 102.0 same day; next day opens below the armed breakeven
        stop (100.0) -> T2 exits at the open. Sell days differ -> full DP twice."""
        qty = int(SLOT // (100.0 * (1.0 + BUY_RATE)))
        t1, t2 = qty // 2, qty - qty // 2
        pos = SimPosition(
            trade_id="G1", symbol="TEST", setup="SETUP_5_RESIDUAL_MOM",
            entry_ref_raw=100.0, max_entry_raw=101.2, structural_stop_raw=98.0,
            tranche1_target_raw=102.0, tranche2_target_raw=106.0,
            tranche1_qty=t1, tranche2_qty=t2)
        book = SimBook(config=CFG, cash=CFG.capital.base_capital,
                       peak_equity=CFG.capital.base_capital)
        d1, d2, d3 = date(2026, 8, 4), date(2026, 8, 5), date(2026, 8, 6)

        # Entry day: fill at open 100.0, T1 target 102.0 touched by high 102.5.
        simulate_entry_day(pos, {"date": d1, "open": 100.0, "high": 102.5,
                                 "low": 99.5, "close": 101.5}, CFG)
        assert pos.entry_price_raw == 100.0 and pos.t1_filled

        # Day 2: no touches (high 103 < 106, low 100.4 > BE stop 100.0);
        # close 101.5 > stall floor 100.8 -> alive, breakeven armed at EOD.
        simulate_open_day(pos, {"date": d2, "open": 101.0, "high": 103.0,
                                "low": 100.4, "close": 101.5}, CFG)
        assert pos.is_open and pos.pending_stop_raw == 100.0

        # Day 3: open 99.8 gaps through the breakeven stop -> T2 exits at 99.8.
        simulate_open_day(pos, {"date": d3, "open": 99.8, "high": 100.2,
                                "low": 99.0, "close": 99.9}, CFG)
        assert not pos.is_open

        # Settle through the engine's money path.
        _settle_position_money(book, pos, CFG)

        # --- hand computation ---
        buy_value = qty * 100.0
        buy_cost = buy_value * (1 + BUY_RATE)
        t1_value = t1 * 102.0
        t1_net = t1_value * (1 - (F.stt_delivery + F.nse_turnover + F.sebi_fee
                                  + F.slippage_per_side
                                  + (F.nse_turnover + F.sebi_fee) * F.gst_rate)) \
            - F.dp_charge_per_sell
        t2_value = t2 * 99.8
        t2_net = t2_value * (1 - (F.stt_delivery + F.nse_turnover + F.sebi_fee
                                  + F.slippage_per_side
                                  + (F.nse_turnover + F.sebi_fee) * F.gst_rate)) \
            - F.dp_charge_per_sell
        expected_cash = CFG.capital.base_capital - buy_cost + t1_net + t2_net
        assert book.cash == pytest.approx(expected_cash, rel=1e-9)

        expected_pnl = (t1_net + t2_net) - buy_cost
        assert pos.exit_proceeds - pos.entry_cost == pytest.approx(expected_pnl,
                                                                   rel=1e-9)
        assert pos.exit_reason == ExitReason.TRAILING_STOP_HIT.value

    def test_end_of_run_force_exit_flattens_and_settles(self):
        book = SimBook(config=CFG, cash=CFG.capital.base_capital,
                       peak_equity=CFG.capital.base_capital)
        pos = SimPosition(
            trade_id="E1", symbol="X", setup="SETUP_1_VCP",
            entry_ref_raw=100.0, max_entry_raw=101.2,
            structural_stop_raw=98.0, tranche1_target_raw=102.0,
            tranche2_target_raw=106.0, tranche1_qty=37, tranche2_qty=37,
            slot=1)
        pos.entry_price_raw = 100.0
        pos.entry_date = date(2026, 8, 4)
        pos.day_index = 1
        force_exit(pos, 99.0, date(2026, 8, 8), reason=ExitReason.END_OF_RUN)
        _settle_position_money(book, pos, CFG)
        assert not pos.is_open
        assert pos.exit_reason == ExitReason.END_OF_RUN.value
        # 74 shares sold at 99 with sell friction; cash increased.
        proceeds = 74 * 99.0
        sell_rate = (F.stt_delivery + F.nse_turnover + F.sebi_fee
                     + F.slippage_per_side
                     + (F.nse_turnover + F.sebi_fee) * F.gst_rate)
        assert book.cash == pytest.approx(
            CFG.capital.base_capital + proceeds * (1 - sell_rate)
            - F.dp_charge_per_sell, rel=1e-9)

    def test_kill_switch_frozen_bar_defers_then_exits_at_open(self):
        book = SimBook(config=CFG, cash=CFG.capital.base_capital,
                       peak_equity=CFG.capital.base_capital)
        pos = SimPosition(
            trade_id="K1", symbol="X", setup="SETUP_1_VCP",
            entry_ref_raw=100.0, max_entry_raw=101.2,
            structural_stop_raw=98.0, tranche1_target_raw=102.0,
            tranche2_target_raw=106.0, tranche1_qty=37, tranche2_qty=37)
        pos.entry_price_raw = 100.0
        pos.entry_date = date(2026, 8, 4)
        pos.day_index = 1
        pos.slot = 1
        book.positions["X"] = pos

        # Circuit-frozen bar: no counterparty -> deferred.
        frozen = {"date": date(2026, 8, 5), "open": 99.0, "high": 99.0,
                  "low": 99.0, "close": 99.0}
        _consume_pending_liquidation(book, CFG, _FakeCache(frozen),
                                     date(2026, 8, 5), None)
        assert pos.is_open, "circuit-frozen bar must defer liquidation"
        assert book.pending_liquidation

        # Normal bar: exit at the open, never the stop.
        normal = {"date": date(2026, 8, 6), "open": 98.5, "high": 99.4,
                  "low": 98.2, "close": 99.0}
        _consume_pending_liquidation(book, CFG, _FakeCache(normal),
                                     date(2026, 8, 6), None)
        assert not pos.is_open
        assert pos.exit_reason == ExitReason.KILL_SWITCH.value
        kill_events = [e for e in pos.events
                       if e.event_type.value == "KILL_SWITCH"]
        assert kill_events and all(e.price == 98.5 for e in kill_events)
        assert not book.pending_liquidation


class _FakeCache:
    def __init__(self, bar: dict) -> None:
        self._bar = bar

    def bar(self, symbol, d):
        return self._bar


# ---------------------------------------------------------------------------
# Reproducibility (R5)
# ---------------------------------------------------------------------------

class TestReproducibility:
    def test_two_runs_identical_equity_curve(self, tmp_path):
        first = None
        for run in range(2):
            store = _store_with_signal(tmp_path / f"run{run}")
            try:
                dates = store.trading_dates()
                result = run_backtest(store, CFG, dates[-30], dates[-1])
                if first is None:
                    first = result.equity.copy()
                else:
                    pd.testing.assert_frame_equal(result.equity, first)
            finally:
                store.close()
