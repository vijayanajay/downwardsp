"""System-wide constants (Phase 1 directory plan: types, config, math, constants, adjustments)."""

# --- PIT universe gates (Phase 3.2) ---
UNIVERSE_SIZE = 500
UNIVERSE_WINDOW_DAYS = 90          # rolling ADTV window
ADTV_MIN_RUPEES = 50_000_000.0     # Rs 5.00 Crores
PRICE_FLOOR_RUPEES = 50.0          # zero penny stocks

# --- Governance (Phase 3.3) ---
CIRCUIT_BAND_EXCLUDE_PCT = 5.0     # reject stocks with band <= 5%
CIRCUIT_HIT_SESSIONS = 3           # reject stocks that hit circuit in last 3 sessions
BOARD_MEETING_LOOKAHEAD_DAYS = 3   # reject stocks with board meeting in next 3 sessions

# --- NSE series that qualify as tradeable cash equity ---
CASH_SERIES = {"EQ", "BE", "SM"}

# --- Storage ---
DUCKDB_DAILY_BARS = "daily_bars"
DUCKDB_CORPORATE_ACTIONS = "corporate_actions"
DUCKDB_MARKET_INDICES = "market_indices"
DUCKDB_PIT_UNIVERSE = "pit_universe"
DUCKDB_GOVERNANCE = "governance"
