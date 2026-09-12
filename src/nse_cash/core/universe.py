"""Dynamic Point-in-Time (PIT) Universe Engine (Phase 3.2).

For each historical trading date t:
  ADTV_90(t) = mean over last 90 sessions of Close_raw * Volume_raw
  Liquidity gate:  ADTV_90(t) >= Rs 5,00,00,000  (Rs 5.00 Crores)
  Price floor:     Close_raw(t) >= Rs 50.00
  Seasoning gate:  at least 60 trading sessions of history (filters unseasoned IPOs)
Rank qualifying EQ stocks by ADTV_90 descending; keep the Top 500 and store
daily membership in pit_universe(date, symbol, adtv_90, rank).

Vectorized in DuckDB SQL directly to prevent memory thrashing.
"""

from __future__ import annotations

import logging
from datetime import date as Date

import pandas as pd

from nse_cash.core.constants import (ADTV_MIN_RUPEES, PRICE_FLOOR_RUPEES,
                                     UNIVERSE_MIN_SESSIONS, UNIVERSE_SIZE,
                                     UNIVERSE_WINDOW_DAYS)

log = logging.getLogger("nse_cash.universe")


def compute_adtv_all(store, window: int = UNIVERSE_WINDOW_DAYS,
                     min_sessions: int = UNIVERSE_MIN_SESSIONS) -> pd.DataFrame:
    """Rolling ADTV (mean of close*volume over `window` sessions) for all bars.

    Uses a session-indexed window (per-symbol row_number). Enforces a seasoning
    gate so newly listed stocks with fewer than `min_sessions` return NULL.
    """
    total_dates = store.con.execute("SELECT count(DISTINCT date) FROM daily_bars").fetchone()[0]
    effective_min = min(min_sessions, total_dates) if total_dates > 0 else min_sessions

    return store.con.execute(f"""
        WITH ranked AS (
            SELECT symbol, date, close, volume,
                   row_number() OVER (PARTITION BY symbol ORDER BY date) AS rn
            FROM daily_bars
        ),
        with_val AS (
            SELECT symbol, date, close, rn,
                   close * volume AS traded_value FROM ranked
        )
        SELECT symbol, date, close,
               CASE WHEN rn >= {effective_min} THEN
                   avg(traded_value) OVER (
                       PARTITION BY symbol ORDER BY rn
                       ROWS BETWEEN {window - 1} PRECEDING AND CURRENT ROW
                   )
               ELSE NULL END AS adtv_90
        FROM with_val
    """).df()


def build_pit_universe_range(store, start, end,
                             min_sessions: int = UNIVERSE_MIN_SESSIONS) -> int:
    """Build PIT universes for a date range in a single vectorized DuckDB query."""
    start_d = pd.Timestamp(start).date()
    end_d = pd.Timestamp(end).date()
    if start_d > end_d:
        start_d, end_d = end_d, start_d

    total_dates = store.con.execute(
        "SELECT count(DISTINCT date) FROM daily_bars WHERE date <= ?", [end_d]
    ).fetchone()[0]
    effective_min = min(min_sessions, total_dates) if total_dates > 0 else min_sessions

    q = f"""
        WITH ranked AS (
            SELECT symbol, date, close, volume,
                   row_number() OVER (PARTITION BY symbol ORDER BY date) AS rn
            FROM daily_bars
            WHERE date <= ?
        ),
        with_val AS (
            SELECT symbol, date, close, rn,
                   close * volume AS traded_value
            FROM ranked
        ),
        with_adtv AS (
            SELECT symbol, date, close,
                   CASE WHEN rn >= {effective_min} THEN
                       avg(traded_value) OVER (
                           PARTITION BY symbol ORDER BY rn
                           ROWS BETWEEN {UNIVERSE_WINDOW_DAYS - 1} PRECEDING AND CURRENT ROW
                       )
                   ELSE NULL END AS adtv_90
            FROM with_val
        ),
        day_qual AS (
            SELECT date, symbol, adtv_90,
                   row_number() OVER (PARTITION BY date ORDER BY adtv_90 DESC) AS rank
            FROM with_adtv
            WHERE date BETWEEN ? AND ?
              AND adtv_90 >= {ADTV_MIN_RUPEES}
              AND close >= {PRICE_FLOOR_RUPEES}
        )
        SELECT date, symbol, adtv_90, rank
        FROM day_qual
        WHERE rank <= {UNIVERSE_SIZE}
        ORDER BY date, rank
    """
    membership = store.con.execute(q, [end_d, start_d, end_d]).df()
    store.con.execute("DELETE FROM pit_universe WHERE date BETWEEN ? AND ?", [start_d, end_d])
    if not membership.empty:
        membership["date"] = membership["date"].map(lambda d: pd.Timestamp(d).date())
        store.upsert_pit_universe(membership)
        n_dates = int(membership["date"].nunique())
    else:
        n_dates = 0

    log.info("PIT universes built for %d dates in range [%s, %s]", n_dates, start_d, end_d)
    return n_dates


def build_pit_universe(store, on_date=None,
                       min_sessions: int = UNIVERSE_MIN_SESSIONS) -> pd.DataFrame:
    """Compute + store the PIT universe for one date (default: latest date)."""
    if on_date is None:
        on_date = store.latest_date()
    if on_date is None:
        raise RuntimeError("daily_bars is empty; run `nse-cash sync` first.")
    on_date = pd.Timestamp(on_date).date()

    build_pit_universe_range(store, on_date, on_date, min_sessions=min_sessions)
    df = store.con.execute(
        "SELECT date, symbol, adtv_90, rank FROM pit_universe WHERE date = ? ORDER BY rank",
        [on_date]
    ).df()
    if not df.empty:
        df["date"] = df["date"].map(lambda d: pd.Timestamp(d).date())
    log.info("PIT universe %s: %d symbols (min ADTV %.1f Cr)",
             on_date, len(df),
             (df["adtv_90"].min() / 1e7) if len(df) else 0.0)
    return df
