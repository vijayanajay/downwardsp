"""Funnel gates: market regime, sector diversification, pre-entry risk & capacity."""

from nse_cash.funnel.market_regime import (MarketRegimeResult,
                                           compute_nifty50_ema,
                                           compute_nifty500_breadth,
                                           evaluate_market_regime,
                                           evaluate_market_regime_range)
from nse_cash.funnel.sector_gate import SectorGate
from nse_cash.funnel.stage4_gate import (Stage4Decision,
                                         check_gap_invalidation,
                                         check_portfolio_capacity,
                                         check_structural_risk,
                                         evaluate_stage4)

__all__ = [
    "MarketRegimeResult",
    "compute_nifty50_ema",
    "compute_nifty500_breadth",
    "evaluate_market_regime",
    "evaluate_market_regime_range",
    "SectorGate",
    "Stage4Decision",
    "check_portfolio_capacity",
    "check_structural_risk",
    "check_gap_invalidation",
    "evaluate_stage4",
]
