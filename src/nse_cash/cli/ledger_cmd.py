"""`nse-cash ledger` implementation (Phase 7.4).

The CLI is a thin shell: it parses arguments, opens the ledger and the market
store, and delegates every rule to `execution/ledger.py` (the fold) and
`fill_model.py` (the brain). Commands:

  ledger                       show the position book
  ledger --record-signal ...   tonight: commit accepted candidates + slots
  ledger --record-fill ...     10:00 AM: the broker fill receipt
  ledger --record-exit ...     any time: a tranche exit (GTT trigger hit)
  ledger --record-gap-rejected SYMBOL   10:00 AM: signal died at the gap ceiling
  ledger --check-eod           3:20 PM cockpit + after-sync reconciliation

Trade IDs follow the engine's convention: {SYMBOL}-{signal_date}.
"""

from __future__ import annotations

import logging
from datetime import date as Date, timedelta
from pathlib import Path

import click

from nse_cash.backtest.engine import slot_quantity
from nse_cash.core.logger import console
from nse_cash.core.types import SetupID
from nse_cash.data.storage import MarketStore
from nse_cash.execution.action_sheet import render_position_book
from nse_cash.execution.ledger import Ledger, round_to_tick
from nse_cash.funnel.pipeline import decide_entries

log = logging.getLogger("nse_cash.ledger_cmd")

def _open_ledger(config) -> Ledger:
    return Ledger(config.paths.ledger_db_path)


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def run_ledger_display(ctx: click.Context) -> None:
    config = ctx.obj["config"]
    led = _open_ledger(config)
    try:
        render_position_book(config, led)
        for p in led.integrity_problems(config):
            console.print(f"[red]LEDGER:[/] {p}")
        store = _open_store(config)
        if store is not None:
            try:
                for w in led.upcoming_corporate_actions(store):
                    console.print(f"[yellow]CORP-ACTION:[/] {w}")
            finally:
                store.close()
    finally:
        led.close()


def _open_store(config) -> MarketStore | None:
    db_path = Path(config.paths.duckdb_path)
    if not db_path.exists():
        return None
    return MarketStore(db_path, read_only=False)


# ---------------------------------------------------------------------------
# Record tonight's signals (the scan's output becomes the committed book)
# ---------------------------------------------------------------------------

def run_record_signals(ctx: click.Context, date_opt: Date | None) -> None:
    """Re-run the funnel and commit accepted candidates to the ledger.

    Same brain as `scan` (`decide_entries`), one flag apart: slots occupied by
    filled trades AND pending recorded signals, so re-running cannot
    double-commit the same signal or over-allocate past 4 slots.
    """
    config = ctx.obj["config"]
    db_path = Path(config.paths.duckdb_path)
    if not db_path.exists():
        console.print(f"[red]No market database at {db_path}. Run `nse-cash sync`.[/]")
        raise SystemExit(1)

    store = MarketStore(db_path, read_only=False)
    led = _open_ledger(config)
    try:
        trade_date = date_opt or Date.fromisoformat(str(store.latest_date()))
        occupied = led.occupied_slots() + len(led.pending_signals())
        existing = led.recorded_trade_ids()

        result = decide_entries(store, config, trade_date,
                                occupied_slots=occupied,
                                active_sectors=led.active_sectors())
        if result.regime.state.value == "DEFENSIVE_CASH":
            console.print("[red]Regime is DEFENSIVE_CASH — nothing recorded.[/]")
            return

        added = skipped = 0
        for cand, dec in result.accepted:
            trade_id = f"{cand.symbol}-{cand.date.isoformat()}"
            if trade_id in existing:
                skipped += 1
                continue
            slot = led.next_slot(config)
            if slot is None:
                break  # pipeline already respects capacity; belt and braces
            qty = slot_quantity(config, cand.entry_ref)
            led.add_trade(
                trade_id=trade_id, symbol=cand.symbol,
                setup_id=cand.setup.value, signal_date=cand.date, slot=slot,
                sector=dec.sector if dec.sector != "Unknown" else None,
                entry_ref=cand.entry_ref, structural_stop=cand.structural_stop,
                tranche1_target=cand.entry_ref * (1 + cand.tranche1_target_pct),
                tranche2_target=cand.entry_ref * (1 + cand.tranche2_target_pct),
                max_gap_pct=config.risk.max_gap_entry,
                tranche1_qty=qty // 2, tranche2_qty=qty - qty // 2,
                stop_limit=cand.stop_limit or None)
            added += 1
            stop_trig = round_to_tick(cand.structural_stop)
            stop_lim = round_to_tick(cand.stop_limit) if cand.stop_limit > 0 else \
                round_to_tick(cand.structural_stop
                              * (1.0 - config.risk.gtt_stop_limit_buffer))
            console.print(
                f"[green]recorded[/] {trade_id} slot {slot} — "
                f"T1 ₹{round_to_tick(cand.entry_ref * (1 + cand.tranche1_target_pct)):,.2f} "
                f"T2 ₹{round_to_tick(cand.entry_ref * (1 + cand.tranche2_target_pct)):,.2f} "
                f"stop trigger ₹{stop_trig:,.2f} / limit ₹{stop_lim:,.2f} "
                f"max entry ₹{round_to_tick(cand.entry_ref * (1 + config.risk.max_gap_entry)):,.2f}")
        console.print(f"[bold]{added} signal(s) recorded, {skipped} already "
                      "in the book.[/] Place orders from the sheet; record "
                      "fills at 10:00 AM.")
    finally:
        store.close()
        led.close()


# ---------------------------------------------------------------------------
# Receipts
# ---------------------------------------------------------------------------

def run_record_fill(ctx: click.Context, symbol: str, price: float,
                    date_opt: Date | None) -> None:
    config = ctx.obj["config"]
    led = _open_ledger(config)
    try:
        d = date_opt or Date.today()
        matches = [t for t in led.pending_signals() if t["symbol"] == symbol]
        if not matches:
            raise SystemExit(f"no pending signal for {symbol}; "
                             "run `ledger --record-signal` first (or check the symbol)")
        if len(matches) > 1:
            ids = ", ".join(t["trade_id"] for t in matches)
            raise SystemExit(f"ambiguous symbol {symbol}: {ids}; "
                             "re-record with a unique symbol or delete stale rows")
        trade = matches[0]
        led.record_fill(trade["trade_id"], price, d)
        stop = trade["structural_stop"]
        stop_lim = trade.get("stop_limit") or \
            round_to_tick(stop * (1.0 - config.risk.gtt_stop_limit_buffer))
        console.print(
            f"[green]fill recorded[/] {trade['trade_id']}: {price} × "
            f"{trade['tranche1_qty'] + trade['tranche2_qty']} on {d}. "
            f"Place the two GTT OCO sells per the sheet: trigger ₹{round_to_tick(stop):,.2f} "
            f"/ limit ₹{round_to_tick(stop_lim):,.2f} on both.")
    finally:
        led.close()


def run_record_exit(ctx: click.Context, symbol: str, price: float,
                    reason: str, date_opt: Date | None) -> None:
    config = ctx.obj["config"]
    led = _open_ledger(config)
    try:
        d = date_opt or Date.today()
        matches = [t for t in led.open_trades() if t["symbol"] == symbol]
        if not matches:
            raise SystemExit(f"no open trade for {symbol}")
        if len(matches) > 1:
            raise SystemExit(f"ambiguous: multiple open trades for {symbol}")
        trade = matches[0]
        seq = led.record_exit(trade["trade_id"], price, d, reason.upper())
        pos = led.position(trade)
        if pos.is_open:
            be_lim = round_to_tick(pos.entry_price_raw
                                   * (1.0 - config.risk.gtt_stop_limit_buffer))
            console.print(
                f"[green]recorded[/] (seq {seq}). {trade['trade_id']}: T1 filled — "
                f"modify GTT 2 stop trigger to breakeven ₹{round_to_tick(pos.entry_price_raw):,.2f} "
                f"/ limit ₹{be_lim:,.2f}.")
        else:
            console.print(
                f"[green]recorded[/] (seq {seq}). {trade['trade_id']} closed at "
                f"₹{price:,.2f} ({reason.upper()}). Slot {trade['slot']} freed; "
                "sector released for the next scan.")
    finally:
        led.close()


def run_record_gap_rejected(ctx: click.Context, symbol: str) -> None:
    """The 10:00 AM gap ceiling killed the signal: release slot + sector."""
    config = ctx.obj["config"]
    led = _open_ledger(config)
    try:
        matches = [t for t in led.pending_signals() if t["symbol"] == symbol]
        if not matches:
            raise SystemExit(f"no pending signal for {symbol}")
        trade = matches[0]
        led.append_event(trade["trade_id"], "ENTRY_REJECTED_GAP", Date.today(),
                         tranche=0, detail="gap ceiling at 10:00 AM; never filled")
        led.delete_trade(trade["trade_id"], "gap-rejected at 10:00 AM")
        console.print(f"[yellow]signal {trade['trade_id']} released "
                      "(gap-rejected); slot/sector freed.[/]")
    finally:
        led.close()


def run_delete_trade(ctx: click.Context, trade_id: str, reason: str) -> None:
    """Tombstone a wrong trade row (receipt-correction loop for DRIFT).

    Facts are never destroyed: the rows stay with deleted=1 and the reason is
    noted. Re-record the trade correctly afterwards (record-signal/fill/exit)."""
    config = ctx.obj["config"]
    led = _open_ledger(config)
    try:
        trade = led.get_trade(trade_id)
        if trade is None:
            raise SystemExit(f"unknown trade {trade_id}")
        led.delete_trade(trade_id, reason)
        console.print(f"[yellow]deleted[/] {trade_id} ({reason}). Facts kept "
                      "(auditable). Re-record the trade if it was real.")
    finally:
        led.close()


# ---------------------------------------------------------------------------
# The 3:20 PM cockpit + after-sync reconciliation
# ---------------------------------------------------------------------------

def run_check_eod(ctx: click.Context, ltp_tokens: tuple[str, ...] | None,
                  skip_reconcile: bool) -> None:
    """`ledger [SYMBOL=PRICE ...] [--skip-reconcile]`: the 3:20 PM cockpit.

    LTPs arrive as SYMBOL=PRICE tokens so a swapped pair of numbers can never
    silently mis-prompt the stall/breakeven routine (the old positional
    mapping looked authoritative while being wrong).
    """
    config = ctx.obj["config"]
    store = _open_store(config)
    led = _open_ledger(config)
    try:
        opens = led.open_positions()
        if not opens:
            console.print("[yellow]No open positions. Nothing to check.[/]")
            return

        if ltp_tokens:
            _intraday_prompts(config, led, opens,
                              _parse_ltps(ltp_tokens, opens))
        elif store is not None:
            for w in led.upcoming_corporate_actions(store):
                console.print(f"[yellow]CORP-ACTION:[/] {w}")
        if store is not None and not skip_reconcile:
            _reconcile_and_report(store, config, led)
        _kill_switch_check(config, led, store)
    finally:
        led.close()
        if store is not None:
            store.close()


def _parse_ltps(tokens: tuple[str, ...],
                opens: list[tuple[dict, object]]) -> dict[str, float]:
    """SYMBOL=PRICE tokens -> {symbol: ltp}. Trust boundary: a malformed or
    unknown token must stop the run, not silently skip a position."""
    known = [t["symbol"] for t, _pos in opens]
    ltps: dict[str, float] = {}
    for tok in tokens:
        sym, sep, px = tok.partition("=")
        if not sep:
            raise SystemExit(f"bad LTP {tok!r}: use SYMBOL=PRICE "
                             "(e.g. `nse-cash ledger SHOCK=101.20`)")
        sym = sym.strip().upper()
        if sym not in known:
            raise SystemExit(f"unknown symbol {sym}; open positions: "
                             f"{', '.join(known) or 'none'}")
        try:
            ltps[sym] = float(px)
        except ValueError:
            raise SystemExit(f"bad price for {sym}: {px!r}")
    return ltps


def _intraday_prompts(config, led: Ledger,
                      opens: list[tuple[dict, object]],
                      ltps: dict[str, float]) -> None:
    """Per-position 3:20 PM routine from live LTPs (SYMBOL=PRICE tokens)."""
    console.rule("[bold cyan]3:20 PM EOD Routine[/]")
    stall = config.risk.stall_threshold
    for t, pos in opens:
        if pos.entry_price_raw is None:
            continue
        ltp = ltps.get(t["symbol"])
        if ltp is None:
            console.print(f"[dim]{t['trade_id']}: no LTP given, skipped[/]")
            continue
        gain = ltp / pos.entry_price_raw - 1.0
        console.print(
            f"\n[bold]{t['trade_id']}[/] day {pos.day_index} · "
            f"LTP ₹{ltp:,.2f} ({gain * 100:+.2f}%) · stop ₹{round_to_tick(pos.pending_stop_raw or pos.structural_stop_raw):,.2f}")
        if pos.t1_filled:
            console.print(
                f"  → T1 filled: GTT 2 stop must be breakeven "
                f"₹{round_to_tick(pos.entry_price_raw):,.2f}. "
                "[green]Modify GTT 2 trigger if not done.[/]")
        elif pos.day_index == 2 and gain < stall:
            console.print(
                "  → [bold red]STALL RULE DAY T+2:[/] gain below "
                f"+{stall * 100:.2f}% — cancel both GTTs and sell at market. "
                "Record it: --record-exit " + t["symbol"] + " LTP STALL_EXITED")
        if pos.day_index >= config.risk.max_holding_days:
            console.print("  → [bold red]DAY 5:[/] hard time exit — cancel "
                          "GTTs, sell at market. Record it: --record-exit "
                          + t["symbol"] + " LTP TIME_EXITED")


def _reconcile_and_report(store: MarketStore, config, led: Ledger) -> None:
    """After-sync pass: replay real bars, diff against recorded events."""
    from nse_cash.execution.ledger import reconcile
    console.rule("[bold cyan]Reconciliation (bars vs recorded events)[/]")
    drift = reconcile(store, config, led)
    if drift:
        for d in drift:
            console.print(f"[red]DRIFT:[/] {d}")
        console.print("[dim]Fix the book with --record-exit / --record-fill, "
                      "or investigate the data.[/]")
    else:
        console.print("[green]Book matches the market — no drift.[/]")


def _kill_switch_check(config, led: Ledger, store: MarketStore | None) -> None:
    """HWM drawdown from close marks; live counterpart of engine rule 4."""
    if store is None:
        return
    marks: dict[str, float] = {}
    for t, _pos in led.open_positions():
        row = store.con.execute(
            "SELECT close FROM daily_bars WHERE symbol = ? "
            "ORDER BY date DESC LIMIT 1", [t["symbol"]]).fetchone()
        if row is not None:
            marks[t["symbol"]] = float(row[0])
    if not marks and not led.open_positions():
        return
    hwm = led.update_hwm(config, marks)
    eq = led.equity(config, marks)
    dd = (hwm - eq) / hwm if hwm > 0 else 0.0
    console.print(f"[cyan]Equity ₹{eq:,.2f} · HWM ₹{hwm:,.2f} · "
                  f"drawdown {dd * 100:.2f}% (kill at "
                  f"{config.risk.kill_switch_drawdown * 100:.1f}%)[/]")
    if dd >= config.risk.kill_switch_drawdown:
        # ponytail: 14 calendar days approximates the engine's 10 TRADING-day
        # cooldown; upgrade path: count sessions from daily_bars.
        until = Date.today() + timedelta(days=14)
        led.arm_cooldown(until)
        console.print(
            "[bold red]KILL SWITCH: drawdown breached. Liquidate all open "
            f"positions at NEXT OPEN, zero new entries until {until}.[/]")
    blocker = led.kill_blocker()
    if blocker:
        console.print(f"[red]ENTRY HALT:[/] {blocker}")
