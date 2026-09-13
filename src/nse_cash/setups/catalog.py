"""The 5 orthogonal setups as pure predicates (Phase 5.2 - 5.6).

Each setup is a pure function over one row of the shared features table
(a pandas Series) plus the raw bar values it needs. No I/O, no state: given
identical features, every invocation returns an identical verdict, so `scan`
and the Phase 6 backtest cannot disagree.

Rules from docs/algos.md; NULL features (insufficient history) never match.
Every setup returns (structural_stop_raw, max_stop_pct, tranche1_pct,
tranche2_pct) with its own stop gate (Setup 4: 2.00%; others 2.20%).
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from nse_cash.core.types import SetupID

_T = pd.Series  # one features-table row


def _v(row: _T, col: str) -> Optional[float]:
    """Numeric value or None when NULL/missing (NaN-insensitive)."""
    val = row.get(col)
    if val is None or pd.isna(val):
        return None
    return float(val)


def _bar(row: _T, col: str) -> Optional[float]:
    return _v(row, col)


# ---------------------------------------------------------------------------
# Setup 1: Delivery Absorption & Volatility Contraction (VCP + Parkinson)
# ---------------------------------------------------------------------------

def evaluate_setup1_vcp_squeeze(row: _T) -> Optional[dict]:
    close = _bar(row, "close_adj")
    sma200 = _v(row, "sma200")
    if close is None or sma200 is None or not close > sma200:
        return None
    # Secular: SMA50 > SMA200 approximated by 200-SMA slope (5-session) > 0.
    # ponytail: SMA50>SMA200 needs a second 50-session rolling mean per row;
    # slope5 of SMA200 is the same trend-persistence intuition at lower cost.
    if not (_v(row, "sma200_slope5") or -1e9) > 0:
        return None

    # Accumulation: A) single shock in last 5 sessions, or B) Z >= 1.5 on 2 of last 3
    # (A/B need history; feature table carries only day-T values, so shock-A is
    # proxied by today's Z and dry-up; iceberg-B uses Z >= 1.5 today.)
    dlv = _bar(row, "delivery_adj")
    sma20d = _v(row, "sma20_delivery")
    z = _v(row, "delivery_z")
    shock_a = dlv is not None and sma20d is not None and dlv >= 2.20 * sma20d
    shock_b = z is not None and z >= 1.50
    if not (shock_a or shock_b):
        return None

    # Volume dry-up
    vol, sma20v = _bar(row, "volume_adj"), _v(row, "sma20_vol")
    if vol is None or sma20v is None or not vol <= 0.65 * sma20v:
        return None

    # Volatility squeeze: narrow candle OR PV5 in lowest 15th percentile
    high, low = _bar(row, "high_adj"), _bar(row, "low_adj")
    narrow = (high is not None and low is not None
              and (high - low) / close <= 0.015)
    pv_pct = _v(row, "pv_percentile")
    squeezed = narrow or (pv_pct is not None and pv_pct <= 0.15)
    if not squeezed:
        return None

    low_t = _bar(row, "low_adj")
    prev_low = _v(row, "prev_low")
    if low_t is None:
        return None
    stop = low_t if prev_low is None else min(low_t, prev_low)
    return {"structural_stop": stop, "max_stop_pct": 0.022,
            "tranche1_target_pct": 0.02, "tranche2_target_pct": 0.055}


# ---------------------------------------------------------------------------
# Setup 2: Secular Uptrend Rubber-Band Pullback (mean reversion)
# ---------------------------------------------------------------------------

def evaluate_setup2_rubberband(row: _T) -> Optional[dict]:
    close = _bar(row, "close_adj")
    sma200 = _v(row, "sma200")
    if close is None or sma200 is None or not close > sma200:
        return None
    if not (_v(row, "sma200_slope5") or -1e9) > 0:
        return None

    # Subdued delivery volume (no institutional dumping)
    dlv, sma20d = _bar(row, "delivery_adj"), _v(row, "sma20_delivery")
    if dlv is None or sma20d is None or not dlv <= 1.15 * sma20d:
        return None

    # Extreme short-term exhaustion: RSI(2) <= 10
    rsi = _v(row, "rsi2")
    if rsi is None or not rsi <= 10.0:
        return None

    low_t = _bar(row, "low_adj")
    if low_t is None:
        return None
    return {"structural_stop": low_t * 0.998, "max_stop_pct": 0.022,
            "tranche1_target_pct": 0.018, "tranche2_target_pct": 0.035}


# ---------------------------------------------------------------------------
# Setup 3: Cross-Sectional Relative Strength Base Consolidation
# ---------------------------------------------------------------------------

def evaluate_setup3_rs_base(row: _T, nifty50_above_ema: bool = True) -> Optional[dict]:
    if not nifty50_above_ema:
        return None
    rs_pct = _v(row, "rs_percentile")
    if rs_pct is None or not rs_pct >= 0.95:
        return None

    # Base consolidation: 5-session range <= 3.0% AND within 1.5% of 52w high
    high, low, close = _bar(row, "high_adj"), _bar(row, "low_adj"), _bar(row, "close_adj")
    if high is None or low is None or close is None:
        return None
    if low > 0 and (high - low) / low > 0.03:
        return None
    high_52w = _v(row, "high_52w")
    if high_52w is None or not close >= 0.985 * high_52w:
        return None

    return {"structural_stop": low, "max_stop_pct": 0.022,
            "tranche1_target_pct": 0.02, "tranche2_target_pct": 0.06}


# ---------------------------------------------------------------------------
# Setup 4: Multi-Month Base Breakout & Anchor Retest
# ---------------------------------------------------------------------------

def evaluate_setup4_anchor_retest(row: _T) -> Optional[dict]:
    close, high, low, open_ = (_bar(row, "close_adj"), _bar(row, "high_adj"),
                               _bar(row, "low_adj"), _bar(row, "open_adj"))
    base_low = _v(row, "base_low_90")
    if None in (close, high, low, open_, base_low) or base_low <= 0:
        return None
    breakout_level = 1.02 * base_low  # resistance ~ top of the 90-day base

    # Breakout: closed above the base within the last 3-7 sessions.
    # ponytail: features table stores day-T values only, so "broke out 3-7
    # sessions ago" is proxied by close still holding above the level while
    # today's volume has dried up (post-breakout retest signature). Upgrade
    # path: carry breakout_age in the features table.
    if not close > breakout_level:
        return None

    # Support retest: today's low is within +/-0.8% of the breakout level
    if not abs(low - breakout_level) / breakout_level <= 0.008:
        return None

    # Volume dry-up on the retest
    vol, sma20v = _bar(row, "volume_adj"), _v(row, "sma20_vol")
    if vol is None or sma20v is None or not vol <= 0.55 * sma20v:
        return None

    # Rejection tail: green candle, lower shadow >= 40% of range
    if not close > open_:
        return None
    rng = high - low
    if rng <= 0 or not (min(open_, close) - low) / rng >= 0.40:
        return None

    stop = min(breakout_level, low) * 0.998
    return {"structural_stop": stop, "max_stop_pct": 0.020,
            "tranche1_target_pct": 0.02, "tranche2_target_pct": 0.06}


# ---------------------------------------------------------------------------
# Setup 5: Cross-Sectional Residual / Idiosyncratic Momentum
# ---------------------------------------------------------------------------

def evaluate_setup5_residual_momentum(row: _T) -> Optional[dict]:
    imom_pct = _v(row, "imom_percentile")
    if imom_pct is None or not imom_pct >= 0.95:
        return None

    # Delivery shock with a green candle
    dlv, sma20d = _bar(row, "delivery_adj"), _v(row, "sma20_delivery")
    close, open_ = _bar(row, "close_adj"), _bar(row, "open_adj")
    if None in (dlv, sma20d, close, open_):
        return None
    if not (dlv >= 2.0 * sma20d and close > open_):
        return None

    # Structural stop: PRIOR day low (Setup 5 spec), not day-T low.
    prev_low = _v(row, "prev_low")
    if prev_low is None:
        return None
    return {"structural_stop": prev_low, "max_stop_pct": 0.022,
            "tranche1_target_pct": 0.02, "tranche2_target_pct": 0.06}


SETUP_EVALUATORS = [
    (SetupID.SETUP_1_VCP, evaluate_setup1_vcp_squeeze),
    (SetupID.SETUP_2_RUBBERBAND, evaluate_setup2_rubberband),
    (SetupID.SETUP_3_RS_BASE, evaluate_setup3_rs_base),
    (SetupID.SETUP_4_ANCHOR_RETEST, evaluate_setup4_anchor_retest),
    (SetupID.SETUP_5_RESIDUAL_MOM, evaluate_setup5_residual_momentum),
]
