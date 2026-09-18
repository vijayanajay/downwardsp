"""The codified fill model (R3) — pure functions, no I/O, no store.

The contract `backtest/trade_manager.py` is written against, and that Phase 7's
ledger will reuse. Every rule that historically lives "somewhere in the loop"
is pinned here in one evaluation order, with a golden test for each:

  1.  Entry (Day T+1 open): fill at min(Open, Close_T * 1.012) when
      Open <= max_entry; gap-down opens are accepted at Open.
  2.  Open-through-stop: exit at Open_t, never at the stop price.
  3.  Intrabar pessimism: if a day's low touches the stop AND high touches a
      target, the stop wins. Ambiguous fills are flagged for the truth band.
  4.  T1 fills at its target on any session until filled. Breakeven arms at
      EOD — T2 protected from the next session only. Alternative runner
      policy: prev-day-low trail (`risk.runner_trail: prev_low`).
  5.  EOD: 48-hour stall (Day T+2 close < entry * 1.008) exits the whole
      position at that close; Day 5 3:15 PM time exit at close.
  6.  refresh_stops_eod: pending limit stop for the next session.
  7.  Corporate actions mid-trade: raw-space levels are scaled by the action's
      adjustment factor and quantity divided out; the pending stop absorbs the
      same adjustment. Applies from the ex-date bar onward.
  8.  Locked circuits (high == low): no counterparty, nothing fills.

Conventions:
  - Raw price space everywhere (friction, GTT levels, ledger are raw rupees).
    The caller converts once-adjusted signal levels into raw space at entry
    (scale = close_raw / close_adj), exactly as ranking.py does.
  - Days are 0-indexed sessions since signal day T: entry day = 1, stall
    check day = 2, hard time exit day = risk.max_holding_days (5).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date as Date
from enum import Enum

import pandas as pd

from nse_cash.core.types import ExitReason

log = logging.getLogger("nse_cash.fill_model")

_TOL = 1e-9  # touch comparisons: a rupee-price level is "hit" within this


# ---------------------------------------------------------------------------
# Events (the spine the Phase 7 ledger will fold into SQLite)
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    ENTRY_FILLED = "ENTRY_FILLED"
    ENTRY_REJECTED_GAP = "ENTRY_REJECTED_GAP"
    T1_TARGET = "T1_TARGET"
    T2_TARGET = "T2_TARGET"
    STOP_HIT = "STOP_HIT"
    STALL_EXITED = "STALL_EXITED"
    TIME_EXITED = "TIME_EXITED"
    KILL_SWITCH = "KILL_SWITCH"
    END_OF_RUN = "END_OF_RUN"
    BREAKEVEN_ARMED = "BREAKEVEN_ARMED"
    CORPORATE_ACTION_ADJUSTED = "CORPORATE_ACTION_ADJUSTED"
    CIRCUIT_FROZEN = "CIRCUIT_FROZEN"


@dataclass(frozen=True)
class TradeEvent:
    """One atomic thing that happened to one trade on one day."""

    event_type: EventType
    date: Date
    symbol: str
    tranche: int                      # 0 = trade-level, 1/2 = tranche-level
    price: float | None = None        # raw rupees; None for informational events
    qty: int = 0
    reason: str = ""                  # ExitReason.value on exits
    ambiguous_intrabar: bool = False  # truth-band flag: stop and target same bar
    detail: str = ""
    setup: str = ""                   # SetupID.value; per-setup attribution


@dataclass
class SimPosition:
    """Mutable in-memory state of one open trade inside the simulation."""

    trade_id: str
    symbol: str
    entry_ref_raw: float              # signal-day close (Close_T), raw
    max_entry_raw: float              # Close_T * (1 + max_gap_entry)
    structural_stop_raw: float
    tranche1_target_raw: float
    tranche2_target_raw: float
    tranche1_qty: int
    tranche2_qty: int
    setup: str = ""                         # SetupID.value; tagged onto events
    slot: int = 0                            # portfolio slot 1..4 (engine bookkeeping)
    sector: str | None = None                # sector gate value (engine bookkeeping)
    # Money/state bookkeeping (engine settles events into these; defaults keep
    # the golden tests constructing positions with signal levels only).
    settled: int = 0                         # events already folded into cash
    entry_cost: float = 0.0                  # value + buy friction, settled
    exit_proceeds: float = 0.0               # value - sell friction, settled
    exit_date: Date | None = None
    exit_reason: str = ""
    sell_days_charged: set = field(default_factory=set)  # dates already DP-charged
    events: list[TradeEvent] = field(default_factory=list)
    entry_price_raw: float | None = None
    entry_date: Date | None = None
    t1_filled: bool = False
    t2_open: bool = True
    pending_stop_raw: float | None = None   # limit stop in force next session
    day_index: int = 0                      # sessions since T; entry day = 1

    @property
    def is_open(self) -> bool:
        return self.entry_price_raw is not None and self.t2_open

    @property
    def open_qty(self) -> int:
        if not self.is_open:
            return 0
        return self.tranche2_qty if self.t1_filled else \
            self.tranche1_qty + self.tranche2_qty

    @property
    def breakeven_raw(self) -> float:
        """T2 breakeven = actual fill price (T1's profit covers round-trip costs)."""
        return self.entry_price_raw or 0.0


# ---------------------------------------------------------------------------
# 1. Entry day (Day T+1)
# ---------------------------------------------------------------------------

def simulate_entry_day(pos: SimPosition, bar, config,
                       action_factor: float | None = None) -> SimPosition:
    """Process the entry session from the morning open to the close.

    action_factor: cumulative adjustment factor of corporate actions whose
    ex-date is the ENTRY day itself (a bonus/split overnight between signal
    and entry). Applied to all signal levels before the gap check so a raw
    post-action open is compared against post-action levels.
    """
    o, h, l = float(bar["open"]), float(bar["high"]), float(bar["low"])
    c = float(bar["close"])
    day = _bar_date(bar)
    pos.day_index = 1

    if action_factor is not None and abs(action_factor - 1.0) > _TOL:
        _apply_corporate_action(pos, action_factor, day)

    # (a) Gap ceiling: reject, no trade ever opens.
    if o > pos.max_entry_raw + _TOL:
        pos.events.append(TradeEvent(
            EventType.ENTRY_REJECTED_GAP, day, pos.symbol, 0, price=o,
            detail=f"open {o:.2f} > max_entry {pos.max_entry_raw:.2f}"))
        return pos

    # (b) Fill at min(Open, max_entry); gap-down opens fill at Open.
    fill = min(o, pos.max_entry_raw)
    pos.entry_price_raw = fill
    pos.entry_date = day
    pos.pending_stop_raw = pos.structural_stop_raw
    pos.events.append(TradeEvent(
        EventType.ENTRY_FILLED, day, pos.symbol, 0, price=fill,
        qty=pos.tranche1_qty + pos.tranche2_qty))

    stop = pos.pending_stop_raw
    stop_touch = l <= stop + _TOL
    t1_touch = h >= pos.tranche1_target_raw - _TOL

    # (c) Immediate stop-out: opened at/below the stop -> the stop GTT placed
    # after the fill triggers at once and sells at market ~= Open.
    if o <= stop + _TOL:
        _exit_all(pos, o, ExitReason.STRUCTURAL_STOP_HIT, day)
        return pos

    # (d) Intrabar pessimism on entry day: stop and T1 target both inside the
    # bar -> the stop wins for the whole position.
    if stop_touch and t1_touch:
        _exit_all(pos, stop, ExitReason.STRUCTURAL_STOP_HIT, day, ambiguous=True)
        return pos
    if stop_touch:
        _exit_all(pos, stop, ExitReason.STRUCTURAL_STOP_HIT, day)
        return pos
    if t1_touch:
        _fill_t1(pos, day)

    # (e) Still holding -> arm tomorrow's stop (breakeven if T1 filled today).
    _ = c  # close unused on entry day (stall/day-5 start at day 2+)
    refresh_stops_eod(pos, bar, config)
    return pos


# ---------------------------------------------------------------------------
# 2. Continuation days (Day >= T+2)
# ---------------------------------------------------------------------------

def simulate_open_day(pos: SimPosition, bar, config,
                      action_factor: float | None = None) -> SimPosition:
    """Process one continuation session for a filled, open position.

    action_factor: cumulative adjustment factor of corporate actions whose
    ex-date is THIS bar's date (< 1.0 for a bonus/split), applied before any
    stop/target evaluation.
    """
    if not pos.is_open:
        return pos
    o, h, l = float(bar["open"]), float(bar["high"]), float(bar["low"])
    c = float(bar["close"])
    day = _bar_date(bar)
    pos.day_index += 1

    if action_factor is not None and abs(action_factor - 1.0) > _TOL:
        _apply_corporate_action(pos, action_factor, day)
        o, h, l, c = float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])

    # Locked circuit: no counterparty, nothing can execute; position frozen.
    if h <= l + _TOL and abs(o - h) <= _TOL:
        pos.events.append(TradeEvent(
            EventType.CIRCUIT_FROZEN, day, pos.symbol, 0, price=c,
            detail="band-locked bar (high == low): no fills"))
        return pos

    stop = pos.pending_stop_raw

    # Gap-through-stop: exit at the open, never at the stop price.
    if stop is not None and o < stop - _TOL:
        _exit_all(pos, o, ExitReason.STRUCTURAL_STOP_HIT, day)
        return pos

    stop_touch = stop is not None and l <= stop + _TOL
    t1_touch = (not pos.t1_filled) and h >= pos.tranche1_target_raw - _TOL
    t2_touch = h >= pos.tranche2_target_raw - _TOL

    # Intrabar pessimism: stop and any target inside the same bar -> stop wins.
    if stop_touch and (t1_touch or t2_touch):
        _exit_all(pos, stop, ExitReason.TRAILING_STOP_HIT, day, ambiguous=True)
        return pos
    if stop_touch:
        _exit_all(pos, stop, ExitReason.TRAILING_STOP_HIT, day)
        return pos
    if t1_touch:
        _fill_t1(pos, day)
    if pos.t1_filled and t2_touch:
        _exit_tranche2(pos, pos.tranche2_target_raw, ExitReason.TARGET_2_HIT, day)
        return pos

    # --- EOD checks, only if still holding ---
    if pos.day_index == 2 and \
            c < pos.breakeven_raw * (1.0 + config.risk.stall_threshold) - _TOL:
        _exit_all(pos, c, ExitReason.STALL_48H_HIT, day)
        return pos
    if pos.day_index >= config.risk.max_holding_days:
        _exit_all(pos, c, ExitReason.TIME_DAY5_HIT, day)
        return pos

    refresh_stops_eod(pos, bar, config)
    return pos


# ---------------------------------------------------------------------------
# 3. EOD stop refresh
# ---------------------------------------------------------------------------

def refresh_stops_eod(pos: SimPosition, bar, config) -> None:
    """Set the pending limit stop for the next session, per runner_trail policy.

    breakeven policy: a T1 fill on day D protects T2 from day D+1 onward; until
    then the structural stop stands. prev_low policy: T2 trails the prior
    session's low, floored at the structural stop (the disaster gate never
    loosens).
    """
    if not pos.is_open:
        return
    low = float(bar["low"])
    if config.risk.runner_trail == "prev_low":
        new_stop = max(pos.structural_stop_raw, low)
        if pos.pending_stop_raw is None or new_stop > pos.pending_stop_raw + _TOL:
            pos.pending_stop_raw = new_stop
        return
    if pos.t1_filled and pos.pending_stop_raw != pos.breakeven_raw:
        pos.pending_stop_raw = pos.breakeven_raw
        pos.events.append(TradeEvent(
            EventType.BREAKEVEN_ARMED, _bar_date(bar), pos.symbol, 0,
            price=pos.breakeven_raw, detail="T2 stop moved to breakeven at EOD"))


# ---------------------------------------------------------------------------
# 4. Portfolio-level force exit (kill switch) — the engine calls this
# ---------------------------------------------------------------------------

def force_exit(pos: SimPosition, price: float, day: Date,
               reason: ExitReason = ExitReason.KILL_SWITCH) -> SimPosition:
    """Liquidate whatever is open at `price` (kill switch / engine-level).

    `reason` defaults to KILL_SWITCH; the engine passes END_OF_RUN when
    flattening the book at the end of the data.
    """
    if not pos.is_open:
        return pos
    _exit_all(pos, price, reason, day)
    return pos


# ---------------------------------------------------------------------------
# internal transitions
# ---------------------------------------------------------------------------

def _fill_t1(pos: SimPosition, day: Date) -> None:
    pos.t1_filled = True
    pos.events.append(TradeEvent(
        EventType.T1_TARGET, day, pos.symbol, 1,
        price=pos.tranche1_target_raw, qty=pos.tranche1_qty,
        reason=ExitReason.TARGET_1_HIT.value, setup=pos.setup))


def _exit_tranche2(pos: SimPosition, price: float, reason: ExitReason,
                   day: Date, ambiguous: bool = False) -> None:
    pos.t2_open = False
    pos.pending_stop_raw = None
    event = _EVENT_FOR_REASON[reason]
    pos.events.append(TradeEvent(
        event, day, pos.symbol, 2, price=price, qty=pos.tranche2_qty,
        reason=reason.value, ambiguous_intrabar=ambiguous, setup=pos.setup))


_EVENT_FOR_REASON = {
    ExitReason.STRUCTURAL_STOP_HIT: EventType.STOP_HIT,
    ExitReason.TRAILING_STOP_HIT: EventType.STOP_HIT,
    ExitReason.TARGET_2_HIT: EventType.T2_TARGET,
    ExitReason.STALL_48H_HIT: EventType.STALL_EXITED,
    ExitReason.TIME_DAY5_HIT: EventType.TIME_EXITED,
    ExitReason.KILL_SWITCH: EventType.KILL_SWITCH,
    ExitReason.END_OF_RUN: EventType.END_OF_RUN,
}


def _exit_all(pos: SimPosition, price: float, reason: ExitReason, day: Date,
              ambiguous: bool = False) -> None:
    """Stop/stall/time/kill exits close T1 (if unfilled) and T2 together."""
    event = _EVENT_FOR_REASON[reason]
    if not pos.t1_filled:
        pos.events.append(TradeEvent(
            event, day, pos.symbol, 1, price=price,
            qty=pos.tranche1_qty, reason=reason.value,
            ambiguous_intrabar=ambiguous, setup=pos.setup))
    pos.t1_filled = False
    _exit_tranche2(pos, price, reason, day, ambiguous=ambiguous)


# ---------------------------------------------------------------------------
# 5. Mid-trade corporate actions
# ---------------------------------------------------------------------------

def _apply_corporate_action(pos: SimPosition, factor: float, day: Date) -> None:
    """Scale raw levels by `factor`, divide quantity out.

    factor = B/(A+B) (e.g. 1:1 bonus -> 0.5), exactly the Phase 3.1 backward
    adjustment. Raw stop/target/entry prices are pre-ex-date rupees; multiplying
    by the factor converts them into post-ex-date space for this bar onward.
    Quantities divide correspondingly (the volume-adjustment identity).
    """
    pos.entry_price_raw = round(pos.entry_price_raw * factor, 2)
    pos.entry_ref_raw = round(pos.entry_ref_raw * factor, 2)
    pos.max_entry_raw = round(pos.max_entry_raw * factor, 2)
    pos.structural_stop_raw = round(pos.structural_stop_raw * factor, 2)
    pos.tranche1_target_raw = round(pos.tranche1_target_raw * factor, 2)
    pos.tranche2_target_raw = round(pos.tranche2_target_raw * factor, 2)
    if pos.pending_stop_raw is not None:
        pos.pending_stop_raw = round(pos.pending_stop_raw * factor, 2)
    if factor > 0:
        pos.tranche1_qty = max(1, round(pos.tranche1_qty / factor))
        pos.tranche2_qty = max(1, round(pos.tranche2_qty / factor))
    pos.events.append(TradeEvent(
        EventType.CORPORATE_ACTION_ADJUSTED, day, pos.symbol, 0, price=factor,
        detail=f"factor={factor:.6f} applied to open levels"))


def action_factor_for_date(actions: pd.DataFrame, day: Date) -> float | None:
    """Cumulative adjustment factor for actions ex on `day`; None if none.

    `actions`: DataFrame with columns ex_date, adjustment_factor (as read from
    the corporate_actions table). Multiple actions on one date multiply.
    """
    if actions is None or actions.empty or "ex_date" not in actions.columns:
        return None
    factor = 1.0
    found = False
    for _, row in actions.iterrows():
        af = row.get("adjustment_factor")
        if af is None or pd.isna(af):
            continue
        if pd.Timestamp(row["ex_date"]).date() == day:
            factor *= float(af)
            found = True
    return factor if found else None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _bar_date(bar) -> Date:
    if hasattr(bar, "get") and "date" in bar:
        d = bar["date"]
    elif hasattr(bar, "name") and bar.name is not None and not pd.isna(bar.name):
        d = bar.name
    elif "date" in bar:
        d = bar["date"]
    else:
        d = getattr(bar, "name", None)
    return d if isinstance(d, Date) else pd.Timestamp(d).date()
