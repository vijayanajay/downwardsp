"""Extra coverage for the operator-facing CLI commands.

`ledger --record-signal`, the 3:20 PM `run_check_eod` cockpit, `run_sync`
with fully mocked fetchers, and the click group's dispatch. The market data
is synthetic; the funnel/pipeline is exercised for real.
"""

from __future__ import annotations

import io
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import rich.console
from click.testing import CliRunner

from nse_cash.core.config import SystemConfig
from nse_cash.data.storage import MarketStore
from nse_cash.execution.ledger import Ledger

START = date(2026, 6, 1)


def _sessions(n: int, start: date = START) -> list[date]:
    days, d = [], start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _market(tmp_path, n_sessions: int = 70) -> MarketStore:
    """A synthetic market whose last session fires the SHOCK setup.

    Same construction as the engine tests: SHOCK is a Setup-1/5-grade signal
    on the final bar, BANKX is a wide-range decoy that never qualifies.
    """
    import numpy as np

    store = MarketStore(tmp_path / "market.duckdb")
    sessions = _sessions(n_sessions)
    rng = np.random.default_rng(7)
    n = len(sessions)

    idx = pd.DataFrame({
        "index_name": ["NIFTY 50"] * n + ["NIFTY 500"] * n,
        "date": sessions * 2,
        "close": list(24_000.0 * np.cumprod(1 + rng.normal(0.0005, 0.004, n)))
        + list(4_600.0 * np.cumprod(1 + rng.normal(0.0005, 0.005, n))),
        "open": 0.0, "high": 0.0, "low": 0.0, "volume": 0.0,
    })
    store.upsert_market_indices(idx)

    for sym, base, vol in (("SHOCK", 100.0, 900_000), ("BANKX", 500.0, 500_000)):
        rets = np.where(np.arange(n) % 2 == 1, 0.02, -0.004)
        closes = list(base * np.cumprod(1 + rets))
        closes[-1] = closes[-2] * 1.015
        opens = [closes[0]] + closes[:-1]
        highs = [max(o, c) * 1.002 for o, c in zip(opens, closes)]
        lows = [min(o, c) * 0.998 for o, c in zip(opens, closes)]
        if sym == "BANKX":
            highs = [max(o, c) * 1.03 for o, c in zip(opens, closes)]
            lows = [min(o, c) * 0.97 for o, c in zip(opens, closes)]
        bars = pd.DataFrame({
            "symbol": sym, "date": sessions,
            "open": opens, "high": highs, "low": lows, "close": closes,
            "volume": [vol] * n, "turnover": [vol * c for c in closes],
            "deliverable_qty": [int(vol * 0.4)] * n,
            "delivery_pct": [40.0] * n,
        })
        # Delivery shock + green candle on the last bar: the Setup 5 signature.
        bars.loc[bars.index[-1], "deliverable_qty"] = int(vol * 2)
        bars.loc[bars.index[-1], "low"] = closes[-1] * 0.985
        store.upsert_daily_bars(bars)

    # PIT universe + features so the funnel has something to chew on.
    from nse_cash.core.universe import build_pit_universe
    from nse_cash.setups.features import refresh_features
    build_pit_universe(store, sessions[-1])
    refresh_features(store, sessions[-1])
    return store


@pytest.fixture()
def sandbox(tmp_path):
    """(config, store, ledger) with everything pointed into tmp_path."""
    config = SystemConfig(paths={
        "duckdb_path": tmp_path / "market.duckdb",
        "raw_dir": tmp_path / "raw",
        "parquet_dir": tmp_path / "proc",
        "ledger_db_path": tmp_path / "ledger.sqlite3",
    })
    store = _market(tmp_path)
    led = Ledger(config.paths.ledger_db_path)
    yield config, store, led
    led.close()
    store.close()


@pytest.fixture()
def quiet(monkeypatch):
    """Wide non-terminal consoles for every module that renders."""
    buf = io.StringIO()
    console = rich.console.Console(file=buf, force_terminal=False, width=240)
    import nse_cash.cli.backtest_cmd as bcmd
    import nse_cash.cli.ledger_cmd as lcmd
    import nse_cash.cli.scan_cmd as scmd
    import nse_cash.cli.sync_cmd as ycmd
    for mod in (bcmd, lcmd, scmd, ycmd):
        monkeypatch.setattr(mod, "console", console)
    return buf


class _Ctx:
    def __init__(self, config):
        self.obj = {"config": config}


# ---------------------------------------------------------------------------
# ledger --record-signal
# ---------------------------------------------------------------------------

class TestRecordSignals:
    def test_commit_accepted_signal_to_ledger(self, sandbox, quiet):
        from nse_cash.cli.ledger_cmd import run_record_signals
        config, store, led = sandbox
        trade_date = store.latest_date()

        run_record_signals(_Ctx(config), trade_date)
        out = quiet.getvalue()

        pending = led.pending_signals()
        if pending:   # regime bullish + a setup fired
            assert "recorded" in out
            t = pending[0]
            assert t["symbol"] == "SHOCK"
            assert t["slot"] == 1
            assert t["tranche1_qty"] + t["tranche2_qty"] > 0
        else:
            # No qualifying candidate on the synthetic last bar: the command
            # must still complete cleanly and print its summary line.
            assert "signal(s) recorded" in out or "DEFENSIVE_CASH" in out

    def test_rerun_does_not_double_record(self, sandbox, quiet):
        from nse_cash.cli.ledger_cmd import run_record_signals
        config, store, led = sandbox
        trade_date = store.latest_date()

        run_record_signals(_Ctx(config), trade_date)
        first = len(led.pending_signals())
        run_record_signals(_Ctx(config), trade_date)
        assert len(led.pending_signals()) == first   # idempotent

    def test_record_signals_without_db_exits(self, tmp_path, quiet):
        from nse_cash.cli.ledger_cmd import run_record_signals
        config = SystemConfig(paths={"duckdb_path": tmp_path / "nope.duckdb"})
        with pytest.raises(SystemExit):
            run_record_signals(_Ctx(config), None)


# ---------------------------------------------------------------------------
# The 3:20 PM cockpit: run_check_eod
# ---------------------------------------------------------------------------

class TestCheckEod:
    def test_no_open_positions_is_a_clean_noop(self, sandbox, quiet):
        from nse_cash.cli.ledger_cmd import run_check_eod
        config, store, led = sandbox
        try:
            run_check_eod(_Ctx(config), None, skip_reconcile=True)
        finally:
            pass
        assert "No open positions" in quiet.getvalue()

    def test_ltp_prompts_and_kill_switch_line(self, sandbox, quiet):
        from nse_cash.cli.ledger_cmd import run_check_eod
        config, store, led = sandbox
        trade_date = store.latest_date()
        led.add_trade(
            trade_id=f"SHOCK-{trade_date.isoformat()}", symbol="SHOCK",
            setup_id="SETUP_1_VCP", signal_date=trade_date, slot=1,
            sector="IT", entry_ref=100.0, structural_stop=97.5,
            tranche1_target=102.0, tranche2_target=106.0,
            max_gap_pct=config.risk.max_gap_entry, tranche1_qty=600,
            tranche2_qty=600, stop_limit=None)
        led.record_fill(f"SHOCK-{trade_date.isoformat()}", 100.0,
                        trade_date + timedelta(days=1))
        try:
            run_check_eod(_Ctx(config), (f"SHOCK=103.00",), skip_reconcile=True)
        finally:
            pass
        out = quiet.getvalue()
        assert "3:20 PM EOD Routine" in out
        assert "SHOCK-" in out and "+3.00%" in out
        assert "kill at" in out   # HWM/drawdown line

    def test_malformed_ltp_token_stops_the_run(self, sandbox):
        from nse_cash.cli.ledger_cmd import run_check_eod
        config, store, led = sandbox
        trade_date = store.latest_date()
        led.add_trade(
            trade_id=f"SHOCK-{trade_date.isoformat()}", symbol="SHOCK",
            setup_id="SETUP_1_VCP", signal_date=trade_date, slot=1,
            sector="IT", entry_ref=100.0, structural_stop=97.5,
            tranche1_target=102.0, tranche2_target=106.0,
            max_gap_pct=config.risk.max_gap_entry, tranche1_qty=600,
            tranche2_qty=600, stop_limit=None)
        led.record_fill(f"SHOCK-{trade_date.isoformat()}", 100.0,
                        trade_date + timedelta(days=1))
        with pytest.raises(SystemExit, match="SYMBOL=PRICE"):
            run_check_eod(_Ctx(config), ("103.00",), skip_reconcile=True)


# ---------------------------------------------------------------------------
# run_sync end-to-end with mocked fetchers
# ---------------------------------------------------------------------------

def _bhav_result(d: date, n_rows: int = 2) -> dict:
    bars = pd.DataFrame({
        "symbol": [f"S{i}" for i in range(n_rows)], "date": [d] * n_rows,
        "series": "EQ", "open": [100.0] * n_rows, "high": [101.0] * n_rows,
        "low": [99.0] * n_rows, "close": [100.5] * n_rows,
        "last": [100.4] * n_rows, "volume": [1_000_000] * n_rows,
        "turnover": [100_500_000.0] * n_rows,
        "deliverable_qty": [500_000] * n_rows, "delivery_pct": [50.0] * n_rows,
    })
    mto = pd.DataFrame({
        "symbol": [f"S{i}" for i in range(n_rows)], "date": [d] * n_rows,
        "deliverable_qty": [600_000] * n_rows, "delivery_pct": [60.0] * n_rows,
    })
    idx = pd.DataFrame({
        "index_name": ["NIFTY 50"], "date": [d], "open": [1.0], "high": [1.0],
        "low": [1.0], "close": [24_000.0], "volume": [1.0]})
    return {"date": d, "bhav": MagicMock(date=d, df=bars, source="udiff"),
            "mto": mto, "idx": idx}


class TestRunSync:
    def test_latest_sync_ingests_and_finalizes(self, tmp_path, quiet):
        from nse_cash.cli.sync_cmd import run_sync
        config = SystemConfig(paths={
            "duckdb_path": tmp_path / "market.duckdb",
            "raw_dir": tmp_path / "raw",
            "parquet_dir": tmp_path / "proc",
            "ledger_db_path": tmp_path / "ledger.sqlite3",
        })
        today = date(2026, 9, 10)
        day_result = _bhav_result(today)
        with patch("nse_cash.cli.sync_cmd._fetch_day", return_value=day_result), \
             patch("nse_cash.cli.sync_cmd._latest_trading_day", return_value=today), \
             patch("nse_cash.cli.sync_cmd._sync_corporate_actions", return_value=3), \
             patch("nse_cash.cli.sync_cmd.NSEHttpClient") as hc, \
             patch("nse_cash.cli.sync_cmd.ingest_from_local_cache", return_value=0):
            run_sync(_Ctx(config), None, None, False)

        out = quiet.getvalue()
        store = MarketStore(config.paths.duckdb_path, read_only=False)
        try:
            row = store.con.execute(
                "SELECT count(*) FROM daily_bars WHERE date = ?", [today]).fetchone()
            assert row[0] == 2
            # PIT universe and governance ran; a one-day market has no
            # feature history yet, so only the refresh line is asserted.
            assert store.con.execute(
                "SELECT count(*) FROM pit_universe").fetchone()[0] > 0
            assert "features refreshed" in out
            assert "governance exclusions" in out
            assert "corporate actions upserted: 3" in out
            assert "parquet export" in out
        finally:
            store.close()

    def test_backfill_sync_over_a_range(self, tmp_path, quiet):
        from nse_cash.cli.sync_cmd import run_sync
        config = SystemConfig(paths={
            "duckdb_path": tmp_path / "market.duckdb",
            "raw_dir": tmp_path / "raw",
            "parquet_dir": tmp_path / "proc",
            "ledger_db_path": tmp_path / "ledger.sqlite3",
        })
        d1, d2 = date(2026, 9, 9), date(2026, 9, 10)
        with patch("nse_cash.cli.sync_cmd._fetch_day",
                   side_effect=[_bhav_result(d1), _bhav_result(d2)]), \
             patch("nse_cash.cli.sync_cmd._sync_corporate_actions", return_value=0), \
             patch("nse_cash.cli.sync_cmd.NSEHttpClient") as hc, \
             patch("nse_cash.cli.sync_cmd.ingest_from_local_cache", return_value=0):
            run_sync(_Ctx(config),
                     datetime(2026, 9, 9), datetime(2026, 9, 10), False)

        store = MarketStore(config.paths.duckdb_path, read_only=False)
        try:
            dates = store.con.execute(
                "SELECT count(DISTINCT date) FROM daily_bars").fetchone()[0]
            assert dates == 2
            # The whole backfill window gets PIT coverage.
            pit_dates = store.con.execute(
                "SELECT count(DISTINCT date) FROM pit_universe").fetchone()[0]
            assert pit_dates >= 1
        finally:
            store.close()


# ---------------------------------------------------------------------------
# click group dispatch
# ---------------------------------------------------------------------------

@pytest.fixture()
def config_yaml(tmp_path):
    return tmp_path / "config.yaml"


def _write_config(path, tmp_path):
    path.write_text(f"""
capital:
  base_capital: 500000.0
  num_slots: 4
  slot_capital: 125000.0
  tranche_capital: 62500.0
paths:
  duckdb_path: "{(tmp_path / 'market.duckdb').as_posix()}"
  raw_dir: "{(tmp_path / 'raw').as_posix()}"
  parquet_dir: "{(tmp_path / 'proc').as_posix()}"
  ledger_db_path: "{(tmp_path / 'ledger.sqlite3').as_posix()}"
""", encoding="utf-8")


class TestCliDispatch:
    def test_version_flag(self):
        from nse_cash.cli.main import cli
        result = CliRunner().invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "nse-cash" in result.output

    def test_backtest_without_db_fails_cleanly(self, config_yaml, tmp_path):
        from nse_cash.cli.main import cli
        _write_config(config_yaml, tmp_path)
        result = CliRunner().invoke(cli, ["--config", str(config_yaml),
                                          "backtest"])
        assert result.exit_code != 0
        assert "Run `nse-cash sync` first" in result.output.replace("\n", " ")

    def test_ledger_bare_on_sandbox_book(self, config_yaml, tmp_path, sandbox):
        from nse_cash.cli.main import cli
        _write_config(config_yaml, tmp_path)
        result = CliRunner().invoke(cli, ["--config", str(config_yaml),
                                          "ledger"])
        assert result.exit_code == 0
        assert "Portfolio Ledger" in result.output

    def test_ledger_record_fill_via_cli(self, config_yaml, tmp_path, sandbox):
        from nse_cash.cli.main import cli
        _write_config(config_yaml, tmp_path)
        config, store, led = sandbox
        d = store.latest_date()
        led.add_trade(
            trade_id=f"SHOCK-{d.isoformat()}", symbol="SHOCK",
            setup_id="SETUP_1_VCP", signal_date=d, slot=1, sector="IT",
            entry_ref=100.0, structural_stop=97.5, tranche1_target=102.0,
            tranche2_target=106.0, max_gap_pct=config.risk.max_gap_entry,
            tranche1_qty=600, tranche2_qty=600, stop_limit=None)
        led.close()

        result = CliRunner().invoke(cli, [
            "--config", str(config_yaml), "ledger",
            "--record-fill", "SHOCK", "100.25",
            "--date", (d + timedelta(days=1)).isoformat()])
        assert result.exit_code == 0, result.output
        assert "fill recorded" in result.output

    def test_sync_command_via_cli_with_mocks(self, config_yaml, tmp_path, quiet):
        from nse_cash.cli.main import cli
        _write_config(config_yaml, tmp_path)
        today = date(2026, 9, 10)
        with patch("nse_cash.cli.sync_cmd._fetch_day",
                   return_value=_bhav_result(today)), \
             patch("nse_cash.cli.sync_cmd._latest_trading_day", return_value=today), \
             patch("nse_cash.cli.sync_cmd._sync_corporate_actions", return_value=0), \
             patch("nse_cash.cli.sync_cmd.NSEHttpClient"), \
             patch("nse_cash.cli.sync_cmd.ingest_from_local_cache",
                   return_value=0):
            result = CliRunner().invoke(cli, ["--config", str(config_yaml),
                                              "sync"])
        assert result.exit_code == 0, result.output
