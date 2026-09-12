"""Unit tests for Sector Classification & Diversification Gate (Phase 4.2)."""

import json
from pathlib import Path

import pytest

from nse_cash.funnel.sector_gate import BUILTIN_SECTOR_FALLBACK, SectorGate


@pytest.fixture
def temp_sector_json(tmp_path):
    f = tmp_path / "test_sectors.json"
    data = {
        "HDFCBANK": {"symbol": "HDFCBANK", "sector": "Financial Services", "industry": "Financial Services"},
        "ICICIBANK": {"symbol": "ICICIBANK", "sector": "Financial Services", "industry": "Financial Services"},
        "TCS": {"symbol": "TCS", "sector": "IT", "industry": "Information Technology"},
        "INFY": {"symbol": "INFY", "sector": "IT", "industry": "Information Technology"},
        "MARUTI": {"symbol": "MARUTI", "sector": "Auto", "industry": "Automobile and Auto Components"},
        "SUNPHARMA": {"symbol": "SUNPHARMA", "sector": "Healthcare", "industry": "Healthcare"},
    }
    f.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return f


def test_sector_gate_load_and_lookup(temp_sector_json):
    gate = SectorGate(mapping_path=temp_sector_json)
    assert gate.get_sector("HDFCBANK") == "Financial Services"
    assert gate.get_sector("TCS") == "IT"
    assert gate.get_sector("MARUTI") == "Auto"
    # Strips .NS
    assert gate.get_sector("INFY.NS") == "IT"
    # Unmapped symbol
    assert gate.get_sector("NONEXISTENT") == "Unknown"


def test_sector_gate_fallback_when_file_missing(tmp_path):
    missing_file = tmp_path / "does_not_exist.json"
    gate = SectorGate(mapping_path=missing_file)
    # Falls back to builtin core mapping
    assert gate.get_sector("HDFCBANK") == "Financial Services"
    assert gate.get_sector("TCS") == "IT"
    assert gate.get_sector("RELIANCE") == "Energy"


def test_is_sector_available(temp_sector_json):
    gate = SectorGate(mapping_path=temp_sector_json)
    active_sectors = {"Financial Services", "Auto"}

    # TCS is IT -> Available
    assert gate.is_sector_available("TCS", active_sectors) is True
    # HDFCBANK is Financial Services -> Already occupied
    assert gate.is_sector_available("HDFCBANK", active_sectors) is False
    # MARUTI is Auto -> Already occupied
    assert gate.is_sector_available("MARUTI", active_sectors) is False


def test_filter_candidates_active_sector_collision(temp_sector_json):
    """Candidate is rejected if its sector is already held in an active portfolio slot."""
    gate = SectorGate(mapping_path=temp_sector_json)
    active = {"Financial Services"}  # Slot 1 occupied by HDFCBANK

    candidates = [
        {"symbol": "TCS"},       # IT -> Should PASS
        {"symbol": "ICICIBANK"}, # Financial Services -> Should FAIL (collision with active)
        {"symbol": "SUNPHARMA"}, # Healthcare -> Should PASS
    ]

    accepted, rejected = gate.filter_candidates(candidates, active_sectors=active)

    assert len(accepted) == 2
    assert [c["symbol"] for c in accepted] == ["TCS", "SUNPHARMA"]
    assert len(rejected) == 1
    assert rejected[0][0]["symbol"] == "ICICIBANK"
    assert "already occupied by an active portfolio trade" in rejected[0][1]


def test_filter_candidates_intra_batch_collision(temp_sector_json):
    """If two candidates from the same sector arrive in the same batch, keep only the first."""
    gate = SectorGate(mapping_path=temp_sector_json)
    active = set()  # All 4 slots open

    candidates = [
        {"symbol": "TCS"},    # IT (Rank 1) -> Accepted
        {"symbol": "INFY"},   # IT (Rank 2) -> Rejected (sector already claimed by TCS)
        {"symbol": "MARUTI"}, # Auto -> Accepted
    ]

    accepted, rejected = gate.filter_candidates(candidates, active_sectors=active)

    assert len(accepted) == 2
    assert [c["symbol"] for c in accepted] == ["TCS", "MARUTI"]
    assert len(rejected) == 1
    assert rejected[0][0]["symbol"] == "INFY"
    assert "already claimed by candidate 'TCS'" in rejected[0][1]
