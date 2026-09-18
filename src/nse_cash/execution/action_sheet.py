"""10:00 AM Action Sheet renderer (Phase 7.3) — BRD §9.1 output.

Pure presentation over the shared pipeline's `DecisionResult`. Prices are
rounded to the NSE ₹0.05 tick because Kite rejects other levels; the sheet
prints rounded order prices next to raw math so the operator can see both.
"""

from __future__ import annotations

from datetime import date as Date

from rich.panel import Panel
from rich.table import Table

from nse_cash.backtest.engine import slot_quantity
from nse_cash.core.logger import console
from nse_cash.execution.ledger import round_to_tick
from nse_cash.funnel.sector_gate import UNKNOWN_SECTOR


def render_action_sheet(config, result, target_date: Date,
                        slot_capital: float | None = None) -> None:
    """Capital/slots header + Dual-GTT candidate table + Kite playbook."""
    slot_capital = slot_capital or config.capital.slot_capital
    num_slots = config.capital.num_slots
    occupied = result.occupied_slots
    max_gap = config.risk.max_gap_entry
    # CR-2026-001 Issue 4: Kite GTT OCO needs trigger AND limit. If the limit
    # leg equals the trigger, a gap-down open leaves the sell limit unexecuted.
    gtt_buffer = config.risk.gtt_stop_limit_buffer

    # ---- Capital & slots header (BRD §9.1) --------------------------------
    header = Table(show_header=False, box=None, pad_edge=False)
    header.add_column(style="bold")
    header.add_column(justify="right", style="bold")
    header.add_row("Portfolio Capital:", f"₹{config.capital.base_capital:,.0f}")
    header.add_row("Open Slots:",
                   f"{num_slots - occupied} of {num_slots} Available")
    header.add_row("Position Budget:", f"₹{slot_capital:,.0f}")
    console.print(Panel(header, title=f"[bold]DAILY TRADING ACTION SHEET "
                        f"(10:00 AM IST) — {target_date}[/]",
                        border_style="cyan", expand=False))

    if not result.candidates:
        console.print("[yellow]No setup matched any universe stock today "
                      "(zero new entries; 100% cash on Stage 3).[/]")
        _render_playbook(gtt_buffer)
        return

    # ---- Candidate table ---------------------------------------------------
    table = Table(title="10:00 AM Action Sheet — Dual-GTT Orders",
                  title_style="bold")
    table.add_column("Symbol", style="bold")
    table.add_column("Setup")
    table.add_column("Sector")
    table.add_column("S_runner", justify="right")
    table.add_column("Entry Ref", justify="right")
    table.add_column("Max Entry (+1.2% cap)", justify="right")
    table.add_column("T1 Target (+2%)", justify="right")
    table.add_column("T2 Target", justify="right")
    table.add_column("Stop Trigger", justify="right")
    table.add_column("Stop Limit", justify="right")
    table.add_column("Qty (T1/T2)", justify="right")

    for cand, _dec in result.accepted:
        qty = slot_quantity(config, cand.entry_ref, slot_capital)
        t1_qty, t2_qty = qty // 2, qty - qty // 2
        t1_px = round_to_tick(cand.entry_ref * (1 + cand.tranche1_target_pct))
        t2_px = round_to_tick(cand.entry_ref * (1 + cand.tranche2_target_pct))
        # Trigger/limit levels ride on the signal itself (identical in the
        # backtest and the book); the raw stop is the pre-CR-001 fallback.
        trigger = cand.stop_trigger if cand.stop_trigger > 0 else cand.structural_stop
        limit = cand.stop_limit if cand.stop_limit > 0 else \
            round_to_tick(cand.structural_stop * (1.0 - gtt_buffer))
        table.add_row(
            cand.symbol,
            cand.setup.value.replace("SETUP_", "S").replace("_", " ").title(),
            (_dec.sector or UNKNOWN_SECTOR) if _dec else UNKNOWN_SECTOR,
            f"{cand.s_runner:.2f}",
            f"₹{cand.entry_ref:,.2f}",
            f"₹{round_to_tick(cand.entry_ref * (1 + max_gap)):,.2f}",
            f"₹{t1_px:,.2f}",
            f"₹{t2_px:,.2f}",
            f"₹{trigger:,.2f}",
            f"₹{limit:,.2f}",
            f"{qty} ({t1_qty}/{t2_qty})",
        )
    console.print(table)

    if result.rejected:
        shown = 0
        for cand, reason in result.rejected:
            if shown >= 6:
                console.print(f"[dim]... and {len(result.rejected) - shown} "
                              "more rejections[/]")
                break
            console.print(f"[dim]rejected {cand.symbol} "
                          f"({cand.setup.value}): {reason}[/]")
            shown += 1

    _render_playbook(gtt_buffer)


def _render_playbook(gtt_buffer: float = 0.015) -> None:
    playbook = (
        "1. At 10:00 AM: verify price <= Max Entry. If LTP > Max Entry, the "
        "signal is INVALIDATED — do not place orders.\n"
        "2. On fill: place TWO independent GTT OCO sells:\n"
        "   GTT 1 (T1): target = T1 Target, trigger = Stop Trigger, limit = Stop Limit.\n"
        "   GTT 2 (T2): target = T2 Target, trigger = Stop Trigger, limit = Stop Limit.\n"
        f"   Kite GTT OCO: the trigger ARMS the order; the limit hits the book. "
        f"Enter Limit {gtt_buffer:+.1%} below Trigger so a gap-down open gets a "
        "guaranteed fill — never put the same price in both fields.\n"
        "3. Record the fill: nse-cash ledger --record-fill SYMBOL PRICE\n"
        "4. EOD 3:20 PM: nse-cash ledger SYMBOL=PRICE — if GTT 1 filled, "
        "modify GTT 2 stop to Breakeven (Entry); keep the same limit buffer.\n"
        "5. Stall rule: Day T+2 3:15 PM gain < +0.80% -> cancel GTTs, "
        "sell at market.\n"
        "6. Day 5 3:15 PM: exit anything remaining."
    )
    console.print(Panel(playbook, title="Manual Dual-GTT Playbook (Zerodha Kite)",
                        border_style="green", expand=False))


def render_position_book(config, ledger) -> None:
    """`nse-cash ledger` view: slots, cash, positions with GTT levels."""
    snap = ledger.cash_snapshot(config)
    header = Table(show_header=False, box=None, pad_edge=False)
    header.add_column(style="bold")
    header.add_column(justify="right")
    header.add_row("Base Capital:", f"₹{snap['base_capital']:,.2f}")
    header.add_row("Cash:", f"₹{snap['cash']:,.2f}")
    header.add_row("Invested (settled):", f"₹{snap['invested']:,.2f}")
    header.add_row("Realized P&L:", f"₹{snap['realized_pnl']:,.2f}")
    if snap["tax_paid"]:
        header.add_row("STCG accrued:", f"₹{snap['tax_paid']:,.2f}")
    header.add_row("Open Slots:",
                   f"{ledger.occupied_slots()} of {config.capital.num_slots}")
    console.print(Panel(header, title="[bold]Portfolio Ledger[/]",
                        border_style="cyan", expand=False))

    blocker = ledger.kill_blocker()
    if blocker:
        console.print(f"[bold red]ENTRY HALT:[/] {blocker}")

    opens = ledger.open_positions()
    if not opens:
        console.print("[yellow]No open positions.[/]")
        return

    table = Table(title="Open Positions — GTT Levels", title_style="bold")
    table.add_column("Slot", justify="center")
    table.add_column("Trade ID")
    table.add_column("Symbol", style="bold")
    table.add_column("Setup")
    table.add_column("Day", justify="center")
    table.add_column("Entry", justify="right")
    table.add_column("Qty", justify="right")
    table.add_column("T1 Target", justify="right")
    table.add_column("T2 Target", justify="right")
    table.add_column("Stop Trigger", justify="right")
    table.add_column("Stop Limit", justify="right")
    table.add_column("T1 Filled", justify="center")

    gtt_buffer = config.risk.gtt_stop_limit_buffer
    for t, pos in opens:
        if pos.entry_price_raw is None:
            continue
        stop = pos.pending_stop_raw if pos.pending_stop_raw is not None \
            else pos.structural_stop_raw
        table.add_row(
            str(t["slot"]),
            t["trade_id"],
            t["symbol"],
            t["setup_id"].replace("SETUP_", "S").replace("_", " ").title(),
            str(pos.day_index),
            f"₹{pos.entry_price_raw:,.2f}",
            f"{pos.open_qty}",
            f"₹{round_to_tick(pos.tranche1_target_raw):,.2f}",
            f"₹{round_to_tick(pos.tranche2_target_raw):,.2f}",
            f"₹{round_to_tick(stop):,.2f}",
            # CR-2026-001: the limit leg tracks whatever stop is standing,
            # including the breakeven trail — the buffer is relative.
            f"₹{round_to_tick(stop * (1.0 - gtt_buffer)):,.2f}",
            "yes" if pos.t1_filled else "no",
        )
    console.print(table)
