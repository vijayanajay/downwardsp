"""Phase 1 unit tests: config engine and domain types."""

from __future__ import annotations

import pytest


@pytest.fixture()
def pkg():
    import sys
    sys.path.insert(0, "src")
    from nse_cash.core import config as cfg_mod
    import importlib
    importlib.reload(cfg_mod)
    return cfg_mod


def test_config_defaults_load(pkg):
    cfg = pkg.load_config()
    assert cfg.capital.base_capital == 500_000.0
    assert cfg.capital.num_slots == 4
    assert cfg.capital.slot_capital == 125_000.0
    assert cfg.capital.tranche_capital == 62_500.0
    assert cfg.risk.max_structural_stop == 0.022
    assert cfg.risk.max_gap_entry == 0.012
    assert cfg.risk.stall_threshold == 0.008
    assert cfg.risk.max_holding_days == 5
    assert cfg.risk.kill_switch_drawdown == 0.075
    assert cfg.friction.stt_delivery == 0.001
    assert cfg.friction.nse_turnover == 0.0000345
    assert cfg.friction.sebi_fee == 0.000001
    assert cfg.friction.stamp_duty == 0.00015
    assert cfg.friction.gst_rate == 0.18
    assert cfg.friction.dp_charge_per_sell == 15.93
    assert cfg.friction.slippage_per_side == 0.0005
    assert cfg.friction.stcg_tax_rate == 0.20
    assert str(cfg.paths.raw_dir).replace("\\", "/") == "data/raw"
    assert str(cfg.paths.duckdb_path).replace("\\", "/") == "data/db/nse_market.duckdb"
    assert (str(cfg.paths.ledger_db_path).replace("\\", "/")
            == "data/db/portfolio_ledger.sqlite3")


def test_config_yaml_override(pkg, tmp_path):
    yaml_file = tmp_path / "cfg.yaml"
    yaml_file.write_text(
        "capital:\n  base_capital: 1000000.0\n  slot_capital: 250000.0\n"
        "  tranche_capital: 125000.0\n  num_slots: 4\n")
    cfg = pkg.load_config(yaml_file)
    assert cfg.capital.base_capital == 1_000_000.0
    assert cfg.capital.slot_capital == 250_000.0


def test_config_rejects_bad_partition(pkg):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        pkg.SystemConfig(**{"capital": {"base_capital": 999.0}})


def test_env_overrides(pkg, monkeypatch):
    monkeypatch.setenv("NSE_CASH_CAPITAL__BASE_CAPITAL", "750000")
    monkeypatch.setenv("NSE_CASH_CAPITAL__SLOT_CAPITAL", "187500")
    monkeypatch.setenv("NSE_CASH_CAPITAL__TRANCHE_CAPITAL", "93750")
    monkeypatch.setenv("NSE_CASH_RISK__MAX_STRUCTURAL_STOP", "0.03")
    monkeypatch.setenv("NSE_CASH_PATHS__DUCKDB_PATH", "data/db/other.duckdb")
    cfg = pkg.load_config()
    assert cfg.capital.base_capital == 750_000.0
    assert cfg.capital.slot_capital == 187_500.0
    assert cfg.capital.tranche_capital == 93_750.0
    assert cfg.risk.max_structural_stop == 0.03
    assert str(cfg.paths.duckdb_path).replace("\\", "/") == "data/db/other.duckdb"


def test_env_override_base_capital_only(pkg, monkeypatch):
    """Overriding only BASE_CAPITAL automatically rebalances slots and tranches without error."""
    monkeypatch.setenv("NSE_CASH_CAPITAL__BASE_CAPITAL", "1000000")
    cfg = pkg.load_config()
    assert cfg.capital.base_capital == 1_000_000.0
    assert cfg.capital.slot_capital == 250_000.0
    assert cfg.capital.tranche_capital == 125_000.0


def test_domain_enums():
    from nse_cash.core.types import (ExitReason, MarketRegimeState, SeriesType,
                                     SetupID, TrancheID, TrancheState)
    assert SeriesType.EQ.value == "EQ"
    assert MarketRegimeState.OFFENSIVE_LONG.value == "OFFENSIVE_LONG"
    assert MarketRegimeState.DEFENSIVE_CASH.value == "DEFENSIVE_CASH"
    assert len(SetupID) == 5
    assert len(TrancheID) == 2
    assert len(TrancheState) == 6
    assert len(ExitReason) == 7


def test_daily_bar_model():
    from nse_cash.core.types import DailyBar
    bar = DailyBar(symbol="TCS", date="2026-09-10", open=1.0, high=2.0,
                   low=0.9, close=1.5, volume=100)
    assert bar.series == "EQ"
    assert bar.open_adj is None
