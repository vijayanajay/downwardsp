"""Unit tests for Stage 4 Pre-Entry Risk & Capacity Controller (Phase 4.3)."""

import pytest

from nse_cash.core.config import SystemConfig
from nse_cash.funnel.sector_gate import SectorGate
from nse_cash.funnel.stage4_gate import (check_gap_invalidation,
                                         check_portfolio_capacity,
                                         check_structural_risk,
                                         evaluate_stage4)


def test_check_portfolio_capacity():
    # 0 to 3 occupied out of 4 -> PASS
    ok, msg = check_portfolio_capacity(occupied_slots=0, num_slots=4)
    assert ok is True
    assert "4 open slot(s)" in msg

    ok, msg = check_portfolio_capacity(occupied_slots=2, num_slots=4)
    assert ok is True
    assert "2 open slot(s)" in msg

    ok, msg = check_portfolio_capacity(occupied_slots=3, num_slots=4)
    assert ok is True
    assert "1 open slot(s)" in msg

    # 4/4 occupied -> FAIL
    ok, msg = check_portfolio_capacity(occupied_slots=4, num_slots=4)
    assert ok is False
    assert "Portfolio capacity full" in msg

    # 5/4 occupied -> FAIL
    ok, msg = check_portfolio_capacity(occupied_slots=5, num_slots=4)
    assert ok is False


def test_check_structural_risk_boundaries():
    # 1.8% risk -> PASS
    ok, risk, msg = check_structural_risk(entry_ref=100.0, structural_stop=98.2, max_risk_pct=0.022)
    assert ok is True
    assert pytest.approx(risk, 1e-4) == 0.018

    # Exactly 2.20% risk -> PASS
    ok, risk, msg = check_structural_risk(entry_ref=100.0, structural_stop=97.8, max_risk_pct=0.022)
    assert ok is True
    assert pytest.approx(risk, 1e-4) == 0.022

    # 2.25% risk -> FAIL
    ok, risk, msg = check_structural_risk(entry_ref=100.0, structural_stop=97.75, max_risk_pct=0.022)
    assert ok is False
    assert pytest.approx(risk, 1e-4) == 0.0225
    assert "exceeds maximum allowed 2.20% gate" in msg

    # Inverted stop (stop >= entry for long trade) -> FAIL
    ok, risk, msg = check_structural_risk(entry_ref=100.0, structural_stop=100.5, max_risk_pct=0.022)
    assert ok is False
    assert "Invalid long stop" in msg

    # Equal stop -> FAIL
    ok, risk, msg = check_structural_risk(entry_ref=100.0, structural_stop=100.0, max_risk_pct=0.022)
    assert ok is False

    # Negative / Zero prices -> FAIL
    ok, risk, msg = check_structural_risk(entry_ref=-10.0, structural_stop=9.0, max_risk_pct=0.022)
    assert ok is False


def test_check_gap_invalidation_boundaries():
    # +0.80% gap -> PASS
    ok, gap, msg = check_gap_invalidation(price_10am=100.8, close_t=100.0, max_gap_pct=0.012)
    assert ok is True
    assert pytest.approx(gap, 1e-4) == 0.008

    # Exactly +1.20% gap -> PASS
    ok, gap, msg = check_gap_invalidation(price_10am=101.2, close_t=100.0, max_gap_pct=0.012)
    assert ok is True
    assert pytest.approx(gap, 1e-4) == 0.012

    # +1.30% gap -> FAIL
    ok, gap, msg = check_gap_invalidation(price_10am=101.3, close_t=100.0, max_gap_pct=0.012)
    assert ok is False
    assert pytest.approx(gap, 1e-4) == 0.013
    assert "exceeding +1.20% ceiling" in msg

    # Gap down (negative gap e.g. -0.5%) -> PASS
    ok, gap, msg = check_gap_invalidation(price_10am=99.5, close_t=100.0, max_gap_pct=0.012)
    assert ok is True
    assert pytest.approx(gap, 1e-4) == -0.005


def test_evaluate_stage4_composite_flow():
    config = SystemConfig()
    gate = SectorGate()

    # Valid candidate
    cand_valid = {"symbol": "TCS", "entry_ref": 3500.0, "structural_stop": 3440.0}  # risk = 1.71%
    decision = evaluate_stage4(
        cand_valid,
        occupied_slots=2,
        active_sectors={"Financial Services", "Auto"},
        sector_gate=gate,
        price_10am=3510.0,  # gap = +0.28%
        config=config,
    )
    assert decision.is_accepted is True
    assert decision.sector == "IT"
    assert decision.rejection_reason is None

    # Rejection 1: Portfolio full
    decision = evaluate_stage4(
        cand_valid,
        occupied_slots=4,
        active_sectors={"Financial Services"},
        sector_gate=gate,
        config=config,
    )
    assert decision.is_accepted is False
    assert "Portfolio capacity full" in decision.rejection_reason

    # Rejection 2: Sector collision
    cand_hdfc = {"symbol": "HDFCBANK", "entry_ref": 1600.0, "structural_stop": 1570.0}  # Financial Services
    decision = evaluate_stage4(
        cand_hdfc,
        occupied_slots=1,
        active_sectors={"Financial Services"},
        sector_gate=gate,
        config=config,
    )
    assert decision.is_accepted is False
    assert "already occupied by an active portfolio trade" in decision.rejection_reason

    # Rejection 3: Structural risk > 2.20%
    cand_risky = {"symbol": "TCS", "entry_ref": 3500.0, "structural_stop": 3400.0}  # risk = 2.85%
    decision = evaluate_stage4(
        cand_risky,
        occupied_slots=0,
        active_sectors=set(),
        sector_gate=gate,
        config=config,
    )
    assert decision.is_accepted is False
    assert "exceeds maximum allowed 2.20% gate" in decision.rejection_reason

    # Rejection 4: 10:00 AM Gap > +1.20%
    decision = evaluate_stage4(
        cand_valid,
        occupied_slots=0,
        active_sectors=set(),
        sector_gate=gate,
        price_10am=3550.0,  # 3550 / 3500 = +1.43% gap
        config=config,
    )
    assert decision.is_accepted is False
    assert "exceeding +1.20% ceiling" in decision.rejection_reason
