"""`nse-cash backtest` implementation (Phase 6.6).

Runs the deterministic replay, persists the two artifacts (event log + equity
curve) under reports/, and prints the tear sheet with Rich.
"""

from __future__ import annotations

import json
import logging
from datetime import date as Date
from pathlib import Path

import click
import pandas as pd
from rich.panel import Panel
from rich.table import Table

from nse_cash.core.logger import console, log_execution_time
from nse_cash.data.storage import MarketStore

log = logging.getLogger("nse_cash.cli.backtest")

BENCHMARK = "NIFTY 500"
DEFAULT_IN_SAMPLE_START = "2010-01-01"
IN_SAMPLE_END = "2022-12-31"


def _resolve_range(in_sample: bool, walk_forward: bool, full: bool,
                   start_opt, end_opt) -> tuple[Date, Date]:
    """CLI presets + explicit --start/--end (R5: replay March 2020 etc.)."""
    if start_opt is not None or end_opt is not None:
        start = pd.Timestamp(start_opt).date() if start_opt else None
        end = pd.Timestamp(end_opt).date() if end_opt else None
        if start is None or end is None:
            raise click.UsageError("--start and --end must be given together.")
        return start, end
    if full or (not in_sample and not walk_forward):
        return pd.Timestamp(DEFAULT_IN_SAMPLE_START).date(), Date.today()
    if in_sample:
        return pd.Timestamp(DEFAULT_IN_SAMPLE_START).date(), pd.Timestamp(IN_SAMPLE_END).date()
    return pd.Timestamp("2023-01-01").date(), Date.today()


def run_backtest_cmd(ctx: click.Context, in_sample: bool, walk_forward: bool,
                     full: bool, start_opt, end_opt,
                     carry_forward: bool, no_carry_forward: bool) -> None:
    config = ctx.obj["config"]
    db_path = Path(config.paths.duckdb_path)
    if not db_path.exists():
        console.print(f"[red]No market database found at {db_path}. "
                      "Run `nse-cash sync` first.[/]")
        raise SystemExit(1)

    if carry_forward and no_carry_forward:
        raise click.UsageError("--carry-forward and --no-carry-forward are mutually exclusive.")

    start, end = _resolve_range(in_sample, walk_forward, full, start_opt, end_opt)
    console.rule(f"[bold cyan]Phase 6 Backtest - {start} to {end}[/]")

    from nse_cash.backtest.engine import run_backtest, save_artifacts
    from nse_cash.backtest.metrics import compute_metrics

    carry = True if carry_forward else (False if no_carry_forward else None)

    store = MarketStore(db_path, read_only=False)
    try:
        with log_execution_time("backtest"):
            result = run_backtest(store, config, start, end,
                                  carry_forward_stcg=carry)
            metrics = compute_metrics(store, result, index_name=BENCHMARK)
            out_dir = Path("reports") / "backtest"
            save_artifacts(result, out_dir)
            _render_tear_sheet(metrics, result, out_dir)
    finally:
        store.close()


def _render_tear_sheet(metrics: dict, result, out_dir: Path) -> None:
    # --- Headline ------------------------------------------------------------
    table = Table(title="Tear Sheet - Headline", title_style="bold")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    table.add_column("Benchmark (buy & hold)", justify="right")

    fmt_pct = lambda v: f"{v * 100:+.2f}%" if v is not None else "n/a"  # noqa: E731
    table.add_row("Period", f"{metrics['start_date']} -> {metrics['end_date']}", "")
    table.add_row("CAGR (pre-tax)", fmt_pct(metrics["cagr_pre_tax"]), "")
    table.add_row("CAGR (post-tax)", fmt_pct(metrics["cagr_post_tax"]),
                  fmt_pct(metrics["benchmark_cagr"]))
    table.add_row("Max Drawdown", fmt_pct(metrics["max_drawdown"]),
                  fmt_pct(metrics["benchmark_max_drawdown"]))
    table.add_row("Max DD Duration", f"{metrics['max_drawdown_days']} sessions", "")
    table.add_row("Sharpe / Sortino", f"{metrics['sharpe']:.2f} / {metrics['sortino']:.2f}", "")
    table.add_row("Final Equity", f"Rs {metrics['final_equity']:,.0f}", "")
    table.add_row("Post-Tax Final Equity", f"Rs {metrics['post_tax_final_equity']:,.0f}", "")
    table.add_row("STCG Paid", f"Rs {metrics['total_tax']:,.0f}", "")
    table.add_row("Liquid-Fund Interest", f"Rs {metrics['total_interest']:,.0f}", "")
    console.print(table)

    # --- Trade stats ----------------------------------------------------------
    ts = Table(title="Trade Statistics", title_style="bold")
    ts.add_column("Metric", style="bold")
    ts.add_column("Value", justify="right")
    ts.add_row("Trades (filled)", str(metrics["trades"]))
    ts.add_row("Win Rate", f"{metrics['win_rate'] * 100:.1f}%")
    pf = metrics["profit_factor"]
    ts.add_row("Profit Factor", f"{pf:.2f}" if pf is not None else "inf")
    ts.add_row("Avg Win (net)", f"Rs {metrics['avg_win_net']:,.0f}")
    ts.add_row("Avg Loss (net)", f"Rs {metrics['avg_loss_net']:,.0f}")
    ts.add_row("Expectancy (net)", f"Rs {metrics['expectancy_net']:,.0f}")
    ts.add_row("Kill Switches", str(metrics["kill_switches"]))
    console.print(ts)

    # --- Exit-reason histogram: does each mechanism do what the doc says? ----
    er = Table(title="Exit Reason Histogram", title_style="bold")
    er.add_column("Reason", style="bold")
    er.add_column("Count", justify="right")
    er.add_column("Share", justify="right")
    total = sum(metrics["exit_reason_counts"].values()) or 1
    for reason, count in sorted(metrics["exit_reason_counts"].items(),
                                key=lambda kv: -kv[1]):
        er.add_row(reason, str(count), f"{count / total * 100:.1f}%")
    if metrics["exit_reason_counts"]:
        console.print(er)

    # --- Per-setup attribution ------------------------------------------------
    if metrics["setup_pnl"]:
        st = Table(title="Per-Setup Attribution", title_style="bold")
        st.add_column("Setup", style="bold")
        st.add_column("Trades", justify="right")
        st.add_column("Realized P&L (net)", justify="right")
        for setup, pnl in sorted(metrics["setup_pnl"].items(),
                                 key=lambda kv: -kv[1]):
            st.add_row(setup, str(metrics["setup_trades"].get(setup, 0)),
                       f"Rs {pnl:,.0f}")
        console.print(st)

    # --- Trades per year -------------------------------------------------------
    if metrics["trades_per_year"]:
        ty = Table(title="Trades per Year", title_style="bold")
        ty.add_column("Year", justify="right")
        ty.add_column("Trades", justify="right")
        for year, n in metrics["trades_per_year"].items():
            ty.add_row(year, str(n))
        console.print(ty)

    # --- STCG per financial year ------------------------------------------------
    if metrics.get("fy_tax_summary"):
        fy = Table(title="STCG by Financial Year (Apr-Mar)", title_style="bold")
        fy.add_column("FY", style="bold")
        fy.add_column("Trade P&L", justify="right")
        fy.add_column("Brought Fwd", justify="right")
        fy.add_column("Taxable", justify="right")
        fy.add_column("Tax", justify="right")
        for row in metrics["fy_tax_summary"]:
            fy.add_row(row["fy"], f"Rs {row['trade_pnl']:,.0f}",
                       f"Rs {row['brought_forward']:,.0f}",
                       f"Rs {row['taxable']:,.0f}", f"Rs {row['tax']:,.0f}")
        console.print(fy)

    # --- Monthly returns matrix (compact) ----------------------------------------
    mr = metrics.get("monthly_returns") or {}
    if mr:
        years = sorted({k[:4] for k in mr})
        mt = Table(title="Monthly Returns (%)", title_style="bold")
        mt.add_column("Year", style="bold")
        for m in ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]:
            mt.add_column(m, justify="right")
        for y in years:
            row = [y]
            for mi in range(1, 13):
                v = mr.get(f"{y}-{mi:02d}")
                row.append(f"{v * 100:+.1f}" if v is not None else "")
            mt.add_row(*row)
        console.print(mt)

    summary = {
        "metrics": {k: v for k, v in metrics.items()},
        "artifacts": {k: str(v) for k, v in _artifact_paths(out_dir).items()},
    }
    summary_path = out_dir / "tear_sheet.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str),
                            encoding="utf-8")
    console.print(Panel(
        f"Artifacts: events + equity + trades (Parquet) and tear_sheet.json\n"
        f"-> [bold]{out_dir}[/]\n\n"
        "The event log is the only artifact: every number above is pure\n"
        "aggregation over it. Slice it with DuckDB/pandas before re-running.",
        title="Reproducibility", border_style="cyan"))
    console.print(f"[green]tear sheet -> {summary_path}[/]")


def _artifact_paths(out_dir: Path) -> dict[str, Path]:
    paths = {}
    for name in ("backtest_events", "backtest_equity", "backtest_trades"):
        p = out_dir / f"{name}.parquet"
        if p.exists():
            paths[name] = p
    return paths
