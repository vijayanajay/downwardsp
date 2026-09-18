"""`nse-cash scan` implementation (Phase 4 / Phase 5 / Phase 7.2).

Renders the output of the shared decision pipeline
(`nse_cash.funnel.pipeline.decide_entries`) as the 10:00 AM Dual-GTT Action
Sheet. The funnel logic lives in the pipeline module so `scan` and the Phase 6
backtest engine replay the exact same decisions.
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

from nse_cash.core.logger import console
from nse_cash.core.types import MarketRegimeState
from nse_cash.data.storage import MarketStore
from nse_cash.execution.action_sheet import render_action_sheet
from nse_cash.execution.ledger import Ledger
from nse_cash.funnel.pipeline import decide_entries

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


def _live_book(config) -> tuple[int, set[str], int | None, Ledger]:
    """Open slots + claimed sectors from the production ledger.

    Slots are occupied by FILLED trades (money at risk) and by recorded
    signals awaiting tomorrow's fill (committed at scan time). A missing
    ledger file means an empty book; a read-only open failure falls back to
    read-only=False sqlite (schema creation on a fresh path). If the ledger
    cannot be opened at all, scan still runs — capacity just reads 0.
    """
    try:
        led = Ledger(config.paths.ledger_db_path)
        occupied = led.occupied_slots() + len(led.pending_signals())
        sectors = led.active_sectors()
        # ponytail: pending signals hold their signal-day sector but the
        # sector gate needs FILLED sectors only, so pending sectors do not
        # block a same-sector candidate tonight. Upgrade path: claim pending
        # sectors too if double-allocating one sector across two nights
        # becomes a real annoyance.
        blocker = led.kill_blocker()
        return occupied, sectors, blocker, led
    except Exception as exc:  # noqa: BLE001 - scan must never die on ledger
        log.warning("ledger unavailable (%s); scan proceeds with an empty book", exc)
        return 0, set(), None, None


def _execute_funnel_scan(store: MarketStore, config, target_date: Date) -> None:
    console.rule(f"[bold cyan]NSE Cash Swing System — 4-Stage Filter Funnel ({target_date})[/]")

    occupied, sectors, blocker, led = _live_book(config)
    if blocker:
        console.print(f"[bold red]ENTRY HALT:[/] {blocker} — signals below are "
                      "informational only. Do NOT place orders.")

    result = decide_entries(store, config, target_date,
                            occupied_slots=occupied,
                            active_sectors=sectors)
    regime = result.regime

    # =========================================================================
    # STAGE 1: MACRO MARKET REGIME GATE
    # =========================================================================
    table = Table(title=f"Stage 1: Macro Market Regime Gate ({target_date})",
                  title_style="bold")
    table.add_column("Indicator", style="bold")
    table.add_column("Current Value", justify="right")
    table.add_column("Threshold / Condition", justify="right")
    table.add_column("Status", justify="center")

    nifty_diff = ((regime.nifty50_close - regime.nifty50_ema20)
                  / regime.nifty50_ema20) * 100.0
    nifty_status = "[bold green]PASS[/]" if regime.nifty50_above_ema else "[bold red]FAIL[/]"
    table.add_row(
        "NIFTY 50 vs 20-EMA",
        f"{regime.nifty50_close:,.2f} (diff: {nifty_diff:+.2f}%)",
        f"> {regime.nifty50_ema20:,.2f}",
        nifty_status,
    )

    breadth_status = "[bold green]PASS[/]" if regime.breadth_above_50 else "[bold red]FAIL[/]"
    table.add_row(
        "NIFTY 500 Breadth (>50-day SMA)",
        f"{regime.breadth_pct:.1f}% ({regime.advancing_stocks}/{regime.total_eligible_stocks})",
        "> 50.0%",
        breadth_status,
    )

    state_colored = (
        "[bold green]OFFENSIVE_LONG[/]"
        if regime.state == MarketRegimeState.OFFENSIVE_LONG
        else "[bold red]DEFENSIVE_CASH[/]"
    )
    table.add_row("Regime Decision", state_colored, "Both Must Pass", state_colored)
    console.print(table)

    if led is not None:
        for p in led.integrity_problems(config):
            console.print(f"[red]LEDGER:[/] {p}")
        led.close()
        led = None  # Stage 1 rendering below needs nothing more from it

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

    console.print(
        f"[cyan]Stage 2 Governance Gate:[/] [yellow]{result.excluded_count}[/] symbols excluded "
        f"(SEBI ASM/GSM lists, circuit hits in last 3 sessions, or board meetings within 3 days)."
    )

    # =========================================================================
    # STAGE 3 & 4 via the shared pipeline; this module only renders.
    # =========================================================================
    _render_action_sheet(config, result, target_date)


def _render_action_sheet(config, result, target_date: Date) -> None:
    """10:00 AM Action Sheet: the pipeline's accepted candidates."""
    console.rule(f"[bold cyan]Stage 3 & 4: Candidates, S_runner & Action Sheet ({target_date})[/]")

    render_action_sheet(config, result, target_date)
