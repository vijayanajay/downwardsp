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
from nse_cash.core.types import MarketRegimeState, SetupID
from nse_cash.data.storage import MarketStore
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


def _execute_funnel_scan(store: MarketStore, config, target_date: Date) -> None:
    console.rule(f"[bold cyan]NSE Cash Swing System — 4-Stage Filter Funnel ({target_date})[/]")

    result = decide_entries(store, config, target_date)
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

    accepted = result.accepted
    if not result.candidates:
        console.print("[yellow]No setup matched any universe stock today "
                      "(zero new entries; 100% cash on Stage 3).[/]")
        return

    slot_capital = config.capital.slot_capital
    table = Table(title="10:00 AM Action Sheet — Dual-GTT Orders", title_style="bold")
    table.add_column("Symbol", style="bold")
    table.add_column("Setup")
    table.add_column("S_runner", justify="right")
    table.add_column("Entry Ref", justify="right")
    table.add_column("Max Entry (+1.2%)", justify="right")
    table.add_column("T1 Target", justify="right")
    table.add_column("T2 Target", justify="right")
    table.add_column("Struct Stop", justify="right")
    table.add_column("Qty (T1/T2)", justify="right")

    max_gap = config.risk.max_gap_entry
    for cand, _dec in accepted:
        qty = int(slot_capital // cand.entry_ref)
        t1_qty, t2_qty = qty // 2, qty - qty // 2
        table.add_row(
            cand.symbol,
            cand.setup.value.replace("SETUP_", "S").replace("_", " ").title(),
            f"{cand.s_runner:.2f}",
            f"₹{cand.entry_ref:,.2f}",
            f"₹{cand.entry_ref * (1 + max_gap):,.2f}",
            f"₹{cand.entry_ref * (1 + cand.tranche1_target_pct):,.2f}",
            f"₹{cand.entry_ref * (1 + cand.tranche2_target_pct):,.2f}",
            f"₹{cand.structural_stop:,.2f} ({-cand.structural_stop_pct * 100:.2f}%)",
            f"{qty} ({t1_qty}/{t2_qty})",
        )
    console.print(table)

    if result.rejected:
        shown = 0
        for cand, reason in result.rejected:
            if shown >= 6:
                console.print(f"[dim]... and {len(result.rejected) - shown} more rejections[/]")
                break
            console.print(f"[dim]rejected {cand.symbol} ({cand.setup.value}): {reason}[/]")
            shown += 1

    playbook = (
        "1. At 10:00 AM: verify price <= Max Entry. Place Buy Limit for full Qty.\n"
        "2. On fill: place TWO independent GTT OCO sells:\n"
        "   GTT 1 (T1): target = T1 Target, stop = Structural Stop.\n"
        "   GTT 2 (T2): target = T2 Target, stop = Structural Stop.\n"
        "3. EOD 3:20 PM: if GTT 1 filled, modify GTT 2 stop to Breakeven (Entry).\n"
        "4. Stall rule: Day T+2 3:15 PM gain < +0.80% -> cancel GTTs, sell at market.\n"
        "5. Day 5 3:15 PM: exit anything remaining."
    )
    console.print(Panel(playbook, title="Manual Dual-GTT Playbook (Zerodha Kite)",
                        border_style="green", expand=False))
    if any(c.setup is SetupID.SETUP_4_ANCHOR_RETEST for c, _ in accepted):
        console.print("[dim]Setup 4 lines carry a tighter -2.00% stop gate; "
                      "all others -2.20%.[/]")
