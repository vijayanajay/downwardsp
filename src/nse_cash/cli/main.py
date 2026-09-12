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
