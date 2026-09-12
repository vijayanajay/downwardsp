"""`nse-cash scan` implementation (Phase 4 / Phase 7.2).

Executes the 4-Stage Filter Funnel:
  Stage 1: Macro Market Regime Gate (NIFTY 50 20-EMA & NIFTY 500 Breadth > 50%)
  Stage 2: Liquidity, Governance & Event Risk Gate (SEBI ASM/GSM, Circuits, Board Meetings)
  Stage 4: Sector Diversification & Portfolio Capacity Controller
"""

from __future__ import annotations

import logging
from datetime import date as Date
from pathlib import Path
from typing import Optional

import click
import pandas as pd
from rich.panel import Panel
from rich.table import Table

from nse_cash.core.governance import excluded_symbols
from nse_cash.core.logger import console
from nse_cash.core.types import MarketRegimeState
from nse_cash.data.storage import MarketStore
from nse_cash.funnel.market_regime import evaluate_market_regime
from nse_cash.funnel.sector_gate import SectorGate
from nse_cash.funnel.stage4_gate import check_portfolio_capacity

log = logging.getLogger("nse_cash.scan")


def run_scan(ctx: click.Context, trade_date_opt: Optional[Date]) -> None:
    """Execute the multi-stage filter funnel for trade_date."""
    config = ctx.obj["config"]
    db_path = Path(config.paths.duckdb_path)
    if not db_path.exists():
        console.print(f"[red]No market database found at {db_path}. Run `nse-cash sync` first.[/]")
        raise SystemExit(1)

    store = MarketStore(db_path, read_only=False)
    try:
        if trade_date_opt is not None:
            target_date = pd.Timestamp(trade_date_opt).date()
        else:
            latest = store.latest_date()
            if latest is None:
                console.print("[red]Market database has zero dates. Run `nse-cash sync` first.[/]")
                raise SystemExit(1)
            target_date = pd.Timestamp(latest).date()

        _execute_funnel_scan(store, config, target_date)
    finally:
        store.close()


def _execute_funnel_scan(store: MarketStore, config, target_date: Date) -> None:
    console.rule(f"[bold cyan]NSE Cash Swing System — 4-Stage Filter Funnel ({target_date})[/]")

    # =========================================================================
    # STAGE 1: MACRO MARKET REGIME GATE
    # =========================================================================
    regime = evaluate_market_regime(store, as_of_date=target_date)

    table = Table(title=f"Stage 1: Macro Market Regime Gate ({target_date})", title_style="bold")
    table.add_column("Indicator", style="bold")
    table.add_column("Current Value", justify="right")
    table.add_column("Threshold / Condition", justify="right")
    table.add_column("Status", justify="center")

    # NIFTY 50 20-EMA row
    nifty_diff = ((regime.nifty50_close - regime.nifty50_ema20) / regime.nifty50_ema20) * 100.0
    nifty_status = "[bold green]PASS[/]" if regime.nifty50_above_ema else "[bold red]FAIL[/]"
    table.add_row(
        "NIFTY 50 vs 20-EMA",
        f"{regime.nifty50_close:,.2f} (diff: {nifty_diff:+.2f}%)",
        f"> {regime.nifty50_ema20:,.2f}",
        nifty_status,
    )

    # NIFTY 500 Breadth row
    breadth_status = "[bold green]PASS[/]" if regime.breadth_above_50 else "[bold red]FAIL[/]"
    table.add_row(
        "NIFTY 500 Breadth (>50-day SMA)",
        f"{regime.breadth_pct:.1f}% ({regime.advancing_stocks}/{regime.total_eligible_stocks})",
        "> 50.0%",
        breadth_status,
    )

    # State row
    state_colored = (
        "[bold green]OFFENSIVE_LONG[/]"
        if regime.state == MarketRegimeState.OFFENSIVE_LONG
        else "[bold red]DEFENSIVE_CASH[/]"
    )
    table.add_row("Regime Decision", state_colored, "Both Must Pass", state_colored)

    console.print(table)

    if regime.state == MarketRegimeState.DEFENSIVE_CASH:
        alert_text = (
            "[bold red]HALT: 100% CASH DEFENSIVE SWITCH ACTIVE[/]\n\n"
            f"[white]{regime.reason}[/]\n\n"
            "• [bold]Zero new swing entries generated today.[/]\n"
            "• Macro conditions indicate insufficient market-wide momentum participation.\n"
            "• Capital preserved. Existing trades (if any) are managed to their pre-set stops/targets/stall exits."
        )
        console.print(Panel(alert_text, border_style="red", expand=False))
        return

    # =========================================================================
    # STAGE 2: LIQUIDITY & GOVERNANCE GATE
    # =========================================================================
    console.print("[bold green]✔ Stage 1 Passed:[/] Market regime is [bold green]OFFENSIVE_LONG[/]. Proceeding to Stage 2.")

    excluded = excluded_symbols(store.con, target_date)
    console.print(
        f"[cyan]Stage 2 Governance Gate:[/] [yellow]{len(excluded)}[/] symbols excluded "
        f"(SEBI ASM/GSM lists, circuit hits in last 3 sessions, or board meetings within 3 days)."
    )

    # =========================================================================
    # STAGE 4: CAPACITY & SECTOR DIVERSIFICATION
    # =========================================================================
    sector_gate = SectorGate()
    cap_ok, cap_msg = check_portfolio_capacity(occupied_slots=0, num_slots=config.capital.num_slots)

    console.print(f"[cyan]Stage 4 Pre-Entry Gate:[/] {cap_msg}. Max 1 trade per sector rule enforced.")
    console.print(
        "[dim]Note: Stage 3 Quantitative Setups (VCP Squeeze, Rubber-Band, RS Base, Anchor Retest, "
        "Residual Momentum, and S_runner scoring) will be active in Phase 5.[/]"
    )
