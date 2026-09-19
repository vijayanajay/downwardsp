"""Central configuration engine (Phase 1.3).

YAML file -> Pydantic SystemConfig with strict validation, then environment
variable overrides of the form NSE_CASH_<SECTION>__<KEY> (double underscore).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional

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


class CatalogConfig(BaseModel):
    """Per-setup on/off switches (CR-2026-003 Phase A.1) and setup geometry
    parameters (Phase A.3), read by the setup predicates (Phase B) and the
    Stage-4 risk-parity admission (Phase D).

    Each setup evaluates through the shared catalog; a disabled setup is
    skipped entirely (predicate never runs, funnel log shows nothing fired).
    Defaults implement CR-003 §3: Setup 2 (Rubber-Band Pullback) is pruned —
    measured -Rs 62,620 over 116 walk-forward trades (68% of trading losses);
    disabling it recovers +Rs 56.7k and lifts post-tax CAGR +1.93% -> +4.93%
    (experiments/cr002/ab_disable_setup2.json). The other four stay enabled
    pending their own A/B arms.

    Geometry flags (setup4_wide_geometry, setup1_pv_binding) and the
    Setup-5 risk-parity switch default OFF: the flag plumbing ships, the
    geometry changes only when an A/B arm flips the switch. Setup 5's
    structural anchor is ALREADY the un-clamped prev_low the CR's B.3 keeps,
    so its arm switch (setup5_use_risk_parity) emits only the risk-parity
    metadata Phase D.1 consumes. Value fields carry the CR-003 §A.3 literals
    so each arm is a one-line config change.
    """

    enable_setup1: bool = True
    enable_setup2: bool = False   # pruned per CR-2026-003 §3 (measured: 68% of losses)
    enable_setup3: bool = True
    enable_setup4: bool = True
    enable_setup5: bool = True

    # --- CR-2026-003 Phase A.3: Setup 4 target expansion (B.2) ---
    # All off by default: identical behavior until the A/B arm opts in.
    setup4_wide_geometry: bool = False
    setup4_tranche1_target: float = 0.035   # lifted from 0.020
    setup4_undercut_tolerance: float = 0.025  # retest low penetration, from 0.008
    setup4_max_stop_pct: float = 0.035      # structural gate for the wide arm, from 0.020

    # --- CR-2026-003 Phase A.3: Setup 5 risk parity (B.3, consumed by D.1) ---
    setup5_use_risk_parity: bool = False
    setup5_max_rupee_risk_pct: float = 0.022  # cap rupee stop risk at 2.2% of slot

    # --- CR-2026-003 Phase A.3: Setup 1 volatility binding (B.4) ---
    # False = historical behavior (narrow candle OR pv <= p15). True makes
    # pv_percentile <= 0.15 binding and adds the close >= sma20*0.99 floor.
    setup1_pv_binding: bool = False

    # --- CR-2026-003 X2: Setup 3 stop-gate parameter ---
    # The setup's own pre-entry stop gate (ranker filter). Default 0.022 =
    # the historical 2.20% bar; the X2 wide arm raises it so S3's natural
    # base lows survive the ranker. admission up to the widened gate then
    # needs risk.risk_parity_stops (the sizing/wide-stop switch).
    setup3_max_stop_pct: float = 0.022


class RankingConfig(BaseModel):
    """Ranker switches (CR-2026-003 Phase A.4 / E.1).

    enforce_s_runner_gate=False (default) demotes the S_runner >= 0.45 bar
    from a hard admission filter to an ordinal sort key only: the gate showed
    mixed selection value within setups (Setup 4's admitted cohort lost MORE
    than the rejected one — experiments/cr002/probe_output.txt P2b), and the
    realized book clusters just above the bar (in-book S_runner min 0.449,
    31.5% within [0.45, 0.50)) — an optimization artifact, not edge.
    Set True to restore the CR-001 behavior (hard admission filter).
    """

    enforce_s_runner_gate: bool = False


class FunnelConfig(BaseModel):
    """Stage-1 environment conditioning (CR-2026-003 Phase A.2, consumed by
    Phase C's breadth gate). breadth_offensive_min=0 disables the gate (the
    pre-CR-003 behavior); 0.75 means new entries require Nifty 500 breadth
    >= 75% of advancing constituents."""

    breadth_offensive_min: float = 0.0       # 0 = gate off (pre-CR-003 default)
    breadth_sizing_multiplier: float = 1.0   # optional slot-scaling knob (Phase C)


class RiskConfig(BaseModel):
    max_structural_stop: float = 0.022
    max_gap_entry: float = 0.012
    stall_threshold: float = 0.008
    max_holding_days: int = 5
    # CR-2026-003 experiment X1 (exit-geometry A/B; fill-model knobs):
    #   t1_enabled=False removes the +2% tranche guillotine — one full-size
    #   position rides to the T2 target / stop / time exit (no breakeven arm,
    #   no T1_TARGET event; a T2 exit books the FULL quantity).
    #   time_stop_enabled=False removes the Day-2 stall + Day-5 time exits —
    #   positions live only by stop/target. max_holding_days stays the sole
    #   time knob (e.g. 20 for the X1a 20-session-horizon arm).
    # Both default True = pre-X1 behavior, byte-identical.
    t1_enabled: bool = True
    time_stop_enabled: bool = True
    # CR-2026-003 X2 (risk-parity wide stops): when False (default) Stage 4
    # hard-walls every candidate at risk.max_structural_stop (2.20%). When
    # True, a candidate whose setup's own gate (max_stop_pct) is wider may
    # admit up to that gate, and the ENGINE sizes the position so rupee
    # stop-risk stays at max_structural_stop * slot_capital (fewer shares for
    # a wider stop — same rupees at risk, more room to breathe). Flag off =
    # legacy everywhere.
    risk_parity_stops: bool = False
    kill_switch_drawdown: float = 0.075
    # Trading sessions of zero-new-entries after a kill switch fires (plan 6.4).
    kill_cooldown_days: int = 10
    # Tranche-2 runner stop policy after a Tranche-1 fill. R3 A/B switch:
    #   "breakeven" -> stop moves to entry price at EOD (BRD default)
    #   "prev_low"  -> stop trails the prior session's low (Plan 6.2 literal)
    runner_trail: Literal["breakeven", "prev_low"] = "breakeven"
    # CR-2026-001 Issue 4: Kite GTT OCO stop LIMIT leg, as a fraction BELOW the
    # stop trigger. A sell limit AT the trigger goes unexecuted when the stock
    # gaps below it overnight; the limit leg guarantees a market-matching fill.
    # Measured: 5.06% of sessions gap down beyond -1.5% (clean 2010-2026 data).
    gtt_stop_limit_buffer: float = Field(default=0.015, ge=0.0, le=0.10)


class FrictionConfig(BaseModel):
    stt_delivery: float = 0.001
    nse_turnover: float = 0.0000345
    sebi_fee: float = 0.000001
    stamp_duty: float = 0.00015
    gst_rate: float = 0.18
    dp_charge_per_sell: float = 15.93
    slippage_per_side: float = 0.0005
    stcg_tax_rate: float = 0.20
    # STCG loss carry-forward (business-loss set-off, 8-FY cap): the legal
    # behavior and the backtest default. False reproduces the plan's flat
    # per-FY rule that drops loss years.
    stcg_carry_forward: bool = True


class PathsConfig(BaseModel):
    raw_dir: Path = Path("data/raw")
    parquet_dir: Path = Path("data/processed")
    duckdb_path: Path = Path("data/db/nse_market.duckdb")
    ledger_db_path: Path = Path("data/db/portfolio_ledger.sqlite3")


class SystemConfig(BaseModel):
    capital: CapitalConfig = CapitalConfig()
    risk: RiskConfig = RiskConfig()
    friction: FrictionConfig = FrictionConfig()
    catalog: CatalogConfig = CatalogConfig()
    ranking: RankingConfig = RankingConfig()
    funnel: FunnelConfig = FunnelConfig()
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
    for section in ("capital", "risk", "friction", "catalog", "ranking",
                    "funnel", "paths"):
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
