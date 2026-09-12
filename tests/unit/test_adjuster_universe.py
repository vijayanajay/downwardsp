"""Phase 3 unit tests: corporate action adjuster and PIT universe math."""

from __future__ import annotations

import sys
from datetime import date

import pandas as pd
import pytest

sys.path.insert(0, "src")

from nse_cash.core.adjuster import adjustment_factor, apply_adjustments
from nse_cash.data.corporate_actions import classify_action

# ---------------------------------------------------------------------------
# Adjustment factor math
# ---------------------------------------------------------------------------


def test_af_one_to_one_bonus():
    # 1:1 bonus: holders get 1 new share per 1 held -> AF = 1/2
    assert adjustment_factor(1.0, 1.0) == 0.5


def test_af_five_to_one_split():
    # 10 -> 2 split: A = old/new - 1 = 4, B = 1 -> AF = 1/5
    assert adjustment_factor(4.0, 1.0) == pytest.approx(0.2)


def test_af_three_to_one_bonus():
    # 3:1 bonus -> AF = 1/4
    assert adjustment_factor(3.0, 1.0) == pytest.approx(0.25)


def test_classify_actions():
    assert classify_action("SPLIT FROM RS 10 TO RS 2") == ("SPLIT", 4.0, 1.0)
    assert classify_action("SPLIT OF RS.5 TO RS.1") == ("SPLIT", 4.0, 1.0)
    assert classify_action(
        "Face Value Split (Sub-Division) - From Rs10/- Per Share To Re 1/- Per Share"
    ) == ("SPLIT", 9.0, 1.0)
    assert classify_action("BONUS 1:1") == ("BONUS", 1.0, 1.0)
    assert classify_action("BONUS ISSUE 3:1") == ("BONUS", 3.0, 1.0)
    assert classify_action("RIGHTS 2:15") == ("RIGHTS", 1.0, 1.0)
    assert classify_action("INTERIM DIVIDEND RS 5 PER SHARE") == ("DIVIDEND", 1.0, 1.0)
    assert classify_action("ANNUAL GENERAL MEETING")[0] is None


def test_apply_adjustments_backward_prices_and_volume():
    bars = pd.DataFrame({
        "date": [date(2026, 9, 9), date(2026, 9, 10)],   # day 1 pre-ex, day 2 ex-date
        "open": [200.0, 100.0], "high": [210.0, 105.0],
        "low": [195.0, 98.0], "close": [205.0, 102.0],
        "volume": [1000, 2000], "deliverable_qty": [500, 1000],
    })
    actions = pd.DataFrame({
        "ex_date": [date(2026, 9, 10)], "ratio_a": [1.0], "ratio_b": [1.0],
    })
    out = apply_adjustments(bars, actions)
    # Pre-ex-date row adjusted by AF=0.5; ex-date row untouched
    assert out.loc[0, "close_adj"] == pytest.approx(102.5)   # 205 * 0.5
    assert out.loc[1, "close_adj"] == pytest.approx(102.0)
    assert out.loc[0, "volume_adj"] == pytest.approx(2000.0)  # 1000 / 0.5
    assert out.loc[1, "volume_adj"] == pytest.approx(2000.0)
    assert out.loc[0, "delivery_adj"] == pytest.approx(1000.0)


def test_apply_adjustments_chained_cascading():
    """Two chained bonuses: cumulative AF multiplies (0.5 * 0.75 = 0.375)."""
    bars = pd.DataFrame({
        "date": [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)],
        "open": [100.0] * 3, "high": [110.0] * 3, "low": [95.0] * 3,
        "close": [105.0] * 3, "volume": [1000] * 3, "deliverable_qty": [500] * 3,
    })
    actions = pd.DataFrame({
        "ex_date": [date(2026, 2, 1), date(2026, 3, 1)],
        "ratio_a": [1.0, 3.0], "ratio_b": [1.0, 1.0],   # 1:1 then 3:1
    })
    out = apply_adjustments(bars, actions)
    # Day 1: before both -> AF = 0.5 * 0.25 = 0.125
    assert out.loc[0, "close_adj"] == pytest.approx(105.0 * 0.125)
    # Day 2: after 1:1 bonus, before 3:1 bonus -> AF = 0.25
    assert out.loc[1, "close_adj"] == pytest.approx(105.0 * 0.25)
    # Day 3: after both -> AF = 1.0
    assert out.loc[2, "close_adj"] == pytest.approx(105.0)


def test_apply_adjustments_no_actions_is_identity():
    bars = pd.DataFrame({
        "date": [date(2026, 9, 10)], "open": [100.0], "high": [101.0],
        "low": [99.0], "close": [100.5], "volume": [1000],
        "deliverable_qty": [800],
    })
    out = apply_adjustments(bars, pd.DataFrame())
    assert out.loc[0, "close_adj"] == pytest.approx(100.5)
    assert out.loc[0, "volume_adj"] == pytest.approx(1000.0)


def test_apply_adjustments_simultaneous_on_same_ex_date():
    """Split (10:2 -> AF=0.2) and Bonus (1:1 -> AF=0.5) on same ex-date: composite AF = 0.10."""
    bars = pd.DataFrame({
        "date": [date(2026, 9, 9), date(2026, 9, 10)],
        "open": [1000.0, 100.0], "high": [1050.0, 105.0],
        "low": [980.0, 98.0], "close": [1020.0, 102.0],
        "volume": [1000, 10000], "deliverable_qty": [500, 5000],
    })
    actions = pd.DataFrame({
        "ex_date": [date(2026, 9, 10), date(2026, 9, 10)],
        "ratio_a": [4.0, 1.0], "ratio_b": [1.0, 1.0],  # 10->2 split and 1:1 bonus
    })
    out = apply_adjustments(bars, actions)
    # Day 1 adjusted by 0.2 * 0.5 = 0.10
    assert out.loc[0, "close_adj"] == pytest.approx(102.0)
    assert out.loc[1, "close_adj"] == pytest.approx(102.0)
    assert out.loc[0, "volume_adj"] == pytest.approx(10000.0)


def test_classify_action_expanded_and_ncrps_filtering():
    assert classify_action(
        "SUB-DIVISION OF EQUITY SHARES FROM RS. 10/- EACH TO RE. 1/- EACH"
    ) == ("SPLIT", 9.0, 1.0)
    assert classify_action("BONUS ISSUE IN THE RATIO OF 1:2") == ("BONUS", 1.0, 2.0)
    # Preference shares / debentures must be excluded from common equity bonus adjustments
    assert classify_action("Scheme Of Arrangement - Bonus Ncrps 4:1")[0] is None
    assert classify_action("BONUS 1:1 PREFERENCE SHARES")[0] is None
    # Demergers and spin-offs
    assert classify_action("DEMERGER OF FINANCIAL SERVICES")[0] == "DEMERGER"
    assert classify_action("SPIN-OFF OF PHARMA BUSINESS")[0] == "DEMERGER"


def test_apply_adjustments_with_explicit_demerger_factor():
    """Verify that an explicit adjustment_factor (e.g. 0.9118 for a spin-off/demerger) is applied directly."""
    bars = pd.DataFrame({
        "date": [date(2023, 7, 19), date(2023, 7, 20)],
        "open": [2800.0, 2550.0], "high": [2850.0, 2600.0],
        "low": [2780.0, 2540.0], "close": [2840.0, 2580.0],
        "volume": [5000000, 8000000], "deliverable_qty": [2500000, 4000000],
    })
    actions = pd.DataFrame({
        "ex_date": [date(2023, 7, 20)], "action_type": ["DEMERGER"],
        "ratio_a": [1.0], "ratio_b": [1.0],
        "adjustment_factor": [0.9118],
    })
    out = apply_adjustments(bars, actions)
    assert out.loc[0, "close_adj"] == pytest.approx(2840.0 * 0.9118)
    assert out.loc[1, "close_adj"] == pytest.approx(2580.0)
    assert out.loc[0, "volume_adj"] == pytest.approx(5000000 / 0.9118)


def test_load_seed_corporate_actions():
    """Verify loading curated offline seed corporate actions."""
    from nse_cash.data.corporate_actions import load_seed_corporate_actions
    df = load_seed_corporate_actions()
    assert df is not None
    assert not df.empty
    assert "RELIANCE" in set(df["symbol"])
    assert "TCS" in set(df["symbol"])
    rel = df[(df["symbol"] == "RELIANCE") & (df["action_type"] == "DEMERGER")].iloc[0]
    assert rel["adjustment_factor"] == pytest.approx(0.9118)


# ---------------------------------------------------------------------------
# PIT universe gates (pure math layer; DuckDB path covered in integration test)
# ---------------------------------------------------------------------------


def test_universe_constants_match_brd():
    from nse_cash.core.constants import (ADTV_MIN_RUPEES, PRICE_FLOOR_RUPEES,
                                         UNIVERSE_MIN_SESSIONS, UNIVERSE_SIZE,
                                         UNIVERSE_WINDOW_DAYS)
    assert ADTV_MIN_RUPEES == 50_000_000.0   # Rs 5 Crores
    assert PRICE_FLOOR_RUPEES == 50.0
    assert UNIVERSE_SIZE == 500
    assert UNIVERSE_WINDOW_DAYS == 90
    assert UNIVERSE_MIN_SESSIONS == 60


def test_adtv_gate_math():
    """ADTV = mean(close * volume) over the window must clear Rs 5 Cr."""
    closes = [100.0] * 90
    volumes = [600_000] * 90            # 100 * 600k = 6 Cr/day
    adtv = sum(c * v for c, v in zip(closes, volumes)) / 90
    assert adtv >= 50_000_000.0
    volumes_low = [400_000] * 90        # 4 Cr/day -> must fail
    adtv_low = sum(c * v for c, v in zip(closes, volumes_low)) / 90
    assert adtv_low < 50_000_000.0
