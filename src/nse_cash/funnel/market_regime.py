"""Macro Market Regime Engine (Phase 4.1).

Evaluates Funnel Stage 1 to determine if the macro environment permits new long swing trades:
  1. NIFTY 50 Close > 20-day EMA (macro trend filter)
  2. NIFTY 500 Market Breadth > 50.0% (% of constituent stocks with Close > 50-day SMA)

Decision logic:
  If NIFTY 50 <= EMA_20 OR Breadth <= 50.0% => DEFENSIVE_CASH (100% Cash switch; zero new entries)
  If both pass => OFFENSIVE_LONG (Allow Stage 3 setup evaluations)
"""

from __future__ import annotations

import logging
from datetime import date as Date
from pathlib import Path
from typing import Optional, Tuple

import pandas as pd
from pydantic import BaseModel, Field

from nse_cash.core.types import MarketRegimeState

log = logging.getLogger("nse_cash.market_regime")


class MarketRegimeResult(BaseModel):
    """Encapsulates the complete Point-in-Time Stage 1 Market Regime evaluation."""

    date: Date
    state: MarketRegimeState
    nifty50_close: float = Field(description="NIFTY 50 closing price on evaluation date")
    nifty50_ema20: float = Field(description="20-day Exponential Moving Average of NIFTY 50")
    nifty50_above_ema: bool = Field(description="True if Close > EMA20")
    breadth_pct: float = Field(description="Percentage of NIFTY 500 stocks above their 50-day SMA")
    breadth_above_50: bool = Field(description="True if Market Breadth > 50.0%")
    advancing_stocks: int = Field(description="Count of active constituent stocks above 50-day SMA")
    total_eligible_stocks: int = Field(description="Total active constituent stocks evaluated")
    reason: str = Field(description="Human-readable decision explanation")


def _get_nifty50_series(store, as_of_date: Date) -> pd.DataFrame:
    """Fetch NIFTY 50 historical closes up to as_of_date."""
    # 1. First check market_indices table
    df = store.con.execute(
        """
        SELECT date, close
        FROM market_indices
        WHERE index_name = 'NIFTY 50' AND date <= ?
        ORDER BY date
        """,
        [as_of_date],
    ).df()

    if not df.empty:
        df["date"] = pd.to_datetime(df["date"]).dt.date
        return df

    # 2. Fallback to local Parquet cache if available
    parquet_path = Path("data/ohlcv/_nifty.parquet")
    if parquet_path.exists():
        try:
            cached = pd.read_parquet(parquet_path)
            cached = cached.reset_index().rename(columns=str.lower)
            if "date" in cached.columns and "close" in cached.columns:
                cached["date"] = pd.to_datetime(cached["date"]).dt.date
                cached = cached[cached["date"] <= as_of_date][["date", "close"]].dropna()
                if not cached.empty:
                    return cached.sort_values("date").reset_index(drop=True)
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed reading fallback _nifty.parquet: %s", exc)

    return pd.DataFrame(columns=["date", "close"])


def compute_nifty50_ema(
    store, as_of_date: Optional[Date] = None, span: int = 20
) -> Tuple[float, float, bool]:
    """Compute NIFTY 50 Close, 20-day EMA, and whether Close > EMA.

    Returns (close, ema, is_above).
    """
    if as_of_date is None:
        latest = store.latest_date()
        if latest is None:
            raise RuntimeError("daily_bars is empty; run `nse-cash sync` first.")
        as_of_date = pd.Timestamp(latest).date()
    else:
        as_of_date = pd.Timestamp(as_of_date).date()

    df = _get_nifty50_series(store, as_of_date)
    if df.empty:
        raise ValueError(f"No NIFTY 50 price history found on or before {as_of_date}")

    df["ema"] = df["close"].ewm(span=span, adjust=False).mean()
    last_row = df[df["date"] == as_of_date]
    if last_row.empty:
        # If exact as_of_date is missing from index, pick the latest available bar on or before
        last_row = df.iloc[[-1]]

    close = float(last_row["close"].values[0])
    ema = float(last_row["ema"].values[0])
    is_above = bool(close > ema)
    return close, ema, is_above


def compute_nifty500_breadth(
    store, as_of_date: Optional[Date] = None, sma_period: int = 50
) -> Tuple[float, int, int, bool]:
    """Compute NIFTY 500 Market Breadth on as_of_date.

    Breadth = (Stocks with Close > SMA_50) / (Total active eligible stocks) * 100%
    Returns (breadth_pct, advancing_stocks, total_eligible, is_above_50).
    """
    if as_of_date is None:
        latest = store.latest_date()
        if latest is None:
            raise RuntimeError("daily_bars is empty; run `nse-cash sync` first.")
        as_of_date = pd.Timestamp(latest).date()
    else:
        as_of_date = pd.Timestamp(as_of_date).date()

    total_dates_in_db = store.con.execute(
        "SELECT count(DISTINCT date) FROM daily_bars WHERE date <= ?", [as_of_date]
    ).fetchone()[0]

    # Effective lookback window: in small test datasets, don't require more sessions than available
    effective_window = min(sma_period, total_dates_in_db) if total_dates_in_db > 0 else sma_period

    query = f"""
        WITH pit_syms AS (
            SELECT symbol FROM pit_universe WHERE date = ?
        ),
        ranked AS (
            SELECT d.symbol, d.date, coalesce(d.close_adj, d.close) AS c,
                   row_number() OVER (PARTITION BY d.symbol ORDER BY d.date) AS rn
            FROM daily_bars d
            WHERE d.date <= ?
              AND (NOT EXISTS (SELECT 1 FROM pit_syms) OR d.symbol IN (SELECT symbol FROM pit_syms))
        ),
        with_sma AS (
            SELECT symbol, date, c, rn,
                   avg(c) OVER (
                       PARTITION BY symbol ORDER BY rn
                       ROWS BETWEEN {effective_window - 1} PRECEDING AND CURRENT ROW
                   ) AS sma50
            FROM ranked
        )
        SELECT count(*) AS total_eligible,
               count(CASE WHEN c > sma50 THEN 1 END) AS advancing
        FROM with_sma
        WHERE date = ?
    """

    res = store.con.execute(query, [as_of_date, as_of_date, as_of_date]).fetchone()
    total_eligible = int(res[0]) if res and res[0] is not None else 0
    advancing = int(res[1]) if res and res[1] is not None else 0

    if total_eligible == 0:
        return 0.0, 0, 0, False

    breadth_pct = (advancing / total_eligible) * 100.0
    is_above_50 = bool(breadth_pct > 50.0)
    return breadth_pct, advancing, total_eligible, is_above_50


def evaluate_market_regime(
    store,
    as_of_date: Optional[Date] = None,
    ema_span: int = 20,
    sma_period: int = 50,
) -> MarketRegimeResult:
    """Evaluate Stage 1 Macro Market Regime for as_of_date."""
    if as_of_date is None:
        latest = store.latest_date()
        if latest is None:
            raise RuntimeError("daily_bars is empty; run `nse-cash sync` first.")
        target_date = pd.Timestamp(latest).date()
    else:
        target_date = pd.Timestamp(as_of_date).date()

    nifty_close, nifty_ema, nifty_pass = compute_nifty50_ema(
        store, as_of_date=target_date, span=ema_span
    )
    breadth_pct, advancing, total_stocks, breadth_pass = compute_nifty500_breadth(
        store, as_of_date=target_date, sma_period=sma_period
    )

    if nifty_pass and breadth_pass:
        state = MarketRegimeState.OFFENSIVE_LONG
        reason = (
            f"OFFENSIVE_LONG: NIFTY 50 ({nifty_close:.2f} > 20EMA {nifty_ema:.2f}) "
            f"and Market Breadth ({breadth_pct:.1f}% > 50.0%) both confirmed bullish."
        )
    else:
        state = MarketRegimeState.DEFENSIVE_CASH
        failures = []
        if not nifty_pass:
            failures.append(f"NIFTY 50 ({nifty_close:.2f} <= 20EMA {nifty_ema:.2f})")
        if not breadth_pass:
            failures.append(f"Market Breadth ({breadth_pct:.1f}% <= 50.0%)")
        reason = (
            f"DEFENSIVE_CASH (100% Cash Switch): {', '.join(failures)} failed. "
            f"Zero new swing entries allowed."
        )

    log.info("Regime %s: %s (%s)", target_date, state.value, reason)

    return MarketRegimeResult(
        date=target_date,
        state=state,
        nifty50_close=nifty_close,
        nifty50_ema20=nifty_ema,
        nifty50_above_ema=nifty_pass,
        breadth_pct=breadth_pct,
        breadth_above_50=breadth_pass,
        advancing_stocks=advancing,
        total_eligible_stocks=total_stocks,
        reason=reason,
    )


def evaluate_market_regime_range(
    store,
    start_date: Optional[Date] = None,
    end_date: Optional[Date] = None,
    ema_span: int = 20,
    sma_period: int = 50,
) -> pd.DataFrame:
    """Vectorized calculation of market regime states across a historical date range.

    Optimized for the Phase 6 backtesting engine.
    """
    dates = store.trading_dates(start=start_date, end=end_date)
    if not dates:
        return pd.DataFrame(
            columns=["date", "state", "nifty50_close", "nifty50_ema", "breadth_pct"]
        )

    # 1. Vectorized NIFTY 50 EMA
    nifty_df = store.con.execute(
        """
        SELECT date, close
        FROM market_indices
        WHERE index_name = 'NIFTY 50'
        ORDER BY date
        """
    ).df()

    if nifty_df.empty:
        parquet_path = Path("data/ohlcv/_nifty.parquet")
        if parquet_path.exists():
            nifty_df = pd.read_parquet(parquet_path).reset_index().rename(columns=str.lower)

    if not nifty_df.empty:
        nifty_df["date"] = pd.to_datetime(nifty_df["date"]).dt.date
        nifty_df["nifty_ema"] = nifty_df["close"].ewm(span=ema_span, adjust=False).mean()
        nifty_map = nifty_df.set_index("date")[["close", "nifty_ema"]].to_dict("index")
    else:
        nifty_map = {}

    # 2. Vectorized Breadth calculation across dates in range
    start_d = dates[0]
    end_d = dates[-1]

    breadth_df = store.con.execute(
        f"""
        WITH pit_syms AS (
            SELECT date, symbol FROM pit_universe
            WHERE date BETWEEN ? AND ?
        ),
        ranked AS (
            SELECT d.symbol, d.date, coalesce(d.close_adj, d.close) AS c,
                   row_number() OVER (PARTITION BY d.symbol ORDER BY d.date) AS rn
            FROM daily_bars d
            WHERE d.date <= ?
        ),
        with_sma AS (
            SELECT symbol, date, c, rn,
                   avg(c) OVER (
                       PARTITION BY symbol ORDER BY rn
                       ROWS BETWEEN {sma_period - 1} PRECEDING AND CURRENT ROW
                   ) AS sma50
            FROM ranked
        ),
        filtered AS (
            SELECT w.date, w.symbol, w.c, w.sma50
            FROM with_sma w
            WHERE w.date BETWEEN ? AND ?
              AND (
                  NOT EXISTS (SELECT 1 FROM pit_syms p WHERE p.date = w.date)
                  OR EXISTS (SELECT 1 FROM pit_syms p WHERE p.date = w.date AND p.symbol = w.symbol)
              )
        )
        SELECT date,
               count(*) AS total_stocks,
               count(CASE WHEN c > sma50 THEN 1 END) AS advancing_stocks
        FROM filtered
        GROUP BY date
        ORDER BY date
        """,
        [start_d, end_d, end_d, start_d, end_d],
    ).df()

    breadth_df["date"] = pd.to_datetime(breadth_df["date"]).dt.date
    breadth_map = breadth_df.set_index("date").to_dict("index")

    rows = []
    for d in dates:
        nifty_info = nifty_map.get(d)
        n_close = nifty_info["close"] if nifty_info else 0.0
        n_ema = nifty_info["nifty_ema"] if nifty_info else 0.0
        n_pass = bool(n_close > n_ema) if nifty_info else False

        b_info = breadth_map.get(d)
        tot = b_info["total_stocks"] if b_info else 0
        adv = b_info["advancing_stocks"] if b_info else 0
        b_pct = (adv / tot * 100.0) if tot > 0 else 0.0
        b_pass = bool(b_pct > 50.0)

        is_offensive = n_pass and b_pass
        state = MarketRegimeState.OFFENSIVE_LONG if is_offensive else MarketRegimeState.DEFENSIVE_CASH

        rows.append(
            {
                "date": d,
                "state": state.value,
                "nifty50_close": n_close,
                "nifty50_ema20": n_ema,
                "nifty50_above_ema": n_pass,
                "breadth_pct": b_pct,
                "breadth_above_50": b_pass,
                "advancing_stocks": adv,
                "total_eligible_stocks": tot,
            }
        )

    return pd.DataFrame(rows)
