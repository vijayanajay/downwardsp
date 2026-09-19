"""The codified fill model (R3) — pure functions, no I/O, no store.

The contract `backtest/trade_manager.py` is written against, and that Phase 7's
ledger will reuse. Every rule that historically lives "somewhere in the loop"
is pinned here in one evaluation order, with a golden test for each:

  1.  Entry (Day T+1 open): fill at min(Open, Close_T * 1.012) when
      Open <= max_entry; gap-down opens are accepted at Open.
  2.  Gap-through-stop (CR-2026-001): the stop is a Kite GTT OCO — trigger at
      the structural stop, LIMIT leg `gtt_stop_limit_buffer` (1.5%) BELOW it.
      On an overnight gap below the trigger:
        a. Open >= limit leg  -> the sell limit fills at the open.
        b. Open < limit leg but the day's high recovers to it -> fills at the
           limit (the order sits working until price reaches it).
        c. Gap beyond the limit leg all day -> GTT_STOP_UNFILLED: the position
           survives the session, and the operator's stall/time rules exit it
           at close. The stop stays armed for the next session.
      An intraday stop touch (open above the trigger) still fills at the
      trigger — the limit leg only matters on gaps.
  3.  Intrabar pessimism: if a day's low touches the stop AND high touches a
      target, the stop wins. Ambiguous fills are flagged for the truth band.
  4.  T1 fills at its target on any session until filled. Breakeven arms at
      EOD — T2 protected from the next session only. Alternative runner
      policy: prev-day-low trail (`risk.runner_trail: prev_low`).
      X1 knob: `risk.t1_enabled: false` removes the tranche entirely — one
      full-size position rides to T2/stop/time (no T1 event, no breakeven arm).
  5.  EOD: 48-hour stall (Day T+2 close < entry * 1.008) exits the whole
      position at that close; Day 5 3:15 PM time exit at close.
      X1 knob: `risk.time_stop_enabled: false` removes both — positions live
      only by stop/target; `risk.max_holding_days` remains the sole time exit.
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

from nse_cash.core.tick import round_to_tick
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
    # CR-2026-001 Issue 4: gap opened below the stop's LIMIT leg and never
    # recovered — the GTT stop did not execute; position survives the session.
    GTT_STOP_UNFILLED = "GTT_STOP_UNFILLED"


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
    # Stop LIMIT leg (GTT field 2), raw rupees; None = derive from config's
    # gtt_stop_limit_buffer. Carried from the CandidateSignal so live book,
    # sheet and backtest share one number.
    stop_limit_raw: float | None = None
    day_index: int = 0                      # sessions since T; entry day = 1
    # CR-2026-003 exit-geometry telemetry (measurement only; never gates
    # behavior): running MFE/MAE vs the raw fill price as decimals, plus the
    # day_index of each extreme. mae_pct is the ADVERSE move as a positive
    # number ((ref - low) / ref); mfe_pct is favorable and >= 0. Exit-day
    # prices print too (the exit IS an excursion). Gap-rejected entries
    # (entry_price_raw None) are never tracked.
    mfe_pct: float = 0.0
    mae_pct: float = 0.0
    mfe_day: int = 0
    mae_day: int = 0

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

def _stop_limit_raw(pos: SimPosition, stop: float, config) -> float:
    """The standing stop's LIMIT leg: the recorded one, or config's buffer."""
    if pos.stop_limit_raw is not None and pos.stop_limit_raw > 0:
        return pos.stop_limit_raw
    return round_to_tick(stop * (1.0 - config.risk.gtt_stop_limit_buffer))


def _gap_through_stop(pos: SimPosition, o: float, h: float, stop: float,
                      config, day: Date) -> bool:
    """GTT stop semantics when the open gaps below the stop trigger.

    Returns True if the position exited (fill at open or at the limit leg on
    an intraday recovery); False when the gap stayed beyond the limit leg all
    day — GTT_STOP_UNFILLED, position survives (stall/time rules exit it).
    """
    limit = _stop_limit_raw(pos, stop, config)
    reason = (ExitReason.TRAILING_STOP_HIT if pos.t1_filled
              else ExitReason.STRUCTURAL_STOP_HIT)
    if o >= limit - _TOL:
        _exit_all(pos, o, reason, day)
        return True
    if h >= limit - _TOL:
        # The triggered sell limit sits at `limit`; fills when price recovers.
        _exit_all(pos, limit, reason, day)
        return True
    pos.events.append(TradeEvent(
        EventType.GTT_STOP_UNFILLED, day, pos.symbol, 0, price=o,
        detail=(f"open {o:.2f} below stop-limit {limit:.2f} "
                "(trigger fired, limit unexecuted) — position carried")))
    return False


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
    _track_excursions(pos, h, l, pos.day_index)

    stop = pos.pending_stop_raw
    stop_touch = l <= stop + _TOL
    t1_touch = h >= pos.tranche1_target_raw - _TOL

    # (c) Immediate stop-out: opened at/below the stop -> the stop GTT placed
    # after the fill triggers at once. GTT-buffer semantics apply (rule 2):
    # fill at the open, at the limit leg on recovery, or survive the session.
    if o <= stop + _TOL:
        if _gap_through_stop(pos, o, h, stop, config, day):
            return pos
        # Unfilled: skip intrabar logic (high < limit < targets); arm the stop
        # for tomorrow via the EOD refresh below.
        refresh_stops_eod(pos, bar, config)
        return pos

    # (d) Intrabar pessimism on entry day: stop and T1 target both inside the
    # bar -> the stop wins for the whole position.
    if stop_touch and t1_touch:
        _exit_all(pos, stop, ExitReason.STRUCTURAL_STOP_HIT, day, ambiguous=True)
        return pos
    if stop_touch:
        _exit_all(pos, stop, ExitReason.STRUCTURAL_STOP_HIT, day)
        return pos
    if t1_touch and config.risk.t1_enabled:
        _fill_t1(pos, day)
    # X1: with the tranche disabled, a T2 touch IS the exit — one full-size
    # position, target fills the whole book (t1_filled can never gate it).
    # Unambiguous: every stop-touch branch above already returned.
    if not config.risk.t1_enabled and h >= pos.tranche2_target_raw - _TOL:
        _exit_tranche2(pos, pos.tranche2_target_raw, ExitReason.TARGET_2_HIT, day)
        return pos

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

    _track_excursions(pos, h, l, pos.day_index)

    # Locked circuit: no counterparty, nothing can execute; position frozen.
    if h <= l + _TOL and abs(o - h) <= _TOL:
        pos.events.append(TradeEvent(
            EventType.CIRCUIT_FROZEN, day, pos.symbol, 0, price=c,
            detail="band-locked bar (high == low): no fills"))
        return pos

    stop = pos.pending_stop_raw

    # Gap-through-stop: GTT-buffer semantics (rule 2) — fill at open, at the
    # limit leg on recovery, or the position survives an unfilled session.
    if stop is not None and o < stop - _TOL:
        if _gap_through_stop(pos, o, h, stop, config, day):
            return pos
        # Unfilled: no intrabar evaluation (high < limit leg < all targets);
        # the operator's stall/time rules below exit at close if triggered.
    else:
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
        if t1_touch and config.risk.t1_enabled:
            _fill_t1(pos, day)
        # Legacy: T2 exits only after a T1 fill (breakeven-protected runner).
        # X1 (t1_enabled=False): T2 touch exits the whole position directly.
        if t2_touch and (pos.t1_filled or not config.risk.t1_enabled):
            _exit_tranche2(pos, pos.tranche2_target_raw, ExitReason.TARGET_2_HIT, day)
            return pos

    # --- EOD checks, only if still holding (X1: time_stop_enabled=False
    # removes the stall + time exits; max_holding_days then never fires) ---
    if config.risk.time_stop_enabled:
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
    # The exit price is an excursion print too (a kill-switch/END_OF_RUN exit
    # can sit above the running MFE or below it).
    _track_excursions(pos, price, price, pos.day_index)
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
    if not pos.t1_filled and pos.tranche1_qty > 0:
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

    entry_price_raw is None until the fill (gap-rejected entries stay None
    forever), so it is scaled only when set — the signal levels below are
    always scaled because the gap check needs them in post-ex space.
    """
    if pos.entry_price_raw is not None:
        pos.entry_price_raw = round(pos.entry_price_raw * factor, 2)
    pos.entry_ref_raw = round(pos.entry_ref_raw * factor, 2)
    pos.max_entry_raw = round(pos.max_entry_raw * factor, 2)
    pos.structural_stop_raw = round(pos.structural_stop_raw * factor, 2)
    pos.tranche1_target_raw = round(pos.tranche1_target_raw * factor, 2)
    pos.tranche2_target_raw = round(pos.tranche2_target_raw * factor, 2)
    if pos.pending_stop_raw is not None:
        pos.pending_stop_raw = round(pos.pending_stop_raw * factor, 2)
    if factor > 0:
        # X1: a disabled tranche (qty 0) stays 0 — max(1, ...) would conjure
        # a phantom share that never sells.
        pos.tranche1_qty = (max(1, round(pos.tranche1_qty / factor))
                            if pos.tranche1_qty else 0)
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

def _track_excursions(pos: SimPosition, high: float, low: float,
                      day_index: int) -> None:
    """Fold one session's high/low into the running MFE/MAE (CR-2026-003).

    Measurement only: no rule may read these fields. MAE is kept as the
    adverse move's magnitude ((ref - low) / ref, floored at 0) so it reads
    directly against the 2.2% stop wall.
    """
    ref = pos.entry_price_raw
    if not ref:
        return
    mfe = (high - ref) / ref
    if mfe > pos.mfe_pct:
        pos.mfe_pct = mfe
        pos.mfe_day = day_index
    mae = (ref - low) / ref
    if mae > pos.mae_pct:
        pos.mae_pct = mae
        pos.mae_day = day_index


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
