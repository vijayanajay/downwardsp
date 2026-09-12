"""Corporate Action Adjustment Calculator (Phase 3.1).

For split/bonus ratio A:B on ex-date t_ex:
    AF_t = B / (A + B)   for t < t_ex        (1:1 bonus -> 0.5; 10->2 split -> 0.2)
    AF_t = 1.0           for t >= t_ex

    Price_adj(t)    = Price_raw(t)  * AF_t
    Volume_adj(t)   = Volume_raw(t) / AF_t
    Delivery_adj(t) = Deliverable(t)/ AF_t

Chained cascading corporate actions use cumulative multiplication:
    Cumulative_AF(t) = prod_k AF_k  over all actions with t_ex_k > t
"""

from __future__ import annotations

import logging
from typing import Optional

import duckdb
import pandas as pd

log = logging.getLogger("nse_cash.adjuster")


def adjustment_factor(ratio_a: float, ratio_b: float) -> float:
    """AF for a single action: B / (A + B)."""
    if ratio_a + ratio_b <= 0:
        raise ValueError("invalid ratio A:B")
    return ratio_b / (ratio_a + ratio_b)


def apply_adjustments(bars: pd.DataFrame, actions: pd.DataFrame) -> pd.DataFrame:
    """Backward-adjust OHLC, volume and delivery columns for one symbol.

    bars:  sorted ascending by date, columns open/high/low/close/volume,
           deliverable_qty.
    actions: DataFrame with ex_date, ratio_a, ratio_b (this symbol only).
    Returns bars with open_adj..delivery_adj populated.
    """
    out = bars.copy()
    n = len(out)
    if n == 0:
        return out
    dates = pd.to_datetime(out["date"])

    af = pd.Series(1.0, index=out.index)
    if not actions.empty:
        acts = actions.dropna(subset=["ex_date"]).copy()
        acts["ex_date"] = pd.to_datetime(acts["ex_date"])
        # Group by ex-date and compute cumulative factor per ex-date
        # so simultaneous actions (e.g. split + bonus) multiply rather than dropping one
        acts = acts.sort_values("ex_date")
        for ex_date, group in acts.groupby("ex_date"):
            cum_group_factor = 1.0
            for _, row in group.iterrows():
                factor = adjustment_factor(float(row["ratio_a"]), float(row["ratio_b"]))
                cum_group_factor *= factor
            af = af.where(dates >= ex_date, af * cum_group_factor)

    out["open_adj"] = out["open"] * af
    out["high_adj"] = out["high"] * af
    out["low_adj"] = out["low"] * af
    out["close_adj"] = out["close"] * af
    out["volume_adj"] = out["volume"] / af
    if "deliverable_qty" in out.columns:
        out["delivery_adj"] = pd.to_numeric(out["deliverable_qty"],
                                            errors="coerce") / af
    else:
        out["delivery_adj"] = pd.NA
    return out


# ---------------------------------------------------------------------------
# DuckDB integration
# ---------------------------------------------------------------------------

def refresh_adjustments(store) -> int:
    """Recompute adjusted columns for every symbol that has actions or stale
    adjustments. Returns number of symbols updated.

    ponytail: per-symbol Python loop for action-bearing symbols; the no-action
    identity case is one bulk SQL pass. If full-history reloads get slow, batch
    the per-symbol frames into a single registered union.
    """
    con: duckdb.DuckDBPyConnection = store.con

    # Bulk identity pass 1: symbols with no split/bonus actions
    con.execute("""
        UPDATE daily_bars b SET
            open_adj = b.open, high_adj = b.high, low_adj = b.low,
            close_adj = b.close, volume_adj = b.volume,
            delivery_adj = b.deliverable_qty
        WHERE b.close_adj IS NULL AND b.symbol NOT IN (
            SELECT DISTINCT symbol FROM corporate_actions
            WHERE action_type IN ('SPLIT', 'BONUS'))
    """)

    # Bulk identity pass 2: symbols with actions where new bars are after latest ex-date (AF = 1.0)
    con.execute("""
        UPDATE daily_bars b SET
            open_adj = b.open, high_adj = b.high, low_adj = b.low,
            close_adj = b.close, volume_adj = b.volume,
            delivery_adj = b.deliverable_qty
        WHERE b.close_adj IS NULL
          AND b.date >= (
              SELECT max(ca.ex_date) FROM corporate_actions ca
              WHERE ca.symbol = b.symbol AND ca.action_type IN ('SPLIT', 'BONUS')
          )
    """)

    # Per-symbol pass: only symbols with unapplied actions or missing pre-ex adjustments
    symbols = [r[0] for r in con.execute("""
        SELECT DISTINCT symbol FROM corporate_actions
        WHERE action_type IN ('SPLIT', 'BONUS') AND adjustment_factor IS NULL
        UNION
        SELECT DISTINCT b.symbol FROM daily_bars b
        JOIN (
            SELECT symbol, max(ex_date) AS max_ex
            FROM corporate_actions
            WHERE action_type IN ('SPLIT', 'BONUS')
            GROUP BY symbol
        ) ca ON b.symbol = ca.symbol
        WHERE b.close_adj IS NULL AND b.date < ca.max_ex
    """).fetchall()]
    if not symbols:
        log.info("adjuster: nothing further to do")
        return 0

    log.info("adjuster: per-symbol pass on %d symbols with actions", len(symbols))
    for symbol in symbols:
        bars = con.execute("""
            SELECT date, open, high, low, close, volume, deliverable_qty
            FROM daily_bars WHERE symbol = ? ORDER BY date
        """, [symbol]).df()
        actions = con.execute("""
            SELECT ex_date, ratio_a, ratio_b FROM corporate_actions
            WHERE symbol = ? AND action_type IN ('SPLIT', 'BONUS')
            ORDER BY ex_date
        """, [symbol]).df()
        if bars.empty:
            continue
        adjusted = apply_adjustments(bars, actions)
        con.register("_adj", adjusted)
        con.execute("""
            UPDATE daily_bars b SET
                open_adj = a.open_adj, high_adj = a.high_adj,
                low_adj = a.low_adj, close_adj = a.close_adj,
                volume_adj = a.volume_adj, delivery_adj = a.delivery_adj
            FROM _adj a
            WHERE b.symbol = ? AND b.date = a.date
        """, [symbol])
        con.unregister("_adj")
        # Record applied factors for auditability
        if not actions.empty:
            con.execute("""
                UPDATE corporate_actions SET adjustment_factor =
                    ratio_b / (ratio_a + ratio_b)
                WHERE symbol = ? AND action_type IN ('SPLIT', 'BONUS')
            """, [symbol])

    log.info("adjuster: %d symbols adjusted", len(symbols))
    return len(symbols)
