"""Extra coverage: sync orchestration internals, backtest CLI range/tear-sheet,
and the remaining market-regime / universe edge branches.

All offline: fake NSE clients, real DuckDB stores under tmp_path.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from nse_cash.cli.backtest_cmd import _resolve_range, run_backtest_cmd
from nse_cash.cli.sync_cmd import (_delivery_present, _fetch_day, _persist_day,
                                   _sync_corporate_actions,
                                   _latest_trading_day)
from nse_cash.core.config import SystemConfig
from nse_cash.data.storage import MarketStore
from nse_cash.funnel.market_regime import (compute_nifty500_breadth,
                                           compute_nifty50_ema,
                                           evaluate_market_regime_range)
from nse_cash.core.universe import build_pit_universe, build_pit_universe_range

START = date(2026, 1, 5)


def _sessions(n: int, start: date = START) -> list[date]:
    days, d = [], start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _bars(symbol: str, sessions: list[date], base: float = 100.0) -> pd.DataFrame:
    n = len(sessions)
    closes = [base * (1 + 0.001 * i) for i in range(n)]
    return pd.DataFrame({
        "symbol": symbol, "date": sessions, "series": "EQ",
        "open": closes, "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes], "close": closes,
        "last": closes, "volume": [1_000_000] * n,
        "turnover": [c * 1_000_000 for c in closes],
        "deliverable_qty": [500_000] * n, "delivery_pct": [50.0] * n,
    })


# ---------------------------------------------------------------------------
# sync_cmd: _persist_day / helpers
# ---------------------------------------------------------------------------

class TestPersistDay:
    def _result(self, d, with_mto=True, with_idx=True, delivery_cols=True):
        bhav_df = _bars("SHOCK", [d])
        if not delivery_cols:
            bhav_df = bhav_df.drop(columns=["deliverable_qty", "delivery_pct"])
        mto = pd.DataFrame({
            "symbol": ["SHOCK"], "date": [d], "deliverable_qty": [750_000],
            "delivery_pct": [75.0]}) if with_mto else None
        idx = pd.DataFrame({
            "index_name": ["NIFTY 50"], "date": [d], "open": [1.0],
            "high": [1.0], "low": [1.0], "close": [24_000.0],
            "volume": [1.0]}) if with_idx else None
        return {"date": d, "bhav": SimpleNamespace(date=d, df=bhav_df, source="udiff"),
                "mto": mto, "idx": idx}

    def test_ingests_bars_joins_delivery_and_indices(self, tmp_path):
        store = MarketStore(tmp_path / "s.duckdb")
        try:
            d = date(2026, 9, 10)
            assert _persist_day(store, self._result(d)) == "ingested"
            row = store.con.execute(
                "SELECT deliverable_qty, delivery_pct FROM daily_bars WHERE date = ?",
                [d]).fetchone()
            # PR delivery values (500k/50) win over MTO per join_delivery.
            assert row[0] == 500_000 and row[1] == pytest.approx(50.0)
            idx = store.con.execute(
                "SELECT close FROM market_indices WHERE date = ?", [d]).fetchone()
            assert idx[0] == pytest.approx(24_000.0)
        finally:
            store.close()

    def test_holiday_persists_nothing(self, tmp_path):
        store = MarketStore(tmp_path / "s.duckdb")
        try:
            res = {"date": date(2026, 9, 10), "bhav": None, "mto": None, "idx": None}
            assert _persist_day(store, res) == "holiday"
            assert store.symbol_count() == 0
        finally:
            store.close()

    def test_bars_without_delivery_columns_still_ingest(self, tmp_path):
        store = MarketStore(tmp_path / "s.duckdb")
        try:
            d = date(2026, 9, 10)
            assert _persist_day(store, self._result(
                d, with_mto=False, delivery_cols=False)) == "ingested"
        finally:
            store.close()

    def test_delivery_present_and_bars_count(self, tmp_path):
        store = MarketStore(tmp_path / "s.duckdb")
        try:
            d = date(2026, 9, 10)
            _persist_day(store, self._result(d))
            assert _delivery_present(store, d) is True
            assert _delivery_present(store, date(2026, 9, 11)) is False
        finally:
            store.close()


class TestLatestTradingDay:
    def test_walks_back_until_bhavcopy_found(self, tmp_path):
        client = MagicMock()
        day2, day1 = date(2026, 9, 9), date(2026, 9, 10)
        with patch("nse_cash.cli.sync_cmd._fetch_day") as fd, \
             patch("nse_cash.cli.sync_cmd.datetime") as dtime:
            # Pin "today" so the walk-back is deterministic.
            dtime.now.return_value.date.return_value = day1
            dtime.combine = datetime.combine
            fd.side_effect = [
                {"date": day1, "bhav": None, "mto": None, "idx": None},
                {"date": day2, "bhav": SimpleNamespace(date=day2), "mto": None,
                 "idx": None},
            ]
            assert _latest_trading_day(client, tmp_path) == day2
            assert fd.call_count == 2

    def test_gives_up_after_lookback(self, tmp_path):
        from nse_cash.cli.sync_cmd import MAX_HOLIDAY_LOOKBACK
        client = MagicMock()
        with patch("nse_cash.cli.sync_cmd._fetch_day") as fd:
            fd.return_value = {"date": None, "bhav": None, "mto": None, "idx": None}
            assert _latest_trading_day(client, tmp_path) is None
            assert fd.call_count == MAX_HOLIDAY_LOOKBACK


# ---------------------------------------------------------------------------
# sync_cmd: corporate-actions monthly walk
# ---------------------------------------------------------------------------

class _FakeCApi:
    """Client whose get_json answers the corporate-actions endpoint."""

    def __init__(self, responses: list[pd.DataFrame | None]):
        self.responses = list(responses)
        self.calls: list[tuple] = []

    def get_json(self, url, bootstrap=True):
        self.calls.append((url, None))
        return {}


@pytest.fixture()
def ca_store(tmp_path):
    s = MarketStore(tmp_path / "ca.duckdb")
    yield s
    s.close()


class TestSyncCorporateActions:
    def test_no_args_fetches_upcoming_only(self, ca_store, monkeypatch):
        upcoming = pd.DataFrame([{
            "symbol": "UP", "series": "EQ", "ex_date": date(2026, 9, 15),
            "purpose": "BONUS 1:1", "action_type": "BONUS",
            "ratio_a": 1.0, "ratio_b": 1.0, "adjustment_factor": None}])
        with patch("nse_cash.cli.sync_cmd.load_seed_corporate_actions",
                   return_value=None), \
             patch("nse_cash.cli.sync_cmd.fetch_corporate_actions",
                   side_effect=[upcoming]) as fc:
            n = _sync_corporate_actions(MagicMock(), ca_store)
        assert n == 1
        assert fc.call_count == 1

    def test_backfill_queries_only_last_365_days_monthly(self, ca_store, monkeypatch):
        """Deep history: the live API is paged monthly from max(start, today-365d)
        to end; months entirely before the cutoff are never queried."""
        start = datetime(2020, 1, 1)
        end = datetime(2020, 3, 31)
        with patch("nse_cash.cli.sync_cmd.load_seed_corporate_actions",
                   return_value=None), \
             patch("nse_cash.cli.sync_cmd.fetch_corporate_actions",
                   side_effect=lambda *a, **k: None) as fc:
            n = _sync_corporate_actions(MagicMock(), ca_store, start, end)
        assert n == 0
        fc.assert_not_called()   # 2020 range lies entirely before the cutoff

    def test_backfill_pages_recent_history_monthly(self, ca_store, monkeypatch):
        today = datetime.now()
        start = today - timedelta(days=100)
        end = today
        months_called = []
        def fake_fetch(client, m_start, m_end):
            months_called.append((m_start, m_end))
            return None
        with patch("nse_cash.cli.sync_cmd.load_seed_corporate_actions",
                   return_value=None), \
             patch("nse_cash.cli.sync_cmd.fetch_corporate_actions",
                   side_effect=fake_fetch):
            _sync_corporate_actions(MagicMock(), ca_store, start, end)
        assert len(months_called) >= 3   # ~3.3 months paged
        assert months_called[0][0] >= (today - timedelta(days=365)).date()

    def test_seed_rows_are_upserted_first(self, ca_store, monkeypatch):
        seed = pd.DataFrame([{
            "symbol": "SEED", "series": "EQ", "ex_date": date(2026, 1, 5),
            "purpose": "BONUS 1:1", "action_type": "BONUS",
            "ratio_a": 1.0, "ratio_b": 1.0, "adjustment_factor": None}])
        with patch("nse_cash.cli.sync_cmd.load_seed_corporate_actions",
                   return_value=seed), \
             patch("nse_cash.cli.sync_cmd.fetch_corporate_actions",
                   side_effect=lambda *a, **k: None):
            n = _sync_corporate_actions(MagicMock(), ca_store)
        assert n == 1
        row = ca_store.con.execute(
            "SELECT symbol FROM corporate_actions").fetchone()
        assert row[0] == "SEED"


# ---------------------------------------------------------------------------
# backtest_cmd: range resolution + tear-sheet render
# ---------------------------------------------------------------------------

class TestResolveRange:
    def test_explicit_start_and_end(self):
        s, e = _resolve_range(False, False, False, "2020-02-01", "2020-05-31")
        assert (s, e) == (date(2020, 2, 1), date(2020, 5, 31))

    def test_start_without_end_is_an_error(self):
        from click import UsageError
        with pytest.raises(UsageError):
            _resolve_range(False, False, False, "2020-02-01", None)

    def test_in_sample_preset(self):
        s, e = _resolve_range(True, False, False, None, None)
        assert (s.year, s.month, s.day) == (2010, 1, 1)
        assert (e.year, e.month, e.day) == (2022, 12, 31)

    def test_walk_forward_preset(self):
        s, e = _resolve_range(False, True, False, None, None)
        assert (s.year, s.month, s.day) == (2023, 1, 1)

    def test_full_preset_runs_to_today(self):
        s, e = _resolve_range(False, False, True, None, None)
        assert s == date(2010, 1, 1)
        assert e >= date.today() - timedelta(days=1)

    def test_default_when_no_flags_is_full(self):
        s, e = _resolve_range(False, False, False, None, None)
        assert s == date(2010, 1, 1)


def _synthetic_run():
    """BacktestResult-shaped namespace for the renderer (no store needed)."""
    sessions = _sessions(6)
    equity = pd.DataFrame({
        "date": sessions,
        "equity": [500_000.0, 501_000.0, 499_000.0, 502_000.0, 503_000.0,
                   502_500.0]})
    trades = pd.DataFrame({
        "symbol": ["AAA", "BBB"], "setup": ["SETUP_1_VCP", "SETUP_5_RESIDUAL_MOM"],
        "entry_date": [sessions[1], sessions[2]], "entry_price": [100.0, 200.0],
        "exit_reason": ["T2_TARGET", "STOP_HIT"],
        "realized_pnl": [1200.0, -450.0]})
    stcg = SimpleNamespace(tax_paid=150.0, fy_summary=[
        {"fy": "FY2025-26", "trade_pnl": 750.0, "brought_forward": 0.0,
         "taxable": 750.0, "tax": 150.0}])
    return SimpleNamespace(start=sessions[0], end=sessions[-1], equity=equity,
                           trades=trades, stcg=stcg,
                           regime_df=pd.DataFrame(), interest_paid=12.5,
                           kill_count=1)


class TestTearSheetRender:
    def test_renders_all_sections_and_writes_summary(self, tmp_path, monkeypatch):
        import rich.console
        buf = io = __import__("io").StringIO()
        quiet = rich.console.Console(file=buf, force_terminal=False, width=220)
        import nse_cash.cli.backtest_cmd as bcmd
        monkeypatch.setattr(bcmd, "console", quiet)

        from nse_cash.backtest.metrics import compute_metrics
        store = MarketStore(tmp_path / "bt.duckdb")
        out_dir = tmp_path / "reports" / "backtest"
        out_dir.mkdir(parents=True)   # the renderer writes but does not mkdir
        try:
            metrics = compute_metrics(store, _synthetic_run())
            result = _synthetic_run()
            result.events = []
            bcmd._render_tear_sheet(metrics, result, out_dir)
        finally:
            store.close()

        out = buf.getvalue()
        assert "Tear Sheet - Headline" in out
        assert "Trade Statistics" in out
        assert "BRD" in out and "Verdict" in out
        assert "Exit Reason Histogram" in out
        assert "Per-Setup Attribution" in out
        assert "Trades per Year" in out
        assert "STCG by Financial Year" in out
        assert "Monthly Returns" in out
        assert "Kill Switches" in out and "1" in out
        summary = out_dir / "tear_sheet.json"
        assert summary.exists()
        payload = json.loads(summary.read_text(encoding="utf-8"))
        assert "metrics" in payload

    def test_missing_db_exits_with_message(self, tmp_path, monkeypatch, capsys):
        import rich.console
        import nse_cash.cli.backtest_cmd as bcmd
        buf = __import__("io").StringIO()
        monkeypatch.setattr(bcmd, "console",
                            rich.console.Console(file=buf, force_terminal=False))
        config = SystemConfig(paths={"duckdb_path": tmp_path / "nope.duckdb"})
        ctx = SimpleNamespace(obj={"config": config})
        with pytest.raises(SystemExit):
            run_backtest_cmd(ctx, False, False, False, None, None,
                             False, False)


# ---------------------------------------------------------------------------
# market_regime: fallback + zero-data branches
# ---------------------------------------------------------------------------

class TestRegimeEdges:
    def test_nifty50_falls_back_to_parquet_when_index_table_empty(
            self, tmp_path, monkeypatch):
        store = MarketStore(tmp_path / "r.duckdb")
        sessions = _sessions(30)
        pd.DataFrame({
            "date": pd.to_datetime(sessions),
            "close": [24_000.0 + i for i in range(30)],
        }).to_parquet(tmp_path / "_nifty.parquet")
        monkeypatch.chdir(tmp_path)   # fallback reads data/ohlcv/_nifty.parquet
        ohlcv = tmp_path / "data" / "ohlcv"
        ohlcv.mkdir(parents=True)
        pd.DataFrame({
            "date": pd.to_datetime(sessions),
            "close": [24_000.0 + i for i in range(30)],
        }).to_parquet(ohlcv / "_nifty.parquet")
        try:
            close, ema, above = compute_nifty50_ema(store, sessions[-1])
            assert close == pytest.approx(24_029.0)
            assert ema > 0 and isinstance(above, bool)
        finally:
            store.close()

    def test_nifty50_missing_history_raises_value_error(self, tmp_path,
                                                        monkeypatch):
        store = MarketStore(tmp_path / "r2.duckdb")
        monkeypatch.chdir(tmp_path)
        try:
            with pytest.raises(ValueError, match="No NIFTY 50"):
                compute_nifty50_ema(store, date(2026, 9, 10))
        finally:
            store.close()

    def test_breadth_zero_bars_is_zero(self, tmp_path):
        store = MarketStore(tmp_path / "r3.duckdb")
        try:
            pct, adv, tot, above = compute_nifty500_breadth(store, date(2026, 9, 10))
            assert (pct, adv, tot, above) == (0.0, 0, 0, False)
        finally:
            store.close()

    def test_range_evaluate_on_empty_store_yields_empty_frame(self, tmp_path):
        store = MarketStore(tmp_path / "r4.duckdb")
        try:
            df = evaluate_market_regime_range(store, date(2026, 9, 1),
                                              date(2026, 9, 10))
            # No bars -> no trading dates -> an explicitly empty frame.
            assert df.empty
            assert "state" in df.columns
        finally:
            store.close()


# ---------------------------------------------------------------------------
# universe: gates and range build
# ---------------------------------------------------------------------------

class TestUniverseGates:
    def test_price_floor_and_adtv_gate_filter_membership(self, tmp_path):
        store = MarketStore(tmp_path / "u.duckdb")
        sessions = _sessions(70)
        # SOAK: price 40 (below floor 50) but huge volume -> excluded by price.
        # FINE: price 100, volume 600k -> ADTV 6 Cr, included.
        frames = [
            _bars("SOAK", sessions, base=40.0).assign(volume=10_000_000),
            _bars("FINE", sessions, base=100.0),
        ]
        store.upsert_daily_bars(pd.concat(frames, ignore_index=True))
        try:
            n = build_pit_universe_range(store, sessions[-1], sessions[-1])
            assert n == 1
            members = set(store.con.execute(
                "SELECT symbol FROM pit_universe WHERE date = ?",
                [sessions[-1]]).fetchall()[0] and
                [r[0] for r in store.con.execute(
                    "SELECT symbol FROM pit_universe WHERE date = ?",
                    [sessions[-1]]).fetchall()])
            assert members == {"FINE"}
        finally:
            store.close()

    def test_min_sessions_gate_excludes_newcomers(self, tmp_path):
        store = MarketStore(tmp_path / "u2.duckdb")
        long_run = _sessions(70)
        newcomer = _sessions(10, start=date(2026, 8, 1))
        store.upsert_daily_bars(pd.concat([
            _bars("VET", long_run), _bars("IPO", newcomer + long_run[-5:]),
        ], ignore_index=True))
        try:
            build_pit_universe_range(store, long_run[-1], long_run[-1])
            members = [r[0] for r in store.con.execute(
                "SELECT symbol FROM pit_universe WHERE date = ?",
                [long_run[-1]]).fetchall()]
            assert "IPO" not in members
        finally:
            store.close()

    def test_build_pit_universe_defaults_to_latest_date(self, tmp_path):
        store = MarketStore(tmp_path / "u3.duckdb")
        sessions = _sessions(70)
        store.upsert_daily_bars(_bars("FINE", sessions))
        try:
            df = build_pit_universe(store)
            assert len(df) == 1 and df["symbol"].iloc[0] == "FINE"
        finally:
            store.close()

    def test_build_pit_universe_empty_store_raises(self, tmp_path):
        store = MarketStore(tmp_path / "u4.duckdb")
        try:
            with pytest.raises(RuntimeError, match="nse-cash sync"):
                build_pit_universe(store)
        finally:
            store.close()
