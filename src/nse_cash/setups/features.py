"""Vectorized indicator & microstructure feature engine (Phase 5.1).

One pass over daily_bars computes every indicator the 5 setups need and
persists the result to the shared DuckDB `features` table, so `scan` and the
Phase 6 backtest read identical point-in-time values. All math is vectorized
(pandas rolling ops); the only costly piece, the 36-day OLS residual momentum,
uses the closed-form beta = Cov(R_i, R_m) / Var(R_m) instead of per-day
regressions.

Conventions:
  - All price/volume features use backward-adjusted columns (close_adj etc.)
    so splits/bonuses never create false signals.
  - Delivery features use delivery_adj (raw deliverable qty / adjustment factor).
  - NULL features mean insufficient history; predicates treat NULL as no-match.
"""

from __future__ import annotations

import logging
from datetime import date as Date
from typing import Optional

import numpy as np
import pandas as pd

from nse_cash.core.constants import (BASE_RESISTANCE_DAYS,
                                     BREAKOUT_CONFIRM_MULT,
                                     BREAKOUT_RETEST_AGE_MAX,
                                     BREAKOUT_RETEST_AGE_MIN,
                                     DELIVERY_Z_WINSOR, FEATURE_COLS,
                                     FEATURE_WINDOW_DAYS, HIGH_52W_DAYS,
                                     ICEBERG_LOOKBACK_DAYS,
                                     IMOM_REGRESSION_DAYS, IMOM_SUM_DAYS,
                                     PV_PERCENTILE_WINDOW, PV_WINDOW)

log = logging.getLogger("nse_cash.features")

# Sessions of history needed per symbol for the longest-lookback feature
# (SMA200 + its 5-session slope, 52w high, PV percentile).
_MIN_SESSIONS = max(200 + 5, HIGH_52W_DAYS, PV_WINDOW + PV_PERCENTILE_WINDOW)


def _add_symbol_features(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all per-symbol time-series features for one symbol's bars.

    df: ascending by date, with close_adj/high_adj/low_adj/volume_adj/
    delivery_adj columns. Returns the same frame with feature columns added.
    """
    out = df.copy()
    close = out["close_adj"]
    high, low = out["high_adj"], out["low_adj"]
    vol = out["volume_adj"]
    dlv = out["delivery_adj"]

    # --- Trend ---
    out["sma200"] = close.rolling(200, min_periods=200).mean()
    out["sma200_slope5"] = out["sma200"] - out["sma200"].shift(5)
    # CR-2026-003 Phase B.4: 20-session price SMA (Setup 1's trend floor:
    # close >= sma20 * 0.99 when catalog.setup1_pv_binding is on).
    out["sma20_close"] = close.rolling(20, min_periods=20).mean()

    # --- Volume & delivery ---
    out["sma20_vol"] = vol.rolling(20, min_periods=20).mean()
    out["sma20_delivery"] = dlv.rolling(20, min_periods=20).mean()
    sd = dlv.rolling(20, min_periods=20).std(ddof=0)
    out["sd20_delivery"] = sd
    z = (dlv - out["sma20_delivery"]) / sd.replace(0.0, np.nan)
    # Zero dispersion means every value in the window (incl. today) is equal,
    # so today's deviation is 0 too: no shock, z = 0 (not NaN).
    z = z.mask(sd == 0, 0.0)
    z = z.clip(upper=DELIVERY_Z_WINSOR)
    out["delivery_z"] = z

    # --- CR-2026-001 Issue 1: rolling accumulation footprints (Setup 1) ---
    # The accumulation shock happens on some day WITHIN the lookback; day T
    # itself is the volume dry-up + squeeze. Storing the rolling flags here
    # (not in the predicate) keeps scan and backtest on identical values.
    shock_now = ((dlv >= 2.20 * out["sma20_delivery"])
                 & (out["close_adj"] > out["open_adj"])).astype(float)
    shock_now = shock_now.mask(out["sma20_delivery"].isna() | out["open_adj"].isna(), np.nan)
    out["shock_a_5d"] = shock_now.rolling(5, min_periods=1).max()
    z15_now = (z >= 1.50).astype(float).where(z.notna())
    out["z15_count_3d"] = z15_now.rolling(3, min_periods=1).sum()

    # --- Parkinson volatility + percentile in own 60-day history ---
    hl2 = (np.log(high / low)) ** 2
    pv5 = np.sqrt(hl2.rolling(PV_WINDOW, min_periods=PV_WINDOW).sum()
                  / (4.0 * np.log(2.0) * PV_WINDOW))
    out["pv5"] = pv5
    out["pv_percentile"] = (pv5.rolling(PV_PERCENTILE_WINDOW,
                                        min_periods=PV_PERCENTILE_WINDOW)
                            .rank(pct=True))

    # --- Wilder's RSI(2) ---
    delta = close.diff()
    # Wilder's smoothing = EMA with alpha = 1/period
    up = delta.clip(lower=0).ewm(alpha=1 / 2, adjust=False,
                                 min_periods=2).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / 2, adjust=False,
                                      min_periods=2).mean()
    # down == 0 with up > 0 -> RS = inf -> RSI = 100 (straight-up move);
    # both 0 -> RS undefined -> NaN (no movement = no exhaustion signal).
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = up / down
    out["rsi2"] = 100.0 - 100.0 / (1.0 + rs)

    # --- Mansfield relative strength vs NIFTY 500 (cross-sectional) ---
    if "nifty500_close" in out.columns:
        bench = out["nifty500_close"]
        ratio = close / bench
        out["rs_mansfield"] = (ratio / ratio.rolling(50, min_periods=50).mean()
                               - 1.0) * 100.0
    else:
        out["rs_mansfield"] = np.nan
        out["rs_percentile"] = np.nan

    # --- 36-day OLS residual momentum vs NIFTY 50 (closed form) ---
    if "nifty50_ret" in out.columns:
        r_stock = close.pct_change()
        r_mkt = out["nifty50_ret"]
        cov = r_stock.rolling(IMOM_REGRESSION_DAYS,
                              min_periods=IMOM_REGRESSION_DAYS).cov(r_mkt)
        var = r_mkt.rolling(IMOM_REGRESSION_DAYS,
                            min_periods=IMOM_REGRESSION_DAYS).var(ddof=1)
        beta = cov / var.replace(0.0, np.nan)
        resid = r_stock - beta * r_mkt
        # sigma_36 of residuals; degenerate fits (var ~ 0) yield NaN
        sigma = resid.rolling(IMOM_REGRESSION_DAYS,
                              min_periods=IMOM_REGRESSION_DAYS).std(ddof=1)
        out["imom"] = resid.rolling(IMOM_SUM_DAYS,
                                    min_periods=IMOM_SUM_DAYS).sum() / sigma
    else:
        out["imom"] = np.nan
        out["imom_percentile"] = np.nan

    # --- Base / resistance levels ---
    out["base_low_90"] = low.rolling(BASE_RESISTANCE_DAYS,
                                     min_periods=BASE_RESISTANCE_DAYS).min()
    out["base_high_90"] = high.rolling(BASE_RESISTANCE_DAYS,
                                       min_periods=BASE_RESISTANCE_DAYS).max()
    out["high_52w"] = high.rolling(HIGH_52W_DAYS,
                                   min_periods=HIGH_52W_DAYS).max()

    # --- CR-2026-001 Issue 2: frozen breakout anchor (Setup 4) ---
    # The CR proposed the rolling base_high_90 as the anchor, but a rolling
    # max includes the breakout rally bars themselves (measured +3.5% above
    # the pre-breakout ceiling by day 1, +6.8% by day 7, worst case +91%).
    # Correct anchor: the PRIOR 90-session ceiling (window ending at B-1),
    # frozen at the breakout day B; `breakout_age` = sessions since the most
    # recent breakout (0 on day B, alive for 30 sessions, else NULL).
    prior_high = out["base_high_90"].shift(1)      # 90-session window ending t-1
    above = out["close_adj"] > BREAKOUT_CONFIRM_MULT * prior_high
    # Breakout = FRESH cross: the first close above the level, not every
    # session still holding above it (matches the probed SQL semantics).
    breakout = above & ~above.shift(1, fill_value=False)
    n = len(out)
    bo_idx = np.where(breakout.to_numpy(), np.arange(n), -1)
    last_bo = np.maximum.accumulate(bo_idx)        # most recent breakout session
    age = (np.arange(n) - last_bo).astype("float64")
    anchor_arr = prior_high.to_numpy(dtype="float64")
    anchor = np.where(last_bo >= 0,
                      anchor_arr[np.maximum(last_bo, 0)], np.nan)
    live = (last_bo >= 0) & (age <= 30)            # stale breakouts die silently
    out["breakout_age"] = np.where(live, age, np.nan)
    out["breakout_anchor_90"] = np.where(live, anchor, np.nan)

    # Prior session low (per-symbol; frame is date-ascending): Setup 1 uses
    # min(Low_T, Low_{T-1}) and Setup 5 uses the prior day low for stops.
    out["prev_low"] = low.shift(1)

    return out


def _attach_market_series(df: pd.DataFrame, store) -> pd.DataFrame:
    """Join NIFTY 50 daily returns and NIFTY 500 closes onto the bar frame.

    Returns are computed within the index series itself (before the merge) so
    they never leak across symbol boundaries in the bar frame.

    ponytail: falls back to NIFTY 50 as the RS benchmark when no NIFTY 500
    history is ingested; upgrade path is seeding market_indices fully from
    NSE archive downloads.
    """
    idx = store.con.execute("""
        SELECT index_name, date, close FROM market_indices
        WHERE index_name IN ('NIFTY 50', 'NIFTY 500') ORDER BY date
    """).df()
    if idx.empty:
        df["nifty50_ret"] = np.nan
        df["nifty500_close"] = np.nan
        return df
    idx["date"] = pd.to_datetime(idx["date"]).dt.date

    n50 = idx[idx["index_name"] == "NIFTY 50"][["date", "close"]] \
        .rename(columns={"close": "nifty50_close"}).drop_duplicates("date")
    n500 = idx[idx["index_name"] == "NIFTY 500"][["date", "close"]] \
        .rename(columns={"close": "nifty500_close"}).drop_duplicates("date")
    if n500.empty:  # degraded benchmark: use NIFTY 50 for RS
        n500 = n50.rename(columns={"nifty50_close": "nifty500_close"})

    n50["nifty50_ret"] = n50["nifty50_close"].pct_change()
    df = df.merge(n50, on="date", how="left").merge(n500, on="date", how="left")
    return df


def compute_features(store, on_date: Optional[Date] = None) -> pd.DataFrame:
    """Compute features for the PIT Top 500 universe up to `on_date` (default latest).

    Only sessions in [on_date - (window + buffer), on_date] are loaded: every
    feature has a bounded lookback, so warm-up bars outside that window cannot
    change any value on or after on_date. Bars are loaded for the full window
    regardless of universe membership (features need warm-up history); PIT
    membership is applied at read time by `load_features`.
    """
    if on_date is None:
        on_date = store.latest_date()
    if on_date is None:
        raise RuntimeError("daily_bars is empty; run `nse-cash sync` first.")
    on_date = pd.Timestamp(on_date).date()

    lookback_days = _MIN_SESSIONS + FEATURE_WINDOW_DAYS  # calendar-day buffer
    start = (pd.Timestamp(on_date) - pd.Timedelta(days=lookback_days)).date()

    symbols = [r[0] for r in store.con.execute(
        "SELECT DISTINCT symbol FROM pit_universe WHERE date BETWEEN ? AND ?",
        [start, on_date]).fetchall()]
    if not symbols:
        log.warning("features: empty PIT universe in [%s, %s]", start, on_date)
        return pd.DataFrame()

    bars = store.con.execute("""
        SELECT b.symbol, b.date, b.open, b.high, b.low, b.close, b.volume,
               b.deliverable_qty,
               coalesce(b.open_adj, b.open) AS open_adj,
               coalesce(b.high_adj, b.high) AS high_adj,
               coalesce(b.low_adj, b.low) AS low_adj,
               coalesce(b.close_adj, b.close) AS close_adj,
               coalesce(b.volume_adj, b.volume) AS volume_adj,
               coalesce(b.delivery_adj, b.deliverable_qty) AS delivery_adj
        FROM daily_bars b
        WHERE b.date BETWEEN ? AND ? AND b.symbol IN ?
        ORDER BY b.symbol, b.date
    """, [start, on_date, symbols]).df()

    if bars.empty:
        log.warning("features: no PIT bars in [%s, %s]", start, on_date)
        return pd.DataFrame()

    bars["date"] = pd.to_datetime(bars["date"]).dt.date
    bars = _attach_market_series(bars, store)

    pieces = []
    for symbol, g in bars.groupby("symbol", sort=False):
        g = g.sort_values("date").reset_index(drop=True)
        if len(g) >= 2:  # pct_change needs >= 2 rows to matter
            pieces.append(_add_symbol_features(g))
    if not pieces:
        return pd.DataFrame()

    out = pd.concat(pieces, ignore_index=True)
    keep = ["symbol", "date", "close_adj", "high_adj", "low_adj",
            "open_adj", "volume_adj", "delivery_adj", *FEATURE_COLS]
    out = out[[c for c in keep if c in out.columns]]

    # Cross-sectional percentiles: RS and iMOM across the universe per date
    out["rs_percentile"] = out.groupby("date")["rs_mansfield"].rank(pct=True)
    out["imom_percentile"] = out.groupby("date")["imom"].rank(pct=True)

    log.info("features: %d rows for %d symbols up to %s",
             len(out), out["symbol"].nunique(), on_date)
    return out


def refresh_features(store, on_date: Optional[Date] = None) -> int:
    """Compute and persist features; returns row count written."""
    df = compute_features(store, on_date)
    if df.empty:
        return 0
    n = store.upsert_features(df)
    log.info("features: upserted %d rows", n)
    return n


def load_features(store, on_date: Date) -> pd.DataFrame:
    """Point-in-time feature snapshot for one date, hydrated with that day's bars.

    The features table persists only derived indicators; the predicates also
    need the day's OHLCV/adj columns, which come straight from daily_bars
    (single source of truth — no duplicated price data in features). PIT
    universe membership is enforced here.
    """
    df = store.con.execute("""
        SELECT f.*,
               b.close AS close_raw,
               coalesce(b.open_adj, b.open) AS open_adj,
               coalesce(b.high_adj, b.high) AS high_adj,
               coalesce(b.low_adj, b.low) AS low_adj,
               coalesce(b.close_adj, b.close) AS close_adj,
               coalesce(b.volume_adj, b.volume) AS volume_adj,
               coalesce(b.delivery_adj, b.deliverable_qty) AS delivery_adj
        FROM features f
        JOIN pit_universe p ON p.symbol = f.symbol AND p.date = f.date
        JOIN daily_bars b ON b.symbol = f.symbol AND b.date = f.date
        WHERE f.date = ?
    """, [pd.Timestamp(on_date).date()]).df()
    return df
