"""Dynamic Point-in-Time (PIT) Universe Engine (Phase 3.2).

For each historical trading date t:
  ADTV_90(t) = mean over last 90 sessions of Close_raw * Volume_raw
  Liquidity gate:  ADTV_90(t) >= Rs 5,00,00,000  (Rs 5.00 Crores)
  Price floor:     Close_raw(t) >= Rs 50.00
Rank qualifying EQ stocks by ADTV_90 descending; keep the Top 500 and store
daily membership in pit_universe(date, symbol, adtv_90, rank).

Vectorized in DuckDB SQL — one statement computes ADTV for all symbols/dates.
"""

from __future__ import annotations

import logging

import pandas as pd

from nse_cash.core.constants import (ADTV_MIN_RUPEES, PRICE_FLOOR_RUPEES,
                                     UNIVERSE_SIZE, UNIVERSE_WINDOW_DAYS)

log = logging.getLogger("nse_cash.universe")


def compute_adtv_all(store, window: int = UNIVERSE_WINDOW_DAYS) -> pd.DataFrame:
    """Rolling ADTV (mean of close*volume over `window` sessions) for all bars.

    Uses a session-indexed window (per-symbol row_number), which equals the
    calendar-90-trading-day definition in the BRD.
    """
    return store.con.execute(f"""
        WITH ranked AS (
            SELECT symbol, date, close, volume,
                   row_number() OVER (PARTITION BY symbol ORDER BY date) AS rn
            FROM daily_bars
        ),
        with_val AS (
            SELECT *, close * volume AS traded_value FROM ranked
        )
        SELECT symbol, date, close,
               avg(traded_value) OVER (
                   PARTITION BY symbol ORDER BY rn
                   ROWS BETWEEN {window - 1} PRECEDING AND CURRENT ROW
               ) AS adtv_90
        FROM with_val
    """).df()


def build_pit_universe(store, on_date=None) -> pd.DataFrame:
    """Compute + store the PIT universe for one date (default: latest date).

    Returns the ranked membership DataFrame for that date.
    """
    if on_date is None:
        on_date = store.latest_date()
    if on_date is None:
        raise RuntimeError("daily_bars is empty; run `nse-cash sync` first.")
    on_date = pd.Timestamp(on_date).date()

    df = compute_adtv_all(store)
    day = df[df["date"] == pd.Timestamp(on_date)]
    day = day.dropna(subset=["adtv_90"])
    day = day[day["adtv_90"] >= ADTV_MIN_RUPEES]
    day = day[day["close"] >= PRICE_FLOOR_RUPEES]
    day = day.sort_values("adtv_90", ascending=False).head(UNIVERSE_SIZE).copy()
    day["rank"] = range(1, len(day) + 1)

    membership = day[["date", "symbol", "adtv_90", "rank"]].copy()
    membership["date"] = membership["date"].map(lambda d: pd.Timestamp(d).date())
    # Membership is a full-date partition: drop stale members first
    # (rows for symbols that fell out of the Top 500 since the last build)
    store.con.execute("DELETE FROM pit_universe WHERE date = ?", [on_date])
    store.upsert_pit_universe(membership)
    log.info("PIT universe %s: %d symbols (min ADTV %.1f Cr)",
             on_date, len(membership),
             (membership["adtv_90"].min() / 1e7) if len(membership) else 0.0)
    return membership


def build_pit_universe_range(store, start, end) -> int:
    """Build PIT universes for a date range; returns dates processed."""
    dates = store.trading_dates(start, end)
    for d in dates:
        build_pit_universe(store, d)
    log.info("PIT universes built for %d dates", len(dates))
    return len(dates)
