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
S_RUNNER_MIN = 0.70               # high-conviction runner threshold
DELIVERY_Z_WINSOR = 3.0           # clamp Z_delivery so S_runner terms are on one scale
PV_WINDOW = 5                     # Parkinson volatility window
PV_PERCENTILE_WINDOW = 60         # PV percentile lookback
IMOM_REGRESSION_DAYS = 36         # rolling OLS residual regression
IMOM_SUM_DAYS = 20                # residual alpha accumulation window
BASE_RESISTANCE_DAYS = 90         # rolling breakout / base resistance level
HIGH_52W_DAYS = 252               # 52-week high lookback

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
    "pv5", "pv_percentile", "rsi2", "rs_mansfield", "rs_percentile",
    "imom", "imom_percentile", "base_low_90", "base_high_90",
    "high_52w", "sma200", "sma200_slope5", "prev_low",
]
