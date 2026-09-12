"""Central configuration engine (Phase 1.3).

YAML file -> Pydantic SystemConfig with strict validation, then environment
variable overrides of the form NSE_CASH_<SECTION>__<KEY> (double underscore).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field, model_validator

_DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "config.yaml"


class CapitalConfig(BaseModel):
    base_capital: float = 500_000.0
    num_slots: int = 4
    slot_capital: float = 125_000.0
    tranche_capital: float = 62_500.0

    @model_validator(mode="after")
    def _check_partition(self) -> "CapitalConfig":
        if abs(self.num_slots * self.slot_capital - self.base_capital) > 1.0:
            raise ValueError("num_slots * slot_capital must equal base_capital")
        if abs(self.slot_capital / 2 - self.tranche_capital) > 0.5:
            raise ValueError("slot_capital / 2 must equal tranche_capital")
        return self


class RiskConfig(BaseModel):
    max_structural_stop: float = 0.022
    max_gap_entry: float = 0.012
    stall_threshold: float = 0.008
    max_holding_days: int = 5
    kill_switch_drawdown: float = 0.075


class FrictionConfig(BaseModel):
    stt_delivery: float = 0.001
    nse_turnover: float = 0.0000345
    sebi_fee: float = 0.000001
    stamp_duty: float = 0.00015
    gst_rate: float = 0.18
    dp_charge_per_sell: float = 15.93
    slippage_per_side: float = 0.0005
    stcg_tax_rate: float = 0.20


class PathsConfig(BaseModel):
    raw_dir: Path = Path("data/raw")
    parquet_dir: Path = Path("data/processed")
    duckdb_path: Path = Path("data/db/nse_market.duckdb")
    ledger_db_path: Path = Path("data/db/portfolio_ledger.sqlite3")


class SystemConfig(BaseModel):
    capital: CapitalConfig = CapitalConfig()
    risk: RiskConfig = RiskConfig()
    friction: FrictionConfig = FrictionConfig()
    paths: PathsConfig = PathsConfig()

    model_config = {"validate_assignment": True}


def _coerce(raw: str, current: object) -> object:
    if isinstance(current, Path):
        return Path(raw)
    if isinstance(current, bool):
        return raw.lower() in ("1", "true", "yes")
    if isinstance(current, int):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    return raw


def _apply_env_overrides(config: SystemConfig) -> None:
    """Apply NSE_CASH_<SECTION>__<KEY> environment overrides in place.

    All overrides for a section are batched into a single re-validation so
    cross-field invariants (e.g. num_slots * slot_capital == base_capital) see
    only the final state, never a partially-overridden intermediate.
    """
    for section in ("capital", "risk", "friction", "paths"):
        sub = getattr(config, section)
        attrs = type(sub).model_fields
        overrides: dict[str, object] = {}
        for key in attrs:
            env = f"NSE_CASH_{section.upper()}__{key.upper()}"
            if env in os.environ:
                overrides[key] = _coerce(os.environ[env], getattr(sub, key))
        if overrides:
            if section == "capital" and "base_capital" in overrides and "slot_capital" not in overrides:
                new_base = float(overrides["base_capital"])
                new_num = int(overrides.get("num_slots", sub.num_slots))
                overrides["slot_capital"] = new_base / new_num
                if "tranche_capital" not in overrides:
                    overrides["tranche_capital"] = overrides["slot_capital"] / 2.0
            setattr(config, section, {**sub.model_dump(), **overrides})


def load_config(path: Optional[Path] = None) -> SystemConfig:
    """Load SystemConfig from YAML (defaults to config/config.yaml) + env overrides."""
    file = path or _DEFAULT_CONFIG
    data: dict = {}
    if Path(file).exists():
        data = yaml.safe_load(Path(file).read_text()) or {}
    config = SystemConfig(**data)
    _apply_env_overrides(config)
    return config
