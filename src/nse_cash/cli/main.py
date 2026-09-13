"""Master CLI entrypoint skeleton (Phase 1.6)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

import click
from rich.console import Console

from nse_cash import __version__

console = Console()

_CONTEXT = {"help_option_names": ["-h", "--help"]}


@click.group(context_settings=_CONTEXT)
@click.version_option(__version__, "--version", "-V", prog_name="nse-cash")
@click.option("--verbose", "-v", is_flag=True, help="Enable DEBUG logging.")
@click.option("--config", "config_path", type=click.Path(exists=True, path_type=Path),
              default=None, help="Path to config YAML.")
@click.pass_context
def cli(ctx: click.Context, verbose: bool, config_path: Optional[Path]) -> None:
    """NSE High-Conviction Cash Swing System (Kailash Nadh Pragmatic Architecture)."""
    from nse_cash.core.config import load_config
    from nse_cash.core.logger import setup_logging

    setup_logging(level="DEBUG" if verbose else "INFO")
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    ctx.obj["config_path"] = config_path
    ctx.obj["config"] = load_config(config_path)


def _placeholder(name: str) -> None:
    console.print(f"[yellow]{name}: not implemented yet (see ACTION_PLAN_8_PHASES.md).[/]")


@cli.command("sync")
@click.option("--from", "from_date", type=click.DateTime(["%Y-%m-%d"]), default=None,
              help="Historical backfill start date (YYYY-MM-DD).")
@click.option("--to", "to_date", type=click.DateTime(["%Y-%m-%d"]), default=None,
              help="Historical backfill end date (YYYY-MM-DD, default latest trading day).")
@click.option("--force", is_flag=True, help="Re-download dates already present in the database.")
@click.pass_context
def sync(ctx: click.Context, from_date, to_date, force: bool) -> None:
    """Download and ingest official daily Bhavcopy, Delivery (MTO) and Corporate Actions."""
    from nse_cash.cli.sync_cmd import run_sync
    run_sync(ctx, from_date, to_date, force)


@cli.command("scan")
@click.option("--date", "trade_date", type=click.DateTime(["%Y-%m-%d"]), default=None,
              help="Evaluate funnel for this date (default: latest ingested trading day).")
@click.pass_context
def scan(ctx: click.Context, trade_date) -> None:
    """Run the 4-stage funnel, rank S_runner, output the 10:00 AM Action Sheet."""
    from nse_cash.cli.scan_cmd import run_scan
    run_scan(ctx, trade_date)


@cli.command("ledger")
@click.pass_context
def ledger(ctx: click.Context) -> None:
    """Show active trades, open positions, tranche statuses and performance."""
    _placeholder("ledger")


@cli.command("backtest")
@click.option("--in-sample", "in_sample", is_flag=True, help="Run 2010-2022 in-sample backtest.")
@click.option("--walk-forward", "walk_forward", is_flag=True, help="Run 2023-Present walk-forward.")
@click.option("--full", "full", is_flag=True, help="Run 2010-Present with comparative report.")
@click.pass_context
def backtest(ctx: click.Context, in_sample: bool, walk_forward: bool, full: bool) -> None:
    """Run the 13-year in-sample & walk-forward point-in-time backtest engine."""
    _placeholder("backtest")


@cli.command("corporate-history")
@click.option("--limit", type=int, default=0, help="Only first N symbols (smoke test).")
@click.option("--audit-only", is_flag=True, help="Skip seeding; only run the gap audit.")
@click.option("--symbols", default="", help="Comma-separated symbol list (default: all).")
@click.pass_context
def corporate_history(ctx: click.Context, limit: int, audit_only: bool, symbols: str) -> None:
    """Seed historical split/bonus actions from yfinance; audit price gaps vs actions.

    One-time data prep for the 13-year backtest: the live NSE corporate-actions
    API retains only ~365 days, so deep history comes from Yahoo Finance splits,
    then `detect_unexplained_gaps` flags raw >25% overnight moves that no
    recorded action explains.
    """
    import json
    from pathlib import Path

    import pandas as pd

    from nse_cash.core.logger import console
    from nse_cash.data.corporate_history import (DISCONTINUITY_THRESHOLD,
                                                 detect_unexplained_gaps,
                                                 seed_corporate_actions_from_yfinance)
    from nse_cash.data.storage import MarketStore

    config = ctx.obj["config"]
    store = MarketStore(Path(config.paths.duckdb_path), read_only=False)
    try:
        sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()] or None
        if sym_list and limit:
            sym_list = sym_list[:limit]
        elif limit:
            sym_list = [r[0] for r in store.con.execute(
                "SELECT DISTINCT symbol FROM daily_bars ORDER BY symbol LIMIT ?",
                [limit]).fetchall()]
        if not audit_only:
            n = seed_corporate_actions_from_yfinance(store, symbols=sym_list)
            console.print(f"[green]seeded {n} corporate action row(s) from yfinance[/]")
            from nse_cash.core.adjuster import refresh_adjustments
            adjusted = refresh_adjustments(store)
            console.print(f"[green]adjustments recomputed for {adjusted} symbol(s)[/]")
        gaps = detect_unexplained_gaps(store, symbols=sym_list)
        report_path = Path("reports/corporate_action_audit.json")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "threshold_pct": DISCONTINUITY_THRESHOLD * 100,
            "total_gaps": int(len(gaps)),
            "unexplained": int((~gaps["explained"]).sum()) if not gaps.empty else 0,
            "gaps": gaps.to_dict(orient="records"),
        }
        report_path.write_text(json.dumps(payload, indent=2, default=str),
                               encoding="utf-8")
        console.print(f"[green]audit report -> {report_path}[/]")
        if not gaps.empty:
            shown = gaps.head(10)
            for _, g in shown.iterrows():
                tag = "[red]UNEXPLAINED[/]" if not g["explained"] else "[green]explained[/]"
                console.print(f"  {g['symbol']} {g['gap_date']} "
                              f"{g['prev_close']:.2f} -> {g['close']:.2f} "
                              f"({g['implied_factor']:.3f}x) {tag}")
            if len(gaps) > 10:
                console.print(f"  ... and {len(gaps) - 10} more (see report)")
    finally:
        store.close()


@cli.command("status")
@click.pass_context
def status(ctx: click.Context) -> None:
    """Verify data integrity, corporate action adjustments and market regime state."""
    from nse_cash.cli.status_cmd import run_status
    run_status(ctx)


def main() -> None:  # console_scripts entry point
    cli()


if __name__ == "__main__":
    cli()
