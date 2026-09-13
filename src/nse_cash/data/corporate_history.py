"""Historical corporate-action coverage (R2).

Two independent pieces:

1. `seed_corporate_actions_from_yfinance`: one-time seeding of decades of
   split/bonus history from Yahoo Finance into the `corporate_actions` table.
   The live NSE corporate-actions API retains only ~365 days of events, which
   cannot support a 2010-2022 in-sample backtest. yfinance is imported lazily
   inside the fetcher so the package never hard-requires it (same pattern as
   scripts/download_data.py); tests inject a fake fetcher.

2. `detect_unexplained_gaps`: cross-check raw bhavcopy prices against recorded
   actions. A raw overnight close-to-close move beyond +/-25% cannot occur
   within NSE circuit bands (max 20%), so it implies a missing split/bonus
   action or bad data. Rights issues can also dilute prices, but rarely past
   the threshold; they surface as unexplained on purpose.
"""

from __future__ import annotations

import logging
import time
from datetime import date as Date

import numpy as np
import pandas as pd

log = logging.getLogger("nse_cash.corporate_history")

DISCONTINUITY_THRESHOLD = 0.25  # raw overnight move beyond circuit bands
ACTION_MATCH_WINDOW_DAYS = 3    # action ex_date within +/-N days of a gap

_ACTION_COLS = ["symbol", "ex_date", "purpose", "action_type",
                "ratio_a", "ratio_b", "adjustment_factor"]


# ---------------------------------------------------------------------------
# 1. yfinance split/bonus seeding
# ---------------------------------------------------------------------------

def split_rows(symbol: str, splits: pd.Series) -> pd.DataFrame:
    """yfinance splits Series -> corporate_actions rows for one symbol.

    yfinance values are shares-after : shares-before (2:1 split -> 2.0,
    reverse 1:10 -> 0.1). Our adjuster takes an explicit adjustment_factor =
    1/ratio (pre-ex prices are multiplied by it), which expresses reverse
    splits (AF > 1) that the ratio_a/ratio_b convention cannot. Indian bonus
    issues appear in the splits series with the same share-count math, so
    they are seeded as SPLIT rows too.
    """
    rows = []
    for ts, ratio in splits.items():
        r = float(ratio)
        if not np.isfinite(r) or r <= 0 or abs(r - 1.0) < 1e-9:
            continue  # identity / junk row
        ex_date = pd.Timestamp(ts).date()
        purpose = (f"YF SPLIT {r:g}:1" if r >= 1.0
                   else f"YF REVERSE SPLIT 1:{1.0 / r:g}")
        rows.append({
            "symbol": symbol,
            "ex_date": ex_date,
            "purpose": purpose,
            "action_type": "SPLIT",
            "ratio_a": r,               # informational (yfinance convention)
            "ratio_b": 1.0,
            "adjustment_factor": 1.0 / r,
        })
    return pd.DataFrame(rows, columns=_ACTION_COLS)


def fetch_splits_yahoo(symbol: str) -> pd.Series:
    """Full-history splits for an NSE symbol via yfinance (lazy import)."""
    import yfinance as yf  # noqa: PLC0415 - optional, one-time data prep only
    splits = yf.Ticker(f"{symbol}.NS").splits
    return splits if splits is not None else pd.Series(dtype=float)


def seed_corporate_actions_from_yfinance(
    store, symbols: list[str] | None = None,
    fetch_splits=fetch_splits_yahoo, sleep_s: float = 0.2,
) -> int:
    """Seed historical splits/bonuses into corporate_actions. Returns rows upserted.

    Idempotent: rows key on (symbol, ex_date, purpose) with a deterministic
    'YF ...' purpose string, so re-runs upsert over themselves.
    """
    if symbols is None:
        symbols = [r[0] for r in store.con.execute(
            "SELECT DISTINCT symbol FROM daily_bars ORDER BY symbol").fetchall()]
    if not symbols:
        log.warning("seed: no symbols in daily_bars")
        return 0

    frames: list[pd.DataFrame] = []
    fetched = failed = 0
    for i, sym in enumerate(symbols):
        try:
            splits = fetch_splits(sym)
        except Exception as exc:  # noqa: BLE001 - one bad ticker must not stop the seed
            log.debug("seed: %s failed: %s", sym, exc)
            failed += 1
        else:
            df = split_rows(sym, splits)
            if not df.empty:
                frames.append(df)
                fetched += 1
        if sleep_s and (i + 1) % 50 == 0:
            time.sleep(sleep_s)  # be polite to the free API
            log.info("seed: %d/%d symbols done (%d with actions)", i + 1, len(symbols), fetched)
    if not frames:
        log.info("seed: no split/bonus history found (%d symbols, %d failed)",
                 len(symbols), failed)
        return 0
    out = pd.concat(frames, ignore_index=True)
    n = store.upsert_corporate_actions(out)
    log.info("seed: %d rows from %d symbols (%d with actions, %d failed)",
             n, len(symbols), fetched, failed)
    return n


# ---------------------------------------------------------------------------
# 2. Price-discontinuity cross-check
# ---------------------------------------------------------------------------

def detect_unexplained_gaps(store, symbols: list[str] | None = None,
                            threshold: float = DISCONTINUITY_THRESHOLD,
                            window_days: int = ACTION_MATCH_WINDOW_DAYS) -> pd.DataFrame:
    """Raw overnight close-to-close moves beyond `threshold` vs recorded actions.

    Returns one row per flagged gap:
      symbol, gap_date, prev_close, close, implied_factor,
      action_ex_date, action_purpose, action_af, explained
    `explained` False = no split/bonus/demerger recorded near the gap: either
    a missing corporate action (seed and re-run) or suspect raw data.
    """
    q = ("SELECT symbol, date, close FROM daily_bars")
    params: list = []
    if symbols:
        q += " WHERE symbol IN ?"
        params.append(list(symbols))
    q += " ORDER BY symbol, date"
    bars = store.con.execute(q, params).df()
    actions = store.con.execute("""
        SELECT symbol, ex_date, purpose, adjustment_factor
        FROM corporate_actions
        WHERE action_type IN ('SPLIT', 'BONUS', 'DEMERGER')
        ORDER BY symbol, ex_date
    """).df()
    if bars.empty:
        return pd.DataFrame(columns=["symbol", "gap_date", "prev_close", "close",
                                     "implied_factor", "action_ex_date",
                                     "action_purpose", "action_af", "explained"])

    bars["date"] = pd.to_datetime(bars["date"]).dt.date
    bars["prev_close"] = bars.groupby("symbol")["close"].shift(1)
    bars["implied_factor"] = bars["close"] / bars["prev_close"].replace(0.0, np.nan)
    flags = bars[(bars["prev_close"].notna())
                 & ((bars["implied_factor"] > 1.0 + threshold)
                    | (bars["implied_factor"] < 1.0 - threshold))]

    acts_by_symbol: dict[str, pd.DataFrame] = {
        sym: g for sym, g in actions.assign(
            ex_date=pd.to_datetime(actions["ex_date"]).dt.date).groupby("symbol")
    } if not actions.empty else {}

    rows = []
    for _, f in flags.iterrows():
        match = None
        for _, a in acts_by_symbol.get(f["symbol"], pd.DataFrame()).iterrows():
            if abs((f["date"] - a["ex_date"]).days) <= window_days:
                match = a
                break
        rows.append({
            "symbol": f["symbol"],
            "gap_date": f["date"],
            "prev_close": float(f["prev_close"]),
            "close": float(f["close"]),
            "implied_factor": float(f["implied_factor"]),
            "action_ex_date": match["ex_date"] if match is not None else None,
            "action_purpose": match["purpose"] if match is not None else None,
            "action_af": (float(match["adjustment_factor"])
                          if match is not None and pd.notna(match["adjustment_factor"]) else None),
            "explained": match is not None,
        })
    out = pd.DataFrame(rows, columns=["symbol", "gap_date", "prev_close", "close",
                                      "implied_factor", "action_ex_date",
                                      "action_purpose", "action_af", "explained"])
    n_unexplained = int((~out["explained"]).sum()) if not out.empty else 0
    log.info("audit: %d gap(s) beyond +/-%.0f%%, %d unexplained",
             len(out), threshold * 100, n_unexplained)
    return out
