"""Production portfolio ledger (Phase 7.1) — event-sourced, one brain.

Deliberate deviation from plan §7.1 (see Phase 7 Implementation Notes in the
action plan): there is no second state machine. The trade lifecycle rules are
`backtest/fill_model.py` — golden-tested, corporate-action aware — and the
ledger stores only facts:

  trades   one row per accepted signal: identity, levels, quantities, slot
  events   append-only TradeEvent rows, the single source of truth
  notes    operator state the engine cannot know (HWM, cooldown, deletions)

Position state is a fold of events over the fill model (`SimPosition`), cash
is settled by the engine's own `_settle_position_money` (same DP-per-day,
friction and STCG paths as the backtest), so a replayed backtest of the same
bars and operator fills produces the same cash — live and backtest cannot
drift by construction. The plan's `slots`/`cash_ledger` tables are views over
open trades and settled events, not more tables.

The operator is a receipt book, not an oracle: `--record-fill` records the
broker fill that happened, `--record-exit` records a GTT trigger. The model
then audits the operator (see `reconcile`), never the other way round.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import replace
from datetime import date as Date
from pathlib import Path

from nse_cash.backtest.engine import _settle_position_money
from nse_cash.backtest.fill_model import (EventType, SimPosition,
                                          simulate_entry_day, simulate_open_day)
from nse_cash.backtest.tax_friction import STCGAccount, liquid_fund_interest
from nse_cash.core.types import ExitReason

log = logging.getLogger("nse_cash.ledger")

LIQUID_FUND_RATE = 0.065  # mirrors backtest.engine.LIQUID_FUND_RATE

_TRADE_COLS = ("trade_id, symbol, setup_id, signal_date, slot, sector,"
               " entry_ref, max_entry, structural_stop, tranche1_target,"
               " tranche2_target, tranche1_qty, tranche2_qty")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    trade_id        TEXT PRIMARY KEY,
    symbol          TEXT NOT NULL,
    setup_id        TEXT NOT NULL,
    signal_date     TEXT NOT NULL,
    slot            INTEGER NOT NULL,
    sector          TEXT,
    entry_ref       REAL NOT NULL,
    max_entry       REAL NOT NULL,
    structural_stop REAL NOT NULL,
    tranche1_target REAL NOT NULL,
    tranche2_target REAL NOT NULL,
    tranche1_qty    INTEGER NOT NULL,
    tranche2_qty    INTEGER NOT NULL,
    deleted         INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id   TEXT NOT NULL REFERENCES trades(trade_id),
    event_type TEXT NOT NULL,
    date       TEXT NOT NULL,
    tranche    INTEGER NOT NULL,
    price      REAL,
    qty        INTEGER NOT NULL DEFAULT 0,
    reason     TEXT NOT NULL DEFAULT '',
    ambiguous  INTEGER NOT NULL DEFAULT 0,
    detail     TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS notes (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_trade ON events(trade_id);
"""

# Exit event types that close tranche 2 (the trade). Mirrors fill_model.
_EXIT_TYPES = ("STOP_HIT", "T2_TARGET", "STALL_EXITED", "TIME_EXITED",
               "KILL_SWITCH", "END_OF_RUN")

# Events an operator may record by hand (receipt book). Everything else is
# written by the system itself.
OPERATOR_EXIT_TYPES = ("T1_TARGET", "T2_TARGET", "STOP_HIT", "STALL_EXITED",
                       "TIME_EXITED", "KILL_SWITCH")


def _iso(d: Date | str) -> str:
    return d.isoformat() if isinstance(d, Date) else str(d)


def _date(s: str) -> Date:
    return Date.fromisoformat(s)


def round_to_tick(price: float, tick: float = 0.05) -> float:
    """Round a rupee price to the NSE tick (₹0.05). Kite rejects other levels."""
    return round(round(price / tick) * tick, 2)


class Ledger:
    """SQLite-backed production book. Facts in, fold out."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.db_path)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode = WAL")
        self.con.executescript(_SCHEMA)
        self.con.commit()

    def close(self) -> None:
        self.con.close()

    # ------------------------------------------------------------------
    # Trades
    # ------------------------------------------------------------------

    def add_trade(self, *, trade_id: str, symbol: str, setup_id: str,
                  signal_date: Date, slot: int, sector: str | None,
                  entry_ref: float, structural_stop: float,
                  tranche1_target: float, tranche2_target: float,
                  max_gap_pct: float, tranche1_qty: int,
                  tranche2_qty: int) -> None:
        self.con.execute(
            "INSERT OR REPLACE INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
            (trade_id, symbol, setup_id, _iso(signal_date), slot, sector,
             entry_ref, round_to_tick(entry_ref * (1.0 + max_gap_pct)),
             round_to_tick(structural_stop), round_to_tick(tranche1_target),
             round_to_tick(tranche2_target), tranche1_qty, tranche2_qty))
        self.con.commit()

    def _trade_from_row(self, row) -> dict | None:
        if row is None or row["deleted"]:
            return None
        return dict(row)

    def get_trade(self, trade_id: str) -> dict | None:
        row = self.con.execute(
            f"SELECT {_TRADE_COLS}, deleted FROM trades WHERE trade_id = ?",
            (trade_id,)).fetchone()
        return self._trade_from_row(row)

    def open_trades(self) -> list[dict]:
        """Trades filled and not yet closed: ENTRY_FILLED present, no T2 exit."""
        rows = self.con.execute(
            f"SELECT {_TRADE_COLS}, deleted FROM trades t"
            " WHERE deleted = 0 AND EXISTS (SELECT 1 FROM events e"
            "          WHERE e.trade_id = t.trade_id"
            "            AND e.event_type = 'ENTRY_FILLED')"
            "   AND NOT EXISTS (SELECT 1 FROM events e"
            "          WHERE e.trade_id = t.trade_id"
            "            AND e.event_type IN ('STOP_HIT','T2_TARGET',"
            "                'STALL_EXITED','TIME_EXITED','KILL_SWITCH',"
            "                'END_OF_RUN') AND e.tranche = 2)"
            " ORDER BY t.signal_date, t.trade_id").fetchall()
        return [dict(r) for r in rows]

    def filled_trades(self) -> list[dict]:
        """Every trade that ever filled (open + closed), signal order."""
        rows = self.con.execute(
            f"SELECT {_TRADE_COLS}, deleted FROM trades t"
            " WHERE deleted = 0 AND EXISTS (SELECT 1 FROM events e"
            "          WHERE e.trade_id = t.trade_id"
            "            AND e.event_type = 'ENTRY_FILLED')"
            " ORDER BY t.signal_date, t.trade_id").fetchall()
        return [dict(r) for r in rows]

    def recorded_trade_ids(self) -> set[str]:
        rows = self.con.execute(
            "SELECT DISTINCT trade_id FROM events WHERE event_type ="
            " 'ENTRY_FILLED'").fetchall()
        return {r["trade_id"] for r in rows}

    def pending_signals(self) -> list[dict]:
        """Trades recorded at scan time still awaiting their 10:00 AM fill.

        A pending signal holds its slot and sector until it either fills
        (record-fill) or is tombstoned (record --record-gap-rejected / delete).
        """
        rows = self.con.execute(
            f"SELECT {_TRADE_COLS}, deleted FROM trades t"
            " WHERE deleted = 0 AND NOT EXISTS (SELECT 1 FROM events e"
            "          WHERE e.trade_id = t.trade_id"
            "            AND e.event_type = 'ENTRY_FILLED')"
            " ORDER BY t.signal_date, t.trade_id").fetchall()
        return [dict(r) for r in rows]

    def delete_trade(self, trade_id: str, reason: str) -> None:
        """Operator undo: tombstone a wrong trade row. Facts stay (auditable)."""
        self.con.execute("UPDATE trades SET deleted = 1 WHERE trade_id = ?",
                         (trade_id,))
        self.con.execute("INSERT OR REPLACE INTO notes VALUES ('deleted', ?)",
                         (f"{Date.today().isoformat()} {trade_id}: {reason}",))
        self.con.commit()

    def _note(self, key: str, value: str) -> None:
        self.con.execute("INSERT OR REPLACE INTO notes VALUES (?,?)",
                         (key, value))
        self.con.commit()

    def get_note(self, key: str) -> str | None:
        row = self.con.execute("SELECT value FROM notes WHERE key = ?",
                               (key,)).fetchone()
        return row[0] if row else None

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def append_event(self, trade_id: str, event_type: EventType | str,
                     date: Date, tranche: int = 0, price: float | None = None,
                     qty: int = 0, reason: str = "",
                     ambiguous: bool = False, detail: str = "") -> int:
        et = event_type.value if isinstance(event_type, EventType) else event_type
        cur = self.con.execute(
            "INSERT INTO events (trade_id, event_type, date, tranche, price,"
            " qty, reason, ambiguous, detail) VALUES (?,?,?,?,?,?,?,?,?)",
            (trade_id, et, _iso(date), tranche, price, qty, reason,
             int(ambiguous), detail))
        self.con.commit()
        return cur.lastrowid

    def record_fill(self, trade_id: str, price: float, date: Date,
                    qty: int | None = None) -> None:
        """Record the 10:00 AM fill (the receipt). Idempotent per trade."""
        t = self.get_trade(trade_id)
        if t is None:
            raise KeyError(f"unknown trade {trade_id}")
        if trade_id in self.recorded_trade_ids():
            raise ValueError(f"{trade_id}: fill already recorded")
        if qty is None:
            qty = t["tranche1_qty"] + t["tranche2_qty"]
        self.append_event(trade_id, EventType.ENTRY_FILLED, date, tranche=0,
                          price=price, qty=qty, detail="operator-recorded fill")

    def record_exit(self, trade_id: str, price: float, date: Date,
                    reason: str) -> int:
        """Record a tranche exit. reason: an EventType from OPERATOR_EXIT_TYPES
        (T1_TARGET = tranche-1 target fill; T2_TARGET/STOP_HIT/... = close)."""
        t = self.get_trade(trade_id)
        if t is None:
            raise KeyError(f"unknown trade {trade_id}")
        if trade_id not in self.recorded_trade_ids():
            raise ValueError(f"{trade_id}: record the entry fill first")
        if reason not in OPERATOR_EXIT_TYPES:
            raise ValueError(f"reason must be one of {sorted(OPERATOR_EXIT_TYPES)}")
        pos = self.position(t)
        if pos.exit_date is not None:
            raise ValueError(f"{trade_id}: trade already closed {pos.exit_date}")
        if reason == "T1_TARGET":
            if pos.t1_filled:
                raise ValueError(f"{trade_id}: T1 already recorded")
            return self.append_event(trade_id, EventType.T1_TARGET, date,
                                     tranche=1, price=price,
                                     qty=t["tranche1_qty"],
                                     reason=ExitReason.TARGET_1_HIT.value)
        # Full-position exit (T2 side); if T1 was still on, the fold splits it.
        return self.append_event(trade_id, EventType(reason), date, tranche=2,
                                 price=price,
                                 qty=t["tranche2_qty"] if pos.t1_filled
                                 else t["tranche1_qty"] + t["tranche2_qty"],
                                 reason=_exit_reason(reason, pos).value)

    # ------------------------------------------------------------------
    # The fold: events -> SimPosition, with transition guards
    # ------------------------------------------------------------------

    def _make_position(self, t: dict) -> SimPosition:
        return SimPosition(
            trade_id=t["trade_id"], symbol=t["symbol"],
            entry_ref_raw=t["entry_ref"], max_entry_raw=t["max_entry"],
            structural_stop_raw=t["structural_stop"],
            tranche1_target_raw=t["tranche1_target"],
            tranche2_target_raw=t["tranche2_target"],
            tranche1_qty=t["tranche1_qty"], tranche2_qty=t["tranche2_qty"],
            setup=t["setup_id"], slot=t["slot"], sector=t["sector"])

    def _apply(self, pos: SimPosition, row) -> None:
        et = EventType(row["event_type"])
        day = _date(row["date"])
        price = row["price"]
        qty = int(row["qty"] or 0)
        ev = _mk_event(et, day, row)
        if et is EventType.ENTRY_FILLED:
            if pos.entry_price_raw is not None:
                raise ValueError(f"{pos.trade_id}: double ENTRY_FILLED "
                                 f"at seq {row['seq']}")
            pos.entry_price_raw = float(price)
            pos.entry_date = day
            pos.day_index = 1
            pos.pending_stop_raw = pos.structural_stop_raw
            pos.events.append(ev)
        elif et is EventType.ENTRY_REJECTED_GAP:
            pos.entry_price_raw = None          # never opened
        elif et is EventType.T1_TARGET:
            if pos.entry_price_raw is None:
                raise ValueError(f"{pos.trade_id}: T1 fill before entry "
                                 f"at seq {row['seq']}")
            if pos.t1_filled:
                raise ValueError(f"{pos.trade_id}: double T1 fill "
                                 f"at seq {row['seq']}")
            pos.t1_filled = True
            pos.events.append(ev)
        elif et is EventType.BREAKEVEN_ARMED:
            if pos.entry_price_raw is None:
                raise ValueError(f"{pos.trade_id}: breakeven before entry "
                                 f"at seq {row['seq']}")
            pos.pending_stop_raw = pos.breakeven_raw
            pos.events.append(ev)
        elif et.value in _EXIT_TYPES:
            if pos.entry_price_raw is None:
                raise ValueError(f"{pos.trade_id}: exit before entry "
                                 f"at seq {row['seq']}")
            if not pos.t1_filled:               # whole position exits at once:
                # split the FULL recorded qty exactly like fill_model._exit_all
                # (one tranche-1 sell of T1 qty + one tranche-2 sell of T2 qty;
                # duplicating the row's full qty would settle double the shares)
                first = _mk_event(et, day, row, tranche=1)
                pos.events.append(replace(first, qty=pos.tranche1_qty))
                ev = replace(ev, qty=pos.tranche2_qty)
            pos.t1_filled = False
            pos.t2_open = False
            pos.pending_stop_raw = None
            pos.exit_date = day
            reason = row["reason"] or ExitReason.STRUCTURAL_STOP_HIT.value
            pos.exit_reason = ExitReason(reason).value
            pos.events.append(ev)
        else:
            # CORPORATE_ACTION_ADJUSTED / CIRCUIT_FROZEN: informational; the
            # operator re-derives levels at check-eod time.
            pos.events.append(ev)

    def position(self, t: dict) -> SimPosition:
        """Fold a trade's full event history into a live SimPosition."""
        pos = self._make_position(t)
        for row in self.con.execute(
                "SELECT seq, event_type, date, tranche, price, qty, reason,"
                " ambiguous, detail FROM events WHERE trade_id = ? ORDER BY seq",
                (t["trade_id"],)).fetchall():
            self._apply(pos, row)
        return pos

    def open_positions(self) -> list[tuple[dict, SimPosition]]:
        return [(t, self.position(t)) for t in self.open_trades()]

    # ------------------------------------------------------------------
    # Money: the engine's own settlement path, no second implementation
    # ------------------------------------------------------------------

    class _CashShim:
        def __init__(self) -> None:
            self.cash = 0.0

    def cash_snapshot(self, config) -> dict:
        """Cash = base capital - settled buys + settled sells (+ interest).

        Settlement is `_settle_position_money` — the exact function the
        backtest engine uses — so DP-per-day, GST and slippage cannot diverge
        between live and simulated books. Interest accrues on the settled
        cash balance overnight (stateful note `last_interest_date`).
        """
        base = config.capital.base_capital
        invested = proceeds = 0.0
        stcg = STCGAccount(config.friction)
        for t in self.filled_trades():
            pos = self.position(t)
            if pos.entry_price_raw is None:
                continue
            shim = self._CashShim()
            _settle_position_money(shim, pos, config)
            invested += pos.entry_cost
            proceeds += pos.exit_proceeds
            # STCG feeds on FULLY closed trades only: settlement stamps
            # exit_date on a T1-partial too (engine-benign there because
            # _finalize_if_closed also checks is_open).
            if pos.exit_date is not None and not pos.is_open:
                stcg.add(pos.exit_proceeds - pos.entry_cost, pos.exit_date)
        cash = base - invested + proceeds
        stcg.finalize()   # close the open FY so fy_summary/tax_paid are live
        last = self.get_note("last_interest_date")
        today = Date.today()
        if last:
            cash += liquid_fund_interest(cash, (today - _date(last)).days,
                                         LIQUID_FUND_RATE)
        self._note("last_interest_date", today.isoformat())
        return {"base_capital": base, "invested": invested,
                "proceeds": proceeds, "cash": cash,
                "realized_pnl": stcg.total_realized, "tax_paid": stcg.tax_paid}

    # ------------------------------------------------------------------
    # Portfolio state: slots, sectors, equity, HWM, cooldown
    # ------------------------------------------------------------------

    def occupied_slots(self) -> int:
        return len(self.open_trades())

    def used_slots(self) -> set[int]:
        return {t["slot"] for t in self.open_trades()}

    def free_slots(self, config) -> set[int]:
        return ({i for i in range(1, config.capital.num_slots + 1)}
                - self.used_slots())

    def next_slot(self, config) -> int | None:
        free = self.free_slots(config)
        return min(free) if free else None

    def active_sectors(self) -> set[str]:
        # "Unknown" is the sector gate's sentinel, never a claim.
        return {t["sector"] for t in self.open_trades()
                if t["sector"] and t["sector"] != "Unknown"}

    def equity(self, config, marks: dict[str, float]) -> float:
        """Equity = settled cash + marked open quantity."""
        snap = self.cash_snapshot(config)
        mv = 0.0
        for t, pos in self.open_positions():
            if pos.entry_price_raw is None:
                continue
            px = marks.get(t["symbol"], pos.entry_price_raw)
            mv += pos.open_qty * px
        return snap["cash"] + mv

    def update_hwm(self, config, marks: dict[str, float]) -> float:
        hwm = max(float(self.get_note("hwm") or config.capital.base_capital),
                  self.equity(config, marks))
        self._note("hwm", repr(hwm))
        return hwm

    def hwm(self, config) -> float:
        return float(self.get_note("hwm") or config.capital.base_capital)

    def kill_blocker(self) -> str | None:
        """Why new entries are forbidden right now, or None."""
        until = self.get_note("cooldown_until")
        if until and _date(until) >= Date.today():
            # ponytail: calendar-date cooldown; the engine's 10 TRADING days
            # map to ~14 calendar days here. Upgrade path: count sessions
            # from daily_bars once a calendar helper is shared.
            return f"kill-switch cooldown active until {until}"
        return None

    def arm_cooldown(self, until: Date) -> None:
        self._note("cooldown_until", until.isoformat())

    # ------------------------------------------------------------------
    # Integrity: the ledger's own health check
    # ------------------------------------------------------------------

    def upcoming_corporate_actions(self, store, horizon_days: int = 3) -> list[str]:
        """GTT warnings: bonus/split ex-dates inside the horizon for open trades.

        A bonus/split during a holding period invalidates every GTT trigger
        and quantity; the operator must modify both GTTs on Kite. Same rule
        the fill model applies mid-trade (`_apply_corporate_action`).
        """
        warnings: list[str] = []
        for t, pos in self.open_positions():
            if pos.entry_price_raw is None:
                continue
            actions = store.con.execute(
                "SELECT ex_date, adjustment_factor FROM corporate_actions"
                " WHERE symbol = ? ORDER BY ex_date", [t["symbol"]]).fetchall()
            today = Date.today()
            for ex_date, af in actions:
                ex = _date(str(ex_date))
                if today <= ex <= today.fromordinal(today.toordinal() + horizon_days) \
                        and af is not None and abs(float(af) - 1.0) > 1e-9:
                    f = float(af)
                    warnings.append(
                        f"{t['trade_id']} ({t['symbol']}): ex-date {ex} — "
                        f"modify GTT triggers: T1 ₹{round_to_tick(pos.tranche1_target_raw * f):,.2f}, "
                        f"T2 ₹{round_to_tick(pos.tranche2_target_raw * f):,.2f}, "
                        f"stop ₹{round_to_tick((pos.pending_stop_raw or pos.structural_stop_raw) * f):,.2f}, "
                        f"qty x{round(1 / f)}")
        return warnings

    def integrity_problems(self, config, sessions_by_trade=None) -> list[str]:
        problems: list[str] = []
        sessions_by_trade = sessions_by_trade or {}
        for t in self.open_trades():
            pos = self.position(t)
            if pos.entry_price_raw is None:
                problems.append(f"{t['trade_id']}: open trade with no fill")
                continue
            n = sessions_by_trade.get(t["trade_id"], pos.day_index)
            if n > config.risk.max_holding_days:
                problems.append(
                    f"{t['trade_id']} ({t['symbol']}): held {n} sessions, "
                    f"past the Day {config.risk.max_holding_days} time exit")
            if (pos.t1_filled and config.risk.runner_trail == "breakeven"
                    and pos.pending_stop_raw != pos.breakeven_raw):
                problems.append(
                    f"{t['trade_id']} ({t['symbol']}): T1 filled but T2 stop "
                    "not at breakeven — modify GTT 2")
        return problems


# ---------------------------------------------------------------------------
# Reconciliation: replay real bars over the recorded events (post-sync EOD)
# ---------------------------------------------------------------------------

def reconcile(store, config, ledger: Ledger) -> list[str]:
    """Replay each open trade's actual bars through the fill model and diff
    against the ledger's fold. Mismatch = forgotten --record-exit, a wrong
    recorded price, or suspect data. Empty list = book matches the market."""
    from nse_cash.backtest.fill_model import action_factor_for_date

    drift: list[str] = []
    for t, folded in ledger.open_positions():
        if folded.entry_price_raw is None:
            continue
        actions = store.con.execute(
            "SELECT ex_date, adjustment_factor FROM corporate_actions"
            " WHERE symbol = ?", [t["symbol"]]).df()
        rows = store.con.execute(
            "SELECT date, open, high, low, close FROM daily_bars"
            " WHERE symbol = ? AND date >= ? ORDER BY date",
            [t["symbol"], folded.entry_date]).fetchall()
        if not rows:
            continue
        if action_factor_for_date(actions, folded.entry_date) is not None or (
                not actions.empty and any(
                    folded.entry_date <= _date(str(a.ex_date)) <= _date(str(rows[-1][0]))
                    for a in actions.itertuples())):
            drift.append(f"{t['trade_id']} ({t['symbol']}): corporate action "
                         "during hold — verify levels manually, replay skipped")
            continue

        # Model replay from the trade's own levels; the operator's recorded
        # fill stays the money truth, the model is the behavior truth.
        model = ledger._make_position(t)
        for i, r in enumerate(rows):
            # rows are (date, open, high, low, close) — plain tuples.
            bar = {"date": _date(str(r[0])), "open": r[1],
                   "high": r[2], "low": r[3], "close": r[4]}
            if i == 0:
                simulate_entry_day(model, bar, config)
            elif model.is_open:
                simulate_open_day(model, bar, config)
        # exit_date/exit_reason are set by settlement in the engine; do the
        # same so the drift message has a real date and reason.
        _settle_position_money(ledger._CashShim(), model, config)
        if abs((model.entry_price_raw or 0.0) - folded.entry_price_raw) > 0.05:
            drift.append(
                f"{t['trade_id']} ({t['symbol']}): recorded fill "
                f"₹{folded.entry_price_raw:.2f} vs model ₹{model.entry_price_raw:.2f}")
        if model.is_open != folded.is_open:
            if model.is_open:
                drift.append(f"{t['trade_id']} ({t['symbol']}): ledger closed "
                             f"the trade but bars say still open — check exits")
            else:
                drift.append(
                    f"{t['trade_id']} ({t['symbol']}): model exited on "
                    f"{model.exit_date} ({model.exit_reason}) but ledger shows "
                    "the trade open — record the exit or check the data")
        elif model.is_open and folded.t1_filled != model.t1_filled:
            drift.append(f"{t['trade_id']} ({t['symbol']}): T1 fill state "
                         f"drifted (model {model.t1_filled}, "
                         f"ledger {folded.t1_filled})")
    return drift


# Operator-facing exit kind -> the fill model's ExitReason.
def _exit_reason(kind: str, pos: SimPosition) -> ExitReason:
    if kind == "T2_TARGET":
        return ExitReason.TARGET_2_HIT
    if kind == "STOP_HIT":
        # After a T1 fill the standing stop is the breakeven trail.
        return (ExitReason.TRAILING_STOP_HIT if pos.t1_filled
                else ExitReason.STRUCTURAL_STOP_HIT)
    if kind == "STALL_EXITED":
        return ExitReason.STALL_48H_HIT
    if kind == "TIME_EXITED":
        return ExitReason.TIME_DAY5_HIT
    return ExitReason.KILL_SWITCH


def _mk_event(et: EventType, day: Date, row, tranche: int | None = None):
    from nse_cash.backtest.fill_model import TradeEvent
    return TradeEvent(
        event_type=et, date=day, symbol="", tranche=row["tranche"]
        if tranche is None else tranche,
        price=row["price"], qty=int(row["qty"] or 0), reason=row["reason"] or "",
        ambiguous_intrabar=bool(row["ambiguous"]), detail=row["detail"] or "")
