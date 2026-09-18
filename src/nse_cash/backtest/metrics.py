"""Performance analytics & tear sheet (Phase 6.5).

Pure aggregation over the engine's two artifacts — the event log and the
daily equity curve. No loop state, no counters: every number here is a
deterministic function of the artifacts, so two runs with identical inputs
produce byte-identical tear sheets.

The tear sheet leads with the boring tables (exit-reason histogram, per-setup
P&L, benchmark comparison) because "did each mechanism do what the doc says"
matters more than any single ratio. The BRD §10.1 targets get an honest
PASS/FAIL/MISSING verdict table (verify_brd_targets) — a verdict, not a gate:
no pytest asserts against a live-ingested walk-forward endpoint, because that
flake-trains people to ignore red. The operator reads the verdict every run.
"""

from __future__ import annotations

import logging
from datetime import date as Date

import numpy as np
import pandas as pd

log = logging.getLogger("nse_cash.backtest.metrics")

_TRADING_DAYS_PER_YEAR = 252.0


# ---------------------------------------------------------------------------
# Equity-curve math
# ---------------------------------------------------------------------------

def max_drawdown(equity: pd.Series) -> tuple[float, int]:
    """Return (max drawdown as a negative decimal, duration in trading days).

    Duration = longest time (in sessions) between a new peak and the recovery
    to that peak — the underwater spell, not just the trough distance.
    """
    if equity.empty:
        return 0.0, 0
    eq = equity.astype(float).reset_index(drop=True)
    peak = eq.cummax()
    dd = eq / peak - 1.0
    mdd = float(dd.min())
    # Longest underwater spell: sessions between successive peak updates.
    duration = 0
    current = 0
    peak_val = eq.iloc[0]
    for i in range(len(eq)):
        if eq.iloc[i] >= peak_val:
            peak_val = eq.iloc[i]
            current = 0
        else:
            current += 1
            duration = max(duration, current)
    return mdd, int(duration)


def cagr(equity: pd.Series, dates: pd.Series, periods_per_year: float = _TRADING_DAYS_PER_YEAR) -> float:
    """Annualized growth from the equity curve (post-fee, pre-tax unless the
    equity series itself is post-tax)."""
    if len(equity) < 2 or float(equity.iloc[0]) <= 0:
        return 0.0
    years = len(equity) / periods_per_year
    return float((equity.iloc[-1] / equity.iloc[0]) ** (1.0 / years) - 1.0)


def sharpe_ratio(equity: pd.Series,
                 periods_per_year: float = _TRADING_DAYS_PER_YEAR) -> float:
    """Annualized Sharpe of daily equity returns (risk-free not netted: cash
    already earns the liquid-fund yield inside the equity curve)."""
    if len(equity) < 3:
        return 0.0
    rets = equity.astype(float).pct_change().dropna()
    sd = rets.std(ddof=1)
    if sd == 0 or np.isnan(sd):
        return 0.0
    return float(rets.mean() / sd * np.sqrt(periods_per_year))


def sortino_ratio(equity: pd.Series,
                  periods_per_year: float = _TRADING_DAYS_PER_YEAR) -> float:
    """Annualized Sortino: downside deviation only."""
    if len(equity) < 3:
        return 0.0
    rets = equity.astype(float).pct_change().dropna()
    downside = rets[rets < 0]
    if downside.empty:
        return 0.0
    dd = float(np.sqrt((downside ** 2).mean()))
    if dd == 0:
        return 0.0
    return float(rets.mean() / dd * np.sqrt(periods_per_year))


def monthly_returns(equity: pd.DataFrame) -> dict[str, float]:
    """Month-over-month equity returns, keyed 'YYYY-MM'."""
    if equity.empty or "date" not in equity.columns:
        return {}
    eq = equity.copy()
    eq["date"] = pd.to_datetime(eq["date"])
    monthly = eq.set_index("date")["equity"].resample("ME").last().dropna()
    if monthly.empty:
        return {}
    prev = monthly.shift(1)
    first = eq["equity"].iloc[0]
    rets = (monthly - prev.fillna(first)) / prev.fillna(first)
    return {str(k.strftime("%Y-%m")): round(float(v), 6)
            for k, v in rets.items()}


# ---------------------------------------------------------------------------
# Benchmark
# ---------------------------------------------------------------------------

def benchmark_curve(store, index_name: str, start: Date, end: Date) -> pd.Series:
    """Normalized (base 1.0) buy-and-hold curve for one index over the range."""
    df = store.con.execute(
        "SELECT date, close FROM market_indices WHERE index_name = ? "
        "AND date BETWEEN ? AND ? ORDER BY date",
        [index_name, start, end]).df()
    if df.empty:
        return pd.Series(dtype=float)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    s = df.set_index("date")["close"].astype(float)
    return s / s.iloc[0]


def benchmark_metrics(store, index_name: str, start: Date, end: Date) -> dict:
    """CAGR + max drawdown for the buy-and-hold benchmark over the same span."""
    s = benchmark_curve(store, index_name, start, end)
    if s.empty or len(s) < 2:
        return {"benchmark_cagr": None, "benchmark_max_drawdown": None,
                "benchmark_name": index_name}
    years = len(s) / _TRADING_DAYS_PER_YEAR
    bg = float(s.iloc[-1] ** (1.0 / years) - 1.0)
    mdd, _ = max_drawdown(s)
    return {"benchmark_cagr": round(bg, 6), "benchmark_max_drawdown": round(mdd, 6),
            "benchmark_name": index_name}


# ---------------------------------------------------------------------------
# Trade aggregation
# ---------------------------------------------------------------------------

def exit_reason_counts(trades: pd.DataFrame) -> dict[str, int]:
    if trades.empty or "exit_reason" not in trades.columns:
        return {}
    return {str(k): int(v) for k, v in
            trades["exit_reason"].value_counts().items()}


def setup_breakdown(trades: pd.DataFrame) -> tuple[dict[str, float], dict[str, int]]:
    """Per-setup realized P&L totals and trade counts."""
    if trades.empty or "setup" not in trades.columns:
        return {}, {}
    filled = trades[trades["entry_price"].notna()] if "entry_price" in trades.columns else trades
    pnl = {str(k): round(float(v), 2) for k, v in
           filled.groupby("setup")["realized_pnl"].sum().items()}
    counts = {str(k): int(v) for k, v in filled.groupby("setup").size().items()}
    return pnl, counts


def trades_per_year(trades: pd.DataFrame) -> dict[str, int]:
    if trades.empty or "entry_date" not in trades.columns:
        return {}
    filled = trades[trades["entry_date"].notna()]
    if filled.empty:
        return {}
    years = pd.to_datetime(filled["entry_date"]).dt.year
    return {str(int(k)): int(v) for k, v in years.value_counts().sort_index().items()}


def win_stats(trades: pd.DataFrame) -> dict:
    """Win rate, profit factor, avg win/loss, expectancy over filled trades."""
    filled = trades[trades["entry_price"].notna()] if not trades.empty else trades
    if filled.empty:
        return {"trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
                "avg_win_net": 0.0, "avg_loss_net": 0.0, "expectancy_net": 0.0}
    pnl = filled["realized_pnl"].astype(float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    gross_win = float(wins.sum())
    gross_loss = float(-losses.sum())
    pf = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
    n = len(pnl)
    return {
        "trades": n,
        "win_rate": float(len(wins) / n),
        "profit_factor": float(pf),
        "avg_win_net": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss_net": float(losses.mean()) if len(losses) else 0.0,
        "expectancy_net": float(pnl.mean()),
    }


# ---------------------------------------------------------------------------
# BRD §10.1 walk-forward acceptance verdict (8.3) — verdict, not CI gate
# ---------------------------------------------------------------------------

BRD_TARGETS: tuple[tuple[str, ...], ...] = (
    # (metric key, label, floor, ceiling). Floor/ceiling None = unbounded.
    # Ranges are BRD §10.1 "Expected Realistic Performance Profile"; CAGR and
    # drawdown are one-sided (post-tax CAGR >= 14%, max DD < 8.5%).
    ("cagr_post_tax", "Post-Tax CAGR", 0.14, None),
    ("win_rate", "Win Rate", 0.48, 0.56),
    ("profit_factor", "Profit Factor", 1.55, 1.85),
    ("max_drawdown", "Max Drawdown", None, -0.085),
    ("avg_win_net_pct", "Avg Win (net %)", 2.40, 2.70),
    ("avg_loss_net_pct", "Avg Loss (net %)", -1.90, -1.80),
    ("expectancy_net_pct", "Expectancy (net %)", 0.40, 0.75),
)


def _fmt_bound(v: float | None, mode: str = "ratio_pct") -> str:
    """mode: 'ratio_pct' scales a fraction to % (0.05 -> +5.00%);
    'plain' prints as-is (profit factor 1.70); 'pct' prints a value that is
    ALREADY in percent (2.55 -> +2.55%)."""
    if v is None:
        return "—"
    if mode == "ratio_pct":
        return f"{v * 100:+.2f}%"
    if mode == "pct":
        return f"{v:+.2f}%"
    return f"{v:+.2f}"  # noqa: E701


def _fmt_mode(key: str) -> str:
    return "plain" if key == "profit_factor" else (
        "pct" if key.endswith("_pct") else "ratio_pct")


def brd_targets_verdict(metrics: dict) -> list[dict]:
    """Compare one run's metrics against the BRD §10.1 target ranges.

    Returns a row per target: metric, target text, actual, verdict
    (PASS / FAIL / MISSING). MISSING — including the trivially-small-sample
    case (zero filled trades) — is deliberate: an absent number must not look
    like a failing one, and neither must ever read as PASS.

    Direction conventions match the metrics dict exactly: win_rate/cagr are
    fractions (higher is better, lo..hi), max_drawdown is a negative fraction
    where CLOSER TO ZERO is better (floor lo is the disaster bound), and the
    *_net_pct rows are already in percent (BRD-style, e.g. 2.55 = +2.55%).
    """
    rows: list[dict] = []
    n_trades = int(metrics.get("trades") or 0)
    for key, label, lo, hi in BRD_TARGETS:
        value = metrics.get(key)
        if value is None or (key.endswith("_pct") and n_trades == 0):
            rows.append({"metric": label, "target": _target_text(lo, hi, key),
                         "actual": "—", "verdict": "MISSING"})
            continue
        v = float(value)
        if key == "max_drawdown":
            # Drawdown is a one-sided magnitude cap: -0.05 is BETTER than
            # -0.085. Whichever bound is present is the disaster limit.
            bound = hi if hi is not None else lo
            ok = bound is None or v >= bound
        else:
            ok = (lo is None or v >= lo) and (hi is None or v <= hi)
        mode = _fmt_mode(key)
        rows.append({"metric": label,
                     "target": _target_text(lo, hi, key),
                     "actual": _fmt_bound(v, mode),
                     "verdict": "PASS" if ok else "FAIL"})
    return rows


def _target_text(lo: float | None, hi: float | None, key: str = "") -> str:
    mode = _fmt_mode(key)
    if key == "max_drawdown":
        bound = hi if hi is not None else lo
        return f"{_fmt_bound(0.0, mode)} .. {_fmt_bound(bound, mode)}"
    if lo is not None and hi is not None:
        return f"{_fmt_bound(lo, mode)} .. {_fmt_bound(hi, mode)}"
    if lo is not None:
        return f">= {_fmt_bound(lo, mode)}"
    return f"< {_fmt_bound(hi, mode)}"


def verify_brd_targets(metrics: dict) -> bool:
    """True iff every BRD §10.1 row exists and passes. A MISSING row fails the
    verification but is rendered distinctly, so 'no data' never impersonates
    'bad performance'."""
    rows = brd_targets_verdict(metrics)
    return bool(rows) and all(r["verdict"] == "PASS" for r in rows)


# ---------------------------------------------------------------------------
# Full tear sheet
# ---------------------------------------------------------------------------

def compute_metrics(store, result, index_name: str = "NIFTY 500") -> dict:
    """Aggregate a BacktestResult into the tear-sheet dict.

    Post-tax CAGR adjusts final equity by cumulative STCG (tax paid at year
    boundaries, reported per FY by the STCG account). Pre-tax CAGR is the raw
    equity curve. Benchmark is buy-and-hold on the same dates.
    """
    eq = result.equity
    trades = result.trades
    dates = eq["date"] if not eq.empty else pd.Series(dtype=object)

    pre_tax = cagr(eq["equity"], dates) if not eq.empty else 0.0
    final_eq = float(eq["equity"].iloc[-1]) if not eq.empty else 0.0
    stcg_total = float(result.stcg.tax_paid)
    post_tax_final = final_eq - stcg_total
    post_tax = pre_tax
    if not eq.empty and final_eq > 0:
        # Scale the curve by the tax factor (tax accrues on year-end realized
        # gains; per-year scaling of the daily curve is over-engineering for
        # a number whose honest home is the FY table).
        post_tax = ((post_tax_final / float(eq["equity"].iloc[0]))
                    ** (1.0 / (len(eq) / _TRADING_DAYS_PER_YEAR)) - 1.0)

    mdd, mdd_days = max_drawdown(eq["equity"]) if not eq.empty else (0.0, 0)
    bench = benchmark_metrics(store, index_name, result.start, result.end)
    ws = win_stats(trades)
    setup_pnl, setup_counts = setup_breakdown(trades)

    # BRD §10.1 rows consume net-percent per trade: win_stats returns rupee
    # averages; the percent must be on CAPITAL DEPLOYED (entry_cost, the
    # friction-inclusive buy value of the whole position), not per-share
    # entry_price — dividing position P&L by a per-share price inflated every
    # row by the share count (~350x on a 5-lakh book; measured on the 2023+
    # walk-forward before the fix).
    filled = trades[trades["entry_price"].notna()] if not trades.empty else trades

    def _avg_net_pct(df: pd.DataFrame) -> float | None:
        if df.empty:
            return None
        if "entry_cost" not in df.columns or not (df["entry_cost"] > 0).all():
            return None  # no honest capital denominator -> no number
        return round(float((df["realized_pnl"] / df["entry_cost"]
                            * 100.0).mean()), 4)

    wins = filled[filled["realized_pnl"] > 0] if not filled.empty else filled
    losses = filled[filled["realized_pnl"] <= 0] if not filled.empty else filled
    avg_win_pct = _avg_net_pct(wins)
    avg_loss_pct = _avg_net_pct(losses)
    expect_pct = _avg_net_pct(filled)

    metrics = {
        "start_date": result.start,
        "end_date": result.end,
        "trades": ws["trades"],
        "win_rate": round(ws["win_rate"], 4),
        "profit_factor": (round(ws["profit_factor"], 4)
                          if np.isfinite(ws["profit_factor"]) else None),
        "cagr_pre_tax": round(pre_tax, 6),
        "cagr_post_tax": round(post_tax, 6),
        "post_tax_final_equity": round(post_tax_final, 2),
        "final_equity": round(final_eq, 2),
        "total_tax": round(stcg_total, 2),
        "total_interest": round(float(result.interest_paid), 2),
        "max_drawdown": round(mdd, 6),
        "max_drawdown_days": mdd_days,
        "avg_win_net": round(ws["avg_win_net"], 2),
        "avg_loss_net": round(ws["avg_loss_net"], 2),
        "expectancy_net": round(ws["expectancy_net"], 2),
        "avg_win_net_pct": avg_win_pct,
        "avg_loss_net_pct": avg_loss_pct,
        "expectancy_net_pct": expect_pct,
        # Risk-adjusted ratios are meaningless for a portfolio that never
        # trades (a flat cash curve's daily "returns" are rounding jitter);
        # report 0 rather than a five-digit Sharpe of pure noise.
        "sharpe": round(sharpe_ratio(eq["equity"]), 4)
        if not eq.empty and ws["trades"] > 0 else 0.0,
        "sortino": round(sortino_ratio(eq["equity"]), 4)
        if not eq.empty and ws["trades"] > 0 else 0.0,
        "benchmark_name": bench["benchmark_name"],
        "benchmark_cagr": bench["benchmark_cagr"],
        "benchmark_max_drawdown": bench["benchmark_max_drawdown"],
        "kill_switches": result.kill_count,
        "exit_reason_counts": exit_reason_counts(trades),
        "setup_pnl": setup_pnl,
        "setup_trades": setup_counts,
        "trades_per_year": trades_per_year(trades),
        "monthly_returns": monthly_returns(eq),
        "fy_tax_summary": list(result.stcg.fy_summary),
    }
    metrics["brd_targets"] = brd_targets_verdict(metrics)
    metrics["brd_all_pass"] = verify_brd_targets(metrics)
    return metrics
