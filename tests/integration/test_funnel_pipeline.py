"""Integration tests for Funnel Stages 1, 2, 4 and the `nse-cash scan` CLI pipeline."""

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest
from click.testing import CliRunner

from nse_cash.cli.main import cli
from nse_cash.cli.scan_cmd import run_scan
from nse_cash.core.config import SystemConfig
from nse_cash.core.types import MarketRegimeState
from nse_cash.data.storage import MarketStore
from nse_cash.funnel.market_regime import evaluate_market_regime
from nse_cash.funnel.sector_gate import SectorGate
from nse_cash.funnel.stage4_gate import evaluate_stage4


@pytest.fixture
def populated_store(tmp_path):
    """Fixture providing a temporary store populated with index and stock data."""
    db_path = tmp_path / "test_funnel.duckdb"
    store = MarketStore(db_path, read_only=False)

    start = date(2026, 9, 1)
    dates = [start + timedelta(days=i) for i in range(5)]

    # 1. NIFTY 50 index data
    nifty_rows = []
    for i, d in enumerate(dates):
        c = 24000.0 + i * 100.0
        nifty_rows.append({
            "index_name": "NIFTY 50",
            "date": d,
            "open": c, "high": c, "low": c, "close": c,
            "volume": 10000.0,
        })
    store.upsert_market_indices(pd.DataFrame(nifty_rows))

    # 2. Daily bars for a couple stocks
    bars = []
    for s in ["INFY", "TCS", "HDFCBANK"]:
        for i, d in enumerate(dates):
            c = 1500.0 + i * 10.0
            bars.append({
                "symbol": s,
                "date": d,
                "series": "EQ",
                "open": c, "high": c, "low": c, "close": c,
                "volume": 100000,
                "turnover": 150000000.0,
                "deliverable_qty": 50000,
                "delivery_pct": 50.0,
            })
    store.upsert_daily_bars(pd.DataFrame(bars))

    yield store
    store.close()


def test_funnel_pipeline_end_to_end(populated_store):
    """Verify market regime evaluation against populated store."""
    latest = populated_store.latest_date()
    assert latest is not None

    regime = evaluate_market_regime(populated_store, as_of_date=latest)
    assert regime.date == latest
    assert regime.state in (MarketRegimeState.OFFENSIVE_LONG, MarketRegimeState.DEFENSIVE_CASH)
    assert regime.nifty50_close > 0
    assert regime.nifty50_ema20 > 0
    assert 0.0 <= regime.breadth_pct <= 100.0

    # Test sector gate
    sg = SectorGate()
    assert sg.get_sector("INFY") in ("IT", "Information Technology")

    # Test Stage 4 gate
    cand = {"symbol": "INFY", "entry_ref": 1800.0, "structural_stop": 1770.0}
    dec = evaluate_stage4(cand, occupied_slots=1, active_sectors={"Auto"})
    assert dec.is_accepted is True


def test_scan_cli_renders_with_context(populated_store, tmp_path, capsys):
    """Verify run_scan executes cleanly against a populated store."""
    class MockCtx:
        obj = {
            "config": SystemConfig(
                paths={"duckdb_path": populated_store.db_path, "raw_dir": tmp_path, "parquet_dir": tmp_path, "ledger_db_path": tmp_path / "ledger.sqlite3"}
            )
        }

    run_scan(MockCtx(), trade_date_opt=populated_store.latest_date())
    # Verify no exception was raised and output completed


def test_scan_cli_command_with_config(populated_store, tmp_path):
    """Verify CLI `nse-cash --config <path> scan` runs cleanly."""
    config_file = tmp_path / "test_config.yaml"
    config_file.write_text(f"""
capital:
  base_capital: 500000.0
  num_slots: 4
  slot_capital: 125000.0
  tranche_capital: 62500.0
paths:
  duckdb_path: "{populated_store.db_path.as_posix()}"
  raw_dir: "{tmp_path.as_posix()}"
  parquet_dir: "{tmp_path.as_posix()}"
  ledger_db_path: "{(tmp_path / 'ledger.sqlite3').as_posix()}"
""", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli, ["--config", str(config_file), "scan"])
    assert result.exit_code == 0
    assert "Stage 1: Macro Market Regime Gate" in result.output
    assert "NIFTY 50" in result.output
    assert "20-EMA" in result.output
    assert "NIFTY 500 Breadth" in result.output
