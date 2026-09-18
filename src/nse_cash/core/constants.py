"""System-wide constants (Phase 1 directory plan: types, config, math, constants, adjustments)."""

# --- PIT universe gates (Phase 3.2) ---
UNIVERSE_SIZE = 500
UNIVERSE_WINDOW_DAYS = 90          # rolling ADTV window
UNIVERSE_MIN_SESSIONS = 60         # minimum history required before qualifying
ADTV_MIN_RUPEES = 50_000_000.0     # Rs 5.00 Crores
PRICE_FLOOR_RUPEES = 50.0          # zero penny stocks

# --- Governance (Phase 3.3) ---
CIRCUIT_BAND_EXCLUDE_PCT = 5.0     # reject stocks with band <= 5%
CIRCUIT_HIT_SESSIONS = 3           # reject stocks that hit circuit in last 3 sessions
BOARD_MEETING_LOOKAHEAD_DAYS = 3   # reject stocks with board meeting in next 3 sessions

# --- NSE series that qualify as tradeable cash equity ---
CASH_SERIES = {"EQ", "BE", "SM"}

# --- Setups / S_runner (Phase 5) ---
FEATURE_WINDOW_DAYS = 210         # min session history per symbol for stable features
# CR-2026-001: 0.70 was a total lockout for dry-volume setups once Z_delivery
# is normalized to [0, 1] (dry-day ceiling ~= 0.645 with z <= 0). Measured on
# 593k feature rows: 0.45 admits ~8.1% of Setup-2 predicate fires, 0.50 only
# ~3.4% (median dry-day score 0.27, p95 0.48). Freeze at 0.45, re-freeze only
# on per-setup attribution evidence (funnel log in ranking.py).
S_RUNNER_MIN = 0.45               # high-conviction runner threshold
DELIVERY_Z_WINSOR = 3.0           # clamp Z_delivery so S_runner terms are on one scale
DELIVERY_Z_NORM_MAX = 3.0         # S_runner normalization: z_norm = clamp(z,0,3)/3
ACCUMULATION_LOOKBACK_DAYS = 5    # Setup 1 condition A: shock within last 5 sessions
ICEBERG_LOOKBACK_DAYS = 3         # Setup 1 condition B: z >= 1.5 on 2 of last 3 sessions
BREAKOUT_RETEST_AGE_MIN = 3       # Setup 4: retest happens 3..7 sessions after breakout
BREAKOUT_RETEST_AGE_MAX = 7
BREAKOUT_CONFIRM_MULT = 1.02      # breakout: close > 1.02x the prior 90-session ceiling
DELIVERY_Z_WINSOR = 3.0           # clamp Z_delivery so S_runner terms are on one scale
PV_WINDOW = 5                     # Parkinson volatility window
PV_PERCENTILE_WINDOW = 60         # PV percentile lookback
IMOM_REGRESSION_DAYS = 36         # rolling OLS residual regression
IMOM_SUM_DAYS = 20                # residual alpha accumulation window
BASE_RESISTANCE_DAYS = 90         # rolling breakout / base resistance level
HIGH_52W_DAYS = 252               # 52-week high lookback

# --- Execution (CR-2026-001 Issue 4) ---
# Kite GTT OCO stop: trigger = structural stop; the LIMIT leg must sit below
# the trigger or a gap-down open leaves the sell limit unexecuted. Measured
# clean overnight-gap distribution (2010-2026, corporate-action artifacts
# excluded): 5.06% of sessions gap down beyond -1.5%. 1.5% is the honest
# default; 2.0% halves that at ~0.5% extra worst-case loss (config overridable).
GTT_STOP_LIMIT_BUFFER = 0.015

# --- Storage ---
DUCKDB_DAILY_BARS = "daily_bars"
DUCKDB_CORPORATE_ACTIONS = "corporate_actions"
DUCKDB_MARKET_INDICES = "market_indices"
DUCKDB_PIT_UNIVERSE = "pit_universe"
DUCKDB_GOVERNANCE = "governance"
DUCKDB_FEATURES = "features"

# Feature table columns (all float; NULL = insufficient history)
FEATURE_COLS = [
    "sma20_vol", "sma20_delivery", "sd20_delivery", "delivery_z",
    "shock_a_5d", "z15_count_3d",
    "pv5", "pv_percentile", "rsi2", "rs_mansfield", "rs_percentile",
    "imom", "imom_percentile", "base_low_90", "base_high_90",
    "breakout_anchor_90", "breakout_age",
    "high_52w", "sma200", "sma200_slope5", "prev_low",
]
