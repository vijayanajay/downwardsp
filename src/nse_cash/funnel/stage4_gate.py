"""Pre-Entry Structural Risk & Capacity Controller (Phase 4.3).

Implements Funnel Stage 4 pre-entry constraints:
  1. Portfolio Capacity Gate: Maximum 4 concurrent positions. If all 4 slots are occupied, abstain from new entries.
  2. Pre-Entry Max-Risk Gate: Structural risk strictly <= 2.20%.
       Structural_Risk_Pct = (Entry_Ref - Structural_Stop) / Entry_Ref
       If Structural_Risk_Pct > 2.20% => Candidate is strictly disqualified.
  3. 10:00 AM Gap Invalidation Rule:
       If Price_10:00_AM > Close_T * (1 + 0.012) => Signal Cancelled (Reject trade).
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Set, Tuple

from pydantic import BaseModel, Field

from nse_cash.core.config import SystemConfig, load_config
from nse_cash.funnel.sector_gate import SectorGate

log = logging.getLogger("nse_cash.stage4_gate")

DEFAULT_MAX_SLOTS = 4
DEFAULT_MAX_STRUCTURAL_STOP = 0.022  # 2.20%
DEFAULT_MAX_GAP_ENTRY = 0.012        # +1.20%


class Stage4Decision(BaseModel):
    """Decision output for a candidate evaluated through Stage 4 gates."""

    symbol: str
    is_accepted: bool
    rejection_reason: Optional[str] = None
    structural_risk_pct: float = Field(description="Structural risk as a decimal (e.g. 0.018 = 1.8%)")
    gap_pct: Optional[float] = Field(default=None, description="10:00 AM gap vs Close_T as a decimal")
    sector: Optional[str] = None


def check_portfolio_capacity(occupied_slots: int, num_slots: int = DEFAULT_MAX_SLOTS) -> Tuple[bool, str]:
    """Verify that portfolio has open capacity for new positions.

    Returns:
        (is_available, message)
    """
    if occupied_slots >= num_slots:
        return False, f"Portfolio capacity full ({occupied_slots}/{num_slots} slots occupied; 0 open slots)"
    open_slots = num_slots - occupied_slots
    return True, f"Capacity available ({open_slots} open slot(s) out of {num_slots})"


def check_structural_risk(
    entry_ref: float,
    structural_stop: float,
    max_risk_pct: float = DEFAULT_MAX_STRUCTURAL_STOP,
) -> Tuple[bool, float, str]:
    """Validate that the structural stop-loss is within the pre-entry risk limit (<= 2.20%).

    Returns:
        (is_valid, risk_pct, reason)
    """
    if entry_ref <= 0 or structural_stop <= 0:
        return False, 0.0, f"Invalid price values (entry={entry_ref}, stop={structural_stop})"

    if structural_stop >= entry_ref:
        return False, 0.0, f"Invalid long stop: structural stop {structural_stop:.2f} >= entry {entry_ref:.2f}"

    risk_pct = (entry_ref - structural_stop) / entry_ref
    # Allow 1e-7 tolerance for floating point representations
    if risk_pct > (max_risk_pct + 1e-7):
        return (
            False,
            risk_pct,
            f"Structural risk {risk_pct * 100:.2f}% exceeds maximum allowed {max_risk_pct * 100:.2f}% gate",
        )

    return (
        True,
        risk_pct,
        f"Structural risk {risk_pct * 100:.2f}% within {max_risk_pct * 100:.2f}% limit",
    )


def check_gap_invalidation(
    price_10am: float,
    close_t: float,
    max_gap_pct: float = DEFAULT_MAX_GAP_ENTRY,
) -> Tuple[bool, float, str]:
    """Check 10:00 AM market execution price against the opening gap-up ceiling (+1.20%).

    Returns:
        (is_valid, gap_pct, reason)
    """
    if close_t <= 0 or price_10am <= 0:
        return False, 0.0, f"Invalid price values (close_t={close_t}, price_10am={price_10am})"

    gap_pct = (price_10am - close_t) / close_t
    max_price = close_t * (1.0 + max_gap_pct)

    if price_10am > (max_price + 1e-7):
        return (
            False,
            gap_pct,
            f"10:00 AM price {price_10am:.2f} gapped up {gap_pct * 100:.2f}%, exceeding +{max_gap_pct * 100:.2f}% ceiling (max: {max_price:.2f})",
        )

    return (
        True,
        gap_pct,
        f"10:00 AM price {price_10am:.2f} gap {gap_pct * 100:.2f}% within +{max_gap_pct * 100:.2f}% ceiling",
    )


def evaluate_stage4(
    candidate: Any,
    occupied_slots: int,
    active_sectors: Set[str],
    sector_gate: Optional[SectorGate] = None,
    price_10am: Optional[float] = None,
    config: Optional[SystemConfig] = None,
) -> Stage4Decision:
    """Comprehensive Stage 4 evaluation for a candidate signal.

    Checks:
      1. Portfolio Capacity (occupied_slots < num_slots)
      2. Sector Diversification (candidate's sector not in active_sectors)
      3. Structural Stop Risk (<= 2.20%)
      4. 10:00 AM Gap Invalidation (price_10am <= Close_T * 1.012, if price_10am provided)
    """
    cfg = config or load_config()
    num_slots = cfg.capital.num_slots
    max_stop = cfg.risk.max_structural_stop
    max_gap = cfg.risk.max_gap_entry
    # CR-2026-003 X2 (risk.risk_parity_stops): in parity mode the hard global
    # wall yields to the candidate's own setup gate (max_stop_pct) — admission
    # up to the WIDER of the two — because the engine sizes shares so rupee
    # stop-risk stays at max_structural_stop * slot_capital. Candidates with a
    # narrower setup gate keep the tighter gate. Flag off = legacy wall.
    if getattr(cfg.risk, "risk_parity_stops", False):
        cand_gate = getattr(candidate, "max_stop_pct", None)
        if cand_gate is not None and float(cand_gate) > max_stop:
            max_stop = float(cand_gate)
    gate = sector_gate or SectorGate()

    sym = str(getattr(candidate, "symbol", "") or candidate.get("symbol", ""))
    entry_ref = float(getattr(candidate, "entry_ref", 0.0) or candidate.get("entry_ref", 0.0))
    structural_stop = float(getattr(candidate, "structural_stop", 0.0) or candidate.get("structural_stop", 0.0))
    sector = gate.get_sector(sym)

    # 1. Capacity Check
    cap_ok, cap_msg = check_portfolio_capacity(occupied_slots, num_slots=num_slots)
    if not cap_ok:
        return Stage4Decision(
            symbol=sym,
            is_accepted=False,
            rejection_reason=cap_msg,
            structural_risk_pct=0.0,
            sector=sector,
        )

    # 2. Sector Check
    if not gate.is_sector_available(sym, active_sectors):
        return Stage4Decision(
            symbol=sym,
            is_accepted=False,
            rejection_reason=f"Sector '{sector}' already occupied by an active portfolio trade",
            structural_risk_pct=0.0,
            sector=sector,
        )

    # 3. Structural Risk Check
    risk_ok, risk_pct, risk_msg = check_structural_risk(entry_ref, structural_stop, max_risk_pct=max_stop)
    if not risk_ok:
        return Stage4Decision(
            symbol=sym,
            is_accepted=False,
            rejection_reason=risk_msg,
            structural_risk_pct=risk_pct,
            sector=sector,
        )

    # 4. 10:00 AM Gap Invalidation Check (if live execution price provided)
    gap_val = None
    if price_10am is not None:
        gap_ok, gap_val, gap_msg = check_gap_invalidation(price_10am, entry_ref, max_gap_pct=max_gap)
        if not gap_ok:
            return Stage4Decision(
                symbol=sym,
                is_accepted=False,
                rejection_reason=gap_msg,
                structural_risk_pct=risk_pct,
                gap_pct=gap_val,
                sector=sector,
            )

    return Stage4Decision(
        symbol=sym,
        is_accepted=True,
        structural_risk_pct=risk_pct,
        gap_pct=gap_val,
        sector=sector,
    )
