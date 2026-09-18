"""Extra coverage for the tear-sheet math (backtest/metrics.py).

Every expectation is hand-computed. The module is pure aggregation over the
engine artifacts, so a synthetic BacktestResult-shaped namespace exercises the
same code paths as a real replay — no store, no network.
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from nse_cash.backtest.metrics import (
    _fmt_bound, _fmt_mode, _target_text,
    benchmark_curve, benchmark_metrics,
    brd_targets_verdict, compute_metrics,
    cagr, exit_reason_counts, max_drawdown,
    monthly_returns, setup_breakdown, sharpe_ratio,
    sortino_ratio, trades_per_year, verify_brd_targets, win_stats,
)


# ---------------------------------------------------------------------------
# Equity-curve math
# ---------------------------------------------------------------------------

class TestMaxDrawdown:
    def test_empty_series_is_zero(self):
        assert max_drawdown(pd.Series(dtype=float)) == (0.0, 0)

    def test_drawdown_and_underwater_duration(self):
        # Peak 100 -> trough 80 (-20%) -> recovery on the 5th bar.
        eq = pd.Series([100.0, 90.0, 80.0, 85.0, 100.0])
        mdd, dur = max_drawdown(eq)
        assert mdd == pytest.approx(-0.20)
        assert dur == 3  # bars 1..3 underwater (recovery at index 4)

    def test_monotonic_has_no_drawdown(self):
        assert max_drawdown(pd.Series([1.0, 1.01, 1.02])) == (0.0, 0)

    def test_never_recovers_duration_runs_to_end(self):
        eq = pd.Series([100.0, 95.0, 96.0])
        mdd, dur = max_drawdown(eq)
        assert mdd == pytest.approx(-0.05)
        assert dur == 2


class TestRatios:
    def test_cagr_needs_two_points(self):
        assert cagr(pd.Series([1.0]), pd.Series([date(2026, 1, 1)])) == 0.0

    def test_cagr_nonpositive_start_is_zero(self):
        assert cagr(pd.Series([0.0, 2.0]), pd.Series([1, 2])) == 0.0

    def test_cagr_doubling_over_one_year(self):
        eq = pd.Series([100.0] + [100.0] * 250 + [200.0])
        # 252 sessions -> exactly one year -> 2x = 100%.
        assert cagr(eq, pd.Series(range(len(eq)))) == pytest.approx(1.0, rel=1e-6)

    def test_sharpe_needs_three_points(self):
        assert sharpe_ratio(pd.Series([1.0, 1.0])) == 0.0

    def test_sharpe_zero_variance_is_zero(self):
        assert sharpe_ratio(pd.Series([1.0, 1.0, 1.0])) == 0.0

    def test_sharpe_hand_computed(self):
        eq = pd.Series([100.0, 101.0, 102.0, 103.0])
        rets = eq.pct_change().dropna()
        expected = float(rets.mean() / rets.std(ddof=1) * np.sqrt(252))
        assert sharpe_ratio(eq) == pytest.approx(expected)

    def test_sortino_no_downside_is_zero(self):
        assert sortino_ratio(pd.Series([1.0, 1.1, 1.2])) == 0.0

    def test_sortino_needs_three_points(self):
        assert sortino_ratio(pd.Series([1.0, 0.9])) == 0.0

    def test_sortino_exceeds_sharpe_when_downside_only_some_days(self):
        eq = pd.Series([100.0, 101.0, 100.5, 102.0, 101.0, 103.0])
        assert sortino_ratio(eq) > sharpe_ratio(eq)


class TestMonthlyReturns:
    def test_empty_frame_is_empty(self):
        assert monthly_returns(pd.DataFrame()) == {}

    def test_frame_without_date_column_is_empty(self):
        assert monthly_returns(pd.DataFrame({"equity": [1.0]})) == {}

    def test_month_keys_and_first_month_anchors_on_first_equity(self):
        eq = pd.DataFrame({
            "date": [date(2026, 1, 15), date(2026, 1, 29), date(2026, 2, 13)],
            "equity": [100.0, 110.0, 121.0],
        })
        mr = monthly_returns(eq)
        assert mr["2026-01"] == pytest.approx(0.10)
        assert mr["2026-02"] == pytest.approx(0.10)


# ---------------------------------------------------------------------------
# Benchmark curves (need a store-like object with a .con)
# ---------------------------------------------------------------------------

class _FakeCon:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, _sql, _params=None):
        return SimpleNamespace(df=lambda: pd.DataFrame(
            self._rows, columns=["date", "close"]))


class TestBenchmark:
    def test_empty_table_gives_empty_curve(self):
        s = benchmark_curve(SimpleNamespace(con=_FakeCon([])), "NIFTY 500",
                            date(2026, 1, 1), date(2026, 3, 1))
        assert s.empty

    def test_curve_normalized_to_base_one(self):
        rows = [(date(2026, 1, 1), 100.0), (date(2026, 1, 2), 110.0)]
        s = benchmark_curve(SimpleNamespace(con=_FakeCon(rows)), "NIFTY 500",
                            date(2026, 1, 1), date(2026, 3, 1))
        assert s.iloc[0] == pytest.approx(1.0)
        assert s.iloc[-1] == pytest.approx(1.10)

    def test_metrics_on_short_curve_are_none(self):
        m = benchmark_metrics(SimpleNamespace(con=_FakeCon([])), "NIFTY 500",
                              date(2026, 1, 1), date(2026, 3, 1))
        assert m == {"benchmark_cagr": None, "benchmark_max_drawdown": None,
                     "benchmark_name": "NIFTY 500"}

    def test_metrics_cagr_and_drawdown(self):
        rows = [(date(2026, 1, 1), 100.0), (date(2026, 1, 2), 90.0),
                (date(2026, 1, 3), 110.0)]
        m = benchmark_metrics(SimpleNamespace(con=_FakeCon(rows)), "NIFTY 500",
                              date(2026, 1, 1), date(2026, 1, 3))
        assert m["benchmark_max_drawdown"] == pytest.approx(-0.10)
        # 3 sessions / 252 = 0.0119 years -> 1.10^(252/3) - 1 compounds violently.
        # The math is the definition; only the sign and scale are asserted.
        assert m["benchmark_cagr"] > 1_000


# ---------------------------------------------------------------------------
# Trade aggregation
# ---------------------------------------------------------------------------

class TestAggregations:
    def test_exit_reason_counts_empty(self):
        assert exit_reason_counts(pd.DataFrame()) == {}

    def test_exit_reason_counts_value_counts(self):
        df = pd.DataFrame({"exit_reason": ["STOP_HIT", "STOP_HIT", "T2_TARGET"]})
        assert exit_reason_counts(df) == {"STOP_HIT": 2, "T2_TARGET": 1}

    def test_setup_breakdown_empty(self):
        assert setup_breakdown(pd.DataFrame()) == ({}, {})

    def test_setup_breakdown_sums_filled_only(self):
        df = pd.DataFrame({
            "setup": ["SETUP_1_VCP", "SETUP_1_VCP", "SETUP_5_RESIDUAL_MOM"],
            "entry_price": [100.0, 100.0, 100.0],
            "realized_pnl": [1500.0, -500.0, 250.0],
        })
        pnl, counts = setup_breakdown(df)
        assert pnl == {"SETUP_1_VCP": 1000.0, "SETUP_5_RESIDUAL_MOM": 250.0}
        assert counts == {"SETUP_1_VCP": 2, "SETUP_5_RESIDUAL_MOM": 1}

    def test_setup_breakdown_skips_unfilled_rows(self):
        df = pd.DataFrame({
            "setup": ["SETUP_1_VCP", "SETUP_1_VCP"],
            "entry_price": [100.0, None],
            "realized_pnl": [100.0, 999.0],
        })
        pnl, counts = setup_breakdown(df)
        assert pnl == {"SETUP_1_VCP": 100.0}
        assert counts == {"SETUP_1_VCP": 1}

    def test_trades_per_year_empty_and_unfilled(self):
        assert trades_per_year(pd.DataFrame()) == {}
        df = pd.DataFrame({"entry_date": [None, None]})
        assert trades_per_year(df) == {}

    def test_trades_per_year_counts_sorted(self):
        df = pd.DataFrame({"entry_date": [date(2025, 3, 1), date(2026, 1, 5),
                                          date(2026, 2, 9), date(2026, 2, 20)]})
        assert trades_per_year(df) == {"2025": 1, "2026": 3}

    def test_win_stats_on_empty(self):
        ws = win_stats(pd.DataFrame())
        assert ws["trades"] == 0 and ws["win_rate"] == 0.0

    def test_win_stats_profit_factor_infinite_when_no_losses(self):
        df = pd.DataFrame({"entry_price": [10.0], "realized_pnl": [500.0]})
        ws = win_stats(df)
        assert ws["profit_factor"] == float("inf")
        assert ws["avg_loss_net"] == 0.0

    def test_win_stats_profit_factor_zero_when_no_pnl(self):
        df = pd.DataFrame({"entry_price": [10.0], "realized_pnl": [0.0]})
        assert win_stats(df)["profit_factor"] == 0.0

    def test_win_stats_hand_computed(self):
        df = pd.DataFrame({
            "entry_price": [10.0, 10.0, 10.0, 10.0],
            "realized_pnl": [300.0, -100.0, 200.0, -100.0],
        })
        ws = win_stats(df)
        assert ws["trades"] == 4
        assert ws["win_rate"] == pytest.approx(0.5)
        assert ws["profit_factor"] == pytest.approx(500.0 / 200.0)
        assert ws["avg_win_net"] == pytest.approx(250.0)
        assert ws["avg_loss_net"] == pytest.approx(-100.0)
        assert ws["expectancy_net"] == pytest.approx(75.0)


# ---------------------------------------------------------------------------
# Verdict formatting helpers
# ---------------------------------------------------------------------------

class TestFormatting:
    def test_fmt_bound_none_is_em_dash(self):
        assert _fmt_bound(None) == "—"

    def test_fmt_bound_modes(self):
        assert _fmt_bound(0.0523) == "+5.23%"
        assert _fmt_bound(-0.02) == "-2.00%"
        assert _fmt_bound(2.56, "pct") == "+2.56%"
        assert _fmt_bound(1.7, "plain") == "+1.70"

    def test_fmt_mode_by_key(self):
        assert _fmt_mode("profit_factor") == "plain"
        assert _fmt_mode("avg_win_net_pct") == "pct"
        assert _fmt_mode("win_rate") == "ratio_pct"

    def test_target_text_two_sided(self):
        assert _target_text(0.48, 0.56, "win_rate") == "+48.00% .. +56.00%"

    def test_target_text_one_sided(self):
        assert _target_text(0.14, None, "cagr_post_tax").startswith(">=")
        assert _target_text(None, -0.085, "other").startswith("<")

    def test_target_text_max_drawdown_is_0_to_bound(self):
        assert _target_text(None, -0.085, "max_drawdown") == "+0.00% .. -8.50%"


class TestVerdictDirections:
    def test_drawdown_closer_to_zero_passes(self):
        rows = {r["metric"]: r["verdict"]
                for r in brd_targets_verdict({"max_drawdown": -0.02, "trades": 5})}
        assert rows["Max Drawdown"] == "PASS"

    def test_drawdown_beyond_cap_fails(self):
        rows = {r["metric"]: r["verdict"]
                for r in brd_targets_verdict({"max_drawdown": -0.09, "trades": 5})}
        assert rows["Max Drawdown"] == "FAIL"

    def test_upper_bound_violation_fails(self):
        rows = {r["metric"]: r["verdict"]
                for r in brd_targets_verdict({"win_rate": 0.60, "trades": 5})}
        assert rows["Win Rate"] == "FAIL"

    def test_lower_bound_violation_fails(self):
        rows = {r["metric"]: r["verdict"]
                for r in brd_targets_verdict({"cagr_post_tax": 0.10, "trades": 5})}
        assert rows["Post-Tax CAGR"] == "FAIL"

    def test_verify_requires_rows(self):
        assert verify_brd_targets({}) is False
        assert verify_brd_targets({"trades": 1, "win_rate": 0.5}) is False


# ---------------------------------------------------------------------------
# compute_metrics on a synthetic BacktestResult
# ---------------------------------------------------------------------------

def _synthetic_result() -> SimpleNamespace:
    sessions = [date(2026, 1, 5) + timedelta(days=i) for i in range(6)]
    equity = pd.DataFrame({
        "date": sessions,
        "equity": [500_000.0, 501_000.0, 499_000.0, 502_000.0, 503_000.0,
                   502_500.0],
    })
    trades = pd.DataFrame({
        "symbol": ["AAA", "AAA", "BBB"],
        "setup": ["SETUP_1_VCP", "SETUP_1_VCP", "SETUP_5_RESIDUAL_MOM"],
        "entry_date": [sessions[1], sessions[2], sessions[3]],
        "entry_price": [100.0, 100.0, 200.0],
        "entry_cost": [12_000.0, 10_000.0, 20_000.0],
        "exit_reason": ["T2_TARGET", "STOP_HIT", "T1_TARGET"],
        "realized_pnl": [1200.0, -450.0, 300.0],
    })
    stcg = SimpleNamespace(tax_paid=150.0, fy_summary=[
        {"fy": "FY2025-26", "trade_pnl": 1050.0, "brought_forward": 0.0,
         "taxable": 1050.0, "tax": 210.0}])
    return SimpleNamespace(
        start=sessions[0], end=sessions[-1], equity=equity,
        trades=trades, stcg=stcg, regime_df=pd.DataFrame(),
        interest_paid=12.5, kill_count=0)


class TestComputeMetrics:
    @pytest.fixture()
    def metrics(self, tmp_path):
        # benchmark read goes through DuckDB; an empty store returns an empty
        # curve so benchmark fields are None — deterministic and offline.
        from nse_cash.data.storage import MarketStore
        store = MarketStore(tmp_path / "m.duckdb")
        try:
            yield compute_metrics(store, _synthetic_result())
        finally:
            store.close()

    def test_headline_fields(self, metrics):
        assert metrics["trades"] == 3
        assert metrics["win_rate"] == pytest.approx(2 / 3, abs=1e-3)  # rounded to 4dp
        assert metrics["final_equity"] == pytest.approx(502_500.0)
        assert metrics["total_tax"] == pytest.approx(150.0)
        assert metrics["total_interest"] == pytest.approx(12.5)
        assert metrics["kill_switches"] == 0
        assert metrics["benchmark_cagr"] is None  # empty store

    def test_post_tax_final_subtracts_stcg(self, metrics):
        assert metrics["post_tax_final_equity"] == pytest.approx(502_350.0)
        assert metrics["cagr_post_tax"] < metrics["cagr_pre_tax"]

    def test_flat_cash_curve_scores_zero_risk_free_ratios(self, metrics):
        assert metrics["sharpe"] > 0  # curve moves: ratios are real numbers
        assert metrics["sortino"] >= 0

    def test_aggregations_flow_through(self, metrics):
        assert metrics["exit_reason_counts"] == {"T2_TARGET": 1, "STOP_HIT": 1,
                                                 "T1_TARGET": 1}
        assert metrics["setup_pnl"]["SETUP_1_VCP"] == pytest.approx(750.0)
        assert metrics["setup_trades"]["SETUP_1_VCP"] == 2
        assert metrics["trades_per_year"] == {"2026": 3}
        assert metrics["monthly_returns"]  # keys YYYY-MM present
        assert metrics["fy_tax_summary"][0]["fy"] == "FY2025-26"

    def test_net_pct_rows_and_verdict_attached(self, metrics):
        # BRD pct rows are realized_pnl / entry_cost * 100 per filled row —
        # capital deployed (friction-inclusive buy value of the position),
        # NOT per-share entry_price (that inflates by the share count).
        # wins (1200/12000=10%, 300/20000=1.5%)*100 -> mean 5.75; loss
        # (-450/10000)*100 -> -4.5.
        assert metrics["avg_win_net_pct"] == pytest.approx(5.75)
        assert metrics["avg_loss_net_pct"] == pytest.approx(-4.5)
        # expectancy: (10.0 - 4.5 + 1.5) / 3 (rounded to 4dp by the metric)
        assert metrics["expectancy_net_pct"] == pytest.approx(7.0 / 3, abs=1e-3)
        assert len(metrics["brd_targets"]) == 7
        assert metrics["brd_all_pass"] is False  # synthetic numbers, no claim

    def test_zero_trade_run_reports_zero_ratios(self, tmp_path):
        from nse_cash.data.storage import MarketStore
        res = _synthetic_result()
        res.trades = pd.DataFrame({
            "symbol": [], "setup": [], "entry_date": [], "entry_price": [],
            "exit_reason": [], "realized_pnl": []})
        store = MarketStore(tmp_path / "m2.duckdb")
        try:
            m = compute_metrics(store, res)
        finally:
            store.close()
        assert m["trades"] == 0
        assert m["sharpe"] == 0.0 and m["sortino"] == 0.0
        assert m["avg_win_net_pct"] is None
        assert m["exit_reason_counts"] == {}
        rows = {r["metric"]: r["verdict"] for r in m["brd_targets"]}
        # Per-trade rows are MISSING; curve-level rows still get judged.
        assert rows["Avg Win (net %)"] == "MISSING"
        assert rows["Avg Loss (net %)"] == "MISSING"
        assert rows["Expectancy (net %)"] == "MISSING"
        assert rows["Max Drawdown"] in {"PASS", "FAIL"}
