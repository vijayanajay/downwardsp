"""Domain types and enums (Phase 1.5)."""

from __future__ import annotations

from datetime import date as Date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class SeriesType(str, Enum):
    """NSE trading series; system trades strictly cash `EQ`."""

    EQ = "EQ"
    BE = "BE"
    SM = "SM"


class MarketRegimeState(str, Enum):
    OFFENSIVE_LONG = "OFFENSIVE_LONG"
    DEFENSIVE_CASH = "DEFENSIVE_CASH"


class SetupID(str, Enum):
    SETUP_1_VCP = "SETUP_1_VCP"
    SETUP_2_RUBBERBAND = "SETUP_2_RUBBERBAND"
    SETUP_3_RS_BASE = "SETUP_3_RS_BASE"
    SETUP_4_ANCHOR_RETEST = "SETUP_4_ANCHOR_RETEST"
    SETUP_5_RESIDUAL_MOM = "SETUP_5_RESIDUAL_MOM"


class TrancheID(str, Enum):
    TRANCHE_1_BASE = "TRANCHE_1_BASE"
    TRANCHE_2_RUNNER = "TRANCHE_2_RUNNER"


class TrancheState(str, Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    TARGET_HIT = "TARGET_HIT"
    STOPPED_OUT = "STOPPED_OUT"
    STALL_EXITED = "STALL_EXITED"
    TIME_EXITED = "TIME_EXITED"


class ExitReason(str, Enum):
    TARGET_1_HIT = "TARGET_1_HIT"
    TARGET_2_HIT = "TARGET_2_HIT"
    TRAILING_STOP_HIT = "TRAILING_STOP_HIT"
    STRUCTURAL_STOP_HIT = "STRUCTURAL_STOP_HIT"
    STALL_48H_HIT = "STALL_48H_HIT"
    TIME_DAY5_HIT = "TIME_DAY5_HIT"
    KILL_SWITCH = "KILL_SWITCH"
    END_OF_RUN = "END_OF_RUN"   # simulation-only: force close at end of data


class ActionType(str, Enum):
    SPLIT = "SPLIT"
    BONUS = "BONUS"
    RIGHTS = "RIGHTS"
    DIVIDEND = "DIVIDEND"
    DEMERGER = "DEMERGER"


class DailyBar(BaseModel):
    """One (symbol, date) cash-market bar joined with delivery data."""

    symbol: str
    date: Date
    series: str = "EQ"
    open: float
    high: float
    low: float
    close: float
    last: Optional[float] = None
    volume: int = 0
    turnover: float = 0.0
    deliverable_qty: Optional[int] = None
    delivery_pct: Optional[float] = None

    # backward-adjusted columns (Phase 3.1); None until adjuster has run
    open_adj: Optional[float] = None
    high_adj: Optional[float] = None
    low_adj: Optional[float] = None
    close_adj: Optional[float] = None
    volume_adj: Optional[float] = None
    delivery_adj: Optional[float] = None


class DeliveryRecord(BaseModel):
    symbol: str
    date: Date
    series: str = "EQ"
    traded_qty: int
    deliverable_qty: int
    delivery_pct: float


class CorporateAction(BaseModel):
    symbol: str
    series: Optional[str] = None
    ex_date: Date
    purpose: str
    action_type: ActionType
    ratio_a: float = 1.0
    ratio_b: float = 1.0
    adjustment_factor: Optional[float] = None


class CandidateSignal(BaseModel):
    """Output of Stage 3 setup evaluation for one symbol on Day T."""

    symbol: str
    date: Date
    setup: SetupID
    entry_ref: float                 # raw Close_T
    structural_stop: float           # raw price level
    structural_stop_pct: float       # (entry - stop) / entry
    max_stop_pct: float = 0.022      # setup's own stop gate (Setup 4: 2.00%)
    tranche1_target_pct: float = 0.02
    tranche2_target_pct: float = 0.06
    s_runner: float = 0.0
    delivery_z: float = 0.0
    imom_percentile: float = 0.0
    pv_percentile: float = 1.0


class TradeOrder(BaseModel):
    """One tradable order derived from a CandidateSignal (10:00 AM sheet line)."""

    symbol: str
    action: str = "BUY"
    entry_ref: float
    max_entry_price: float = Field(description="Entry_Ref * (1 + max_gap_entry)")
    quantity: int
    tranche1_qty: int
    tranche2_qty: int
    tranche1_target: float
    tranche2_target: float
    structural_stop: float
    sector: Optional[str] = None


class PortfolioPosition(BaseModel):
    trade_id: str
    symbol: str
    sector: Optional[str] = None
    entry_date: Date
    entry_price: float
    quantity: int
    tranche1_qty: int
    tranche2_qty: int
    tranche1_target: float
    tranche2_target: float
    structural_stop: float
    breakeven_armed: bool = False
    entry_day_index: int = 0


class BacktestMetrics(BaseModel):
    start_date: Date
    end_date: Date
    trades: int
    win_rate: float
    profit_factor: float
    cagr_post_tax: float
    max_drawdown: float
    max_drawdown_days: int
    avg_win_net: float
    avg_loss_net: float
    expectancy_net: float
    sharpe: float
    sortino: float
    final_equity: float
    # Phase 6 tear-sheet extensions (None when not computable)
    cagr_pre_tax: Optional[float] = None
    post_tax_final_equity: Optional[float] = None
    total_tax: Optional[float] = None
    total_interest: Optional[float] = None
    benchmark_cagr: Optional[float] = None
    benchmark_max_drawdown: Optional[float] = None
    kill_switches: int = 0
    gap_rejections: int = 0
    exit_reason_counts: dict[str, int] = Field(default_factory=dict)
    setup_pnl: dict[str, float] = Field(default_factory=dict)
    setup_trades: dict[str, int] = Field(default_factory=dict)
    trades_per_year: dict[str, int] = Field(default_factory=dict)
    monthly_returns: dict[str, float] = Field(default_factory=dict)
