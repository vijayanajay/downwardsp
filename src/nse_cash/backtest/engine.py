"""Phase 6 high-fidelity simulation engine: a small calendar loop.

The intelligence lives in the layers below — `funnel.pipeline.decide_entries`
is the single brain for decisions, `backtest.fill_model` is the single brain
for fills. This module owns only the boring parts that must still be honest:

  1. Warm-up once, replay fast: features in year chunks (idempotent), the
     regime table via `evaluate_market_regime_range`, historical circuit-hit
     governance. The day loop recomputes no market-wide state.
  2. Slot lifecycle with one honest ordering rule: an entry decision is made
     at the open of Day T+1 from Day T data. A slot freed on Day D can host a
     new trade whose signal day is D (entering at D+1's open) — same-day
     intraday reuse is prohibited, because the manual system does not re-scan
     intraday either. Entries execute BEFORE the day's exit processing, so a
     slot freed at D's close is first allocatable at D+1's scan -> D+2 entry.
  3. Capital accounting: cash earns liquid-fund interest daily on the
     unallocated balance; buys debit cash including buy friction; sells credit
     net proceeds including sell friction, with the flat DP charge once per
     sell day per trade; realized P&L feeds a per-FY STCG account with loss
     carry-forward.
  4. Kill switch at EOD: detect drawdown at the close, liquidate at NEXT
     open (never at the close you just computed), defer any forced exit that
     lands on a circuit-frozen bar, then a 10-trading-day zero-new-entries
     cooldown.
  5. The event log and the daily equity curve are the only artifacts. Every
     metric downstream is pure aggregation over them.

Raw price space everywhere (friction, GTT levels, ledger live in rupees).
CandidateSignal levels are already raw-space (ranking.py converts once from
adjusted space at signal time).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date as Date
from pathlib import Path

import pandas as pd

from nse_cash.backtest.fill_model import (EventType, SimPosition, TradeEvent,
                                          action_factor_for_date, force_exit,
                                          simulate_entry_day,
                                          simulate_open_day)
from nse_cash.backtest.tax_friction import (STCGAccount, buy_side_friction,
                                            liquid_fund_interest,
                                            sell_side_friction)
from nse_cash.core.governance import circuit_hits_from_bars
from nse_cash.core.types import ExitReason, MarketRegimeState
from nse_cash.data.storage import MarketStore
from nse_cash.funnel.pipeline import decide_entries
from nse_cash.funnel.sector_gate import UNKNOWN_SECTOR

log = logging.getLogger("nse_cash.backtest.engine")

LIQUID_FUND_RATE = 0.065            # plan §6.1: overnight fund yield, p.a.
FEATURE_WARMUP_CALENDAR_DAYS = 450  # signal liveness from day 1 of the run

_MONEY_EVENTS = {EventType.T1_TARGET, EventType.T2_TARGET,
                 EventType.STOP_HIT, EventType.STALL_EXITED, EventType.TIME_EXITED,
                 EventType.KILL_SWITCH, EventType.END_OF_RUN}


# ---------------------------------------------------------------------------
# Bar cache: per-symbol OHLC over the replay range, loaded lazily once
# ---------------------------------------------------------------------------

class BarCache:
    """Lazy per-symbol bar frames over a fixed [start, end] range."""

    def __init__(self, store: MarketStore, start: Date, end: Date) -> None:
        self.store = store
        self.start = start
        self.end = end
        self._frames: dict[str, pd.DataFrame] = {}

    def frame(self, symbol: str) -> pd.DataFrame:
        if symbol not in self._frames:
            df = self.store.con.execute(
                "SELECT date, open, high, low, close FROM daily_bars "
                "WHERE symbol = ? AND date BETWEEN ? AND ? ORDER BY date",
                [symbol, self.start, self.end]).df()
            if not df.empty:
                df["date"] = pd.to_datetime(df["date"]).dt.date
                df = df.set_index("date", drop=False)
            self._frames[symbol] = df
        return self._frames[symbol]

    def bar(self, symbol: str, d: Date) -> pd.Series | None:
        f = self.frame(symbol)
        if f.empty or d not in f.index:
            return None
        return f.loc[d]

    def close(self, symbol: str, d: Date) -> float | None:
        bar = self.bar(symbol, d)
        return float(bar["close"]) if bar is not None else None


# ---------------------------------------------------------------------------
# Portfolio book: 4 slots, one cash ledger, zero hidden state
# ---------------------------------------------------------------------------

@dataclass
class SimBook:
    """Simulated portfolio. Structure only; money is settled into `.cash`."""

    config: object
    cash: float
    peak_equity: float
    free_slots: set[int] = field(default_factory=lambda: {1, 2, 3, 4})
    positions: dict[str, SimPosition] = field(default_factory=dict)
    closed: list[SimPosition] = field(default_factory=list)
    sectors: set[str] = field(default_factory=set)
    cooldown_until: int | None = None       # bar index; blocked while i < it
    pending_liquidation: bool = False
    kill_count: int = 0
    equity_curve: list[dict] = field(default_factory=list)

    def occupied(self) -> int:
        return len(self.positions)

    def take_slot(self) -> int:
        slot = min(self.free_slots)
        self.free_slots.discard(slot)
        return slot

    def return_slot(self, slot: int) -> None:
        self.free_slots.add(slot)


# ---------------------------------------------------------------------------
# Warm-up: compute everything market-wide once, outside the day loop
# ---------------------------------------------------------------------------

@dataclass
class Warmup:
    regime_by_date: dict[Date, object]
    regime_df: pd.DataFrame
    nifty50_coverage: int


def warmup(store: MarketStore, start: Date, end: Date) -> Warmup:
    """Precompute regime table, features (year chunks) and circuit governance.

    Idempotent: features and governance rows key on (symbol, date) and are
    upserted, so re-running a backtest skips covered dates. The PIT universe
    is self-healing: if membership is missing for any run date, it is rebuilt
    for the whole range first (one vectorized query) — the engine never runs
    on an empty universe because somebody forgot a `sync` step.
    """
    _ensure_pit_universe(store, start, end)
    regime_df = _compute_regime_table(store, start, end)
    regime_by_date: dict[Date, object] = {}
    for _, row in regime_df.iterrows():
        regime_by_date[row["date"]] = _regime_row_to_result(row)

    n50 = store.con.execute(
        "SELECT count(*) FROM market_indices WHERE index_name = 'NIFTY 50' "
        "AND date BETWEEN ? AND ?", [start, end]).fetchone()[0]
    if n50 == 0:
        log.warning("warmup: no NIFTY 50 history in [%s, %s]; regime will be "
                    "DEFENSIVE_CASH for the whole replay and zero trades will "
                    "be generated. Ingest index data first.", start, end)

    _chunked_features(store, start, end)
    _backfill_circuit_governance(store, start, end)
    return Warmup(regime_by_date=regime_by_date, regime_df=regime_df,
                  nifty50_coverage=int(n50))


def _compute_regime_table(store: MarketStore, start: Date, end: Date) -> pd.DataFrame:
    from nse_cash.funnel.market_regime import evaluate_market_regime_range
    df = evaluate_market_regime_range(store, start_date=start, end_date=end)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def _regime_row_to_result(row: pd.Series):
    """Rebuild a MarketRegimeResult from an evaluate_market_regime_range row."""
    from nse_cash.funnel.market_regime import MarketRegimeResult
    return MarketRegimeResult(
        date=row["date"],
        state=MarketRegimeState(row["state"]),
        nifty50_close=float(row["nifty50_close"]),
        nifty50_ema20=float(row["nifty50_ema20"]),
        nifty50_above_ema=bool(row["nifty50_above_ema"]),
        breadth_pct=float(row["breadth_pct"]),
        breadth_above_50=bool(row["breadth_above_50"]),
        advancing_stocks=int(row["advancing_stocks"]),
        total_eligible_stocks=int(row["total_eligible_stocks"]),
        reason=f"precomputed for backtest replay ({row['state']})",
    )


def _ensure_pit_universe(store: MarketStore, start: Date, end: Date) -> None:
    """Rebuild PIT membership for any run date that lacks it.

    The universe table is data prep (derived 100% from daily_bars), so the
    engine heals it instead of failing; a warm-up window is included so the
    ADTV_90 gate is meaningful from the first session.
    """
    from nse_cash.core.universe import build_pit_universe_range
    covered: set[Date] = {pd.Timestamp(r[0]).date() for r in store.con.execute(
        "SELECT DISTINCT date FROM pit_universe").fetchall()}
    dates = store.trading_dates(start=start, end=end)
    missing = [d for d in dates if d not in covered]
    if not missing:
        return
    log.info("warmup: PIT universe missing for %d date(s); rebuilding [%s, %s]",
             len(missing), missing[0], missing[-1])
    warm_start = (pd.Timestamp(start)
                  - pd.Timedelta(days=FEATURE_WARMUP_CALENDAR_DAYS)).date()
    # The features engine queries PIT membership inside its own lookback
    # window too, so the universe must cover the warm-up window, not just
    # the run dates — otherwise every features call warns 'empty PIT universe'.
    build_pit_universe_range(store, warm_start, end)


def _chunked_features(store: MarketStore, start: Date, end: Date) -> None:
    """Compute features once per calendar-year chunk; skip covered chunks.

    `compute_features(on_date)` derives every feature for that date from its
    bounded lookback window, so computing on each year's first missing date
    covers that year. Warm-up starts FEATURE_WARMUP_CALENDAR_DAYS before the
    run so SMA200/PV-percentile features are live from the first session.
    """
    from nse_cash.setups.features import refresh_features
    covered: set[Date] = {pd.Timestamp(r[0]).date() for r in store.con.execute(
        "SELECT DISTINCT date FROM features").fetchall()}
    warm_start = (pd.Timestamp(start)
                  - pd.Timedelta(days=FEATURE_WARMUP_CALENDAR_DAYS)).date()
    dates = store.trading_dates(start=warm_start, end=end)
    missing = [d for d in dates if d not in covered]
    if not missing:
        log.info("warmup: features already cover [%s, %s]", warm_start, end)
        return
    # Chunk ends: first missing date of each calendar year, plus the last one.
    ends: list[Date] = []
    seen_years: set[int] = set()
    for d in missing:
        if d.year not in seen_years:
            ends.append(d)
            seen_years.add(d.year)
    if missing[-1] not in ends:
        ends.append(missing[-1])
    log.info("warmup: computing features for %d chunk(s) over %d missing date(s) "
             "in [%s, %s]", len(ends), len(missing), warm_start, end)
    for d in ends:
        refresh_features(store, d)


def _backfill_circuit_governance(store: MarketStore, start: Date, end: Date) -> int:
    """Persist circuit-hit exclusions for the historical range (deterministic).

    Live ASM/GSM/board-meeting lists do not exist for 2010-2022; the honest
    tear sheet states what the governance filter actually saw. Circuit hits
    are derivable from bhavcopy bars for the whole range, so those exclusions
    apply historically; the surveillance/calendar layers degrade to no-ops.
    """
    hits = circuit_hits_from_bars(store.con, start, end)
    if hits.empty:
        return 0
    hits = hits.copy()
    hits["date"] = pd.to_datetime(hits["date"]).dt.date
    hits["list_type"] = "CIRCUIT"
    hits["detail"] = "backfill: circuit lock derived from bars"
    return store.upsert_governance(hits)


# ---------------------------------------------------------------------------
# Money settlement
# ---------------------------------------------------------------------------

def _settle_position_money(book: SimBook, pos: SimPosition, config) -> None:
    """Fold fill-model events not yet settled into cash and position cost.

    Sell events: the flat DP charge applies once per sell DAY per trade (T1
    and T2 sold the same day pay it once; sold on two days, twice).
    """
    new_events = pos.events[pos.settled:]
    for ev in new_events:
        if ev.event_type is EventType.ENTRY_FILLED:
            value = ev.qty * float(ev.price)
            buy = buy_side_friction(value, config.friction)
            book.cash -= value + buy.total
            pos.entry_cost += value + buy.total
        elif ev.event_type in _MONEY_EVENTS:  # a sell
            value = ev.qty * float(ev.price)
            if ev.date in pos.sell_days_charged:
                dp = 0.0
            else:
                dp = config.friction.dp_charge_per_sell
                pos.sell_days_charged.add(ev.date)
            sell = sell_side_friction(value, config.friction, dp_charge=dp)
            book.cash += value - sell.total
            pos.exit_proceeds += value - sell.total
            pos.exit_date = ev.date
            pos.exit_reason = ev.reason
    pos.settled = len(pos.events)


def _realized_pnl(pos: SimPosition) -> float:
    return pos.exit_proceeds - pos.entry_cost


def _finalize_trade(book: SimBook, pos: SimPosition, stcg: STCGAccount | None) -> None:
    """Move a closed trade out of the book: free slot + sector, feed STCG."""
    book.positions.pop(pos.symbol, None)
    book.return_slot(pos.slot)
    if pos.sector and pos.sector != UNKNOWN_SECTOR:
        book.sectors.discard(pos.sector)
    book.closed.append(pos)
    if stcg is not None and pos.exit_date is not None:
        stcg.add(_realized_pnl(pos), pos.exit_date)


def _finalize_if_closed(book: SimBook, pos: SimPosition, stcg) -> None:
    if pos.entry_price_raw is not None and not pos.is_open \
            and pos.symbol in book.positions:
        _finalize_trade(book, pos, stcg)


# ---------------------------------------------------------------------------
# Entry execution (Day T+1 open)
# ---------------------------------------------------------------------------

def _buy_friction_rate(config) -> float:
    f = config.friction
    return (f.stt_delivery + f.stamp_duty + f.nse_turnover + f.sebi_fee
            + f.slippage_per_side
            + (f.nse_turnover + f.sebi_fee) * f.gst_rate)


def slot_quantity(config, entry_ref: float,
                  slot_capital: float | None = None) -> int:
    """Shares one slot buys at `entry_ref`: slot capital net of exact buy
    friction. THE sizing rule — the action sheet, the recorded signal and the
    engine all call this, so the live book and the backtest can never size
    the same trade differently."""
    capital = config.capital.slot_capital if slot_capital is None else slot_capital
    return int(capital // (entry_ref * (1.0 + _buy_friction_rate(config))))


def _execute_pending_entry(book: SimBook, config, cache: BarCache,
                           symbol: str, cand, entry_day: Date,
                           sector: str | None,
                           actions: pd.DataFrame | None,
                           stcg: STCGAccount) -> None:
    """Turn an accepted Day-T candidate into a position at T+1's open.

    The fill model owns the fill decision (gap ceiling, gap-down acceptance).
    Rejections undo the slot allocation; same-day stop-outs finalize at once.
    """
    if book.occupied() >= config.capital.num_slots:
        return
    if sector and sector != UNKNOWN_SECTOR and sector in book.sectors:
        return
    if symbol in book.positions:
        # ponytail guard: ranking dedupes per (symbol, date) and entry is
        # T+1, so this should be unreachable — but a duplicate pending entry
        # would overwrite the dict slot and leak the first trade's slot.
        log.warning("entry for %s skipped: position already open", symbol)
        return
    bar = cache.bar(symbol, entry_day)
    if bar is None:
        log.debug("entry skipped for %s on %s: no bar", symbol, entry_day)
        return

    entry_ref = float(cand.entry_ref)                    # raw Close_T
    stop_raw = float(cand.structural_stop)               # raw
    qty_total = slot_quantity(config, entry_ref)
    if qty_total <= 0:
        return

    pos = SimPosition(
        trade_id=f"{symbol}-{cand.date.isoformat()}",
        symbol=symbol,
        setup=cand.setup.value if hasattr(cand, "setup") else str(cand.setup),
        entry_ref_raw=entry_ref,
        max_entry_raw=entry_ref * (1.0 + config.risk.max_gap_entry),
        structural_stop_raw=stop_raw,
        tranche1_target_raw=entry_ref * (1.0 + cand.tranche1_target_pct),
        tranche2_target_raw=entry_ref * (1.0 + cand.tranche2_target_pct),
        tranche1_qty=qty_total // 2,
        tranche2_qty=qty_total - qty_total // 2,
        sector=sector,
        # CR-2026-001: the stop's LIMIT leg derives from config's
        # gtt_stop_limit_buffer inside the fill model (relative to whichever
        # stop is standing — structural or breakeven), so live and backtest
        # gap semantics cannot diverge.
    )
    pos.slot = book.take_slot()
    book.positions[symbol] = pos
    # Only KNOWN sectors are claimed: "Unknown" is a sentinel, not a sector,
    # and must never block a second unknown-sector candidate.
    if sector and sector != UNKNOWN_SECTOR:
        book.sectors.add(sector)

    # A bonus/split with ex-date ON the entry day adjusts the signal levels
    # before the gap check and fill (raw open is post-action space).
    factor = action_factor_for_date(actions, entry_day) \
        if actions is not None else None
    simulate_entry_day(pos, bar, config, action_factor=factor)
    _settle_position_money(book, pos, config)

    if pos.entry_price_raw is None:
        # Gap ceiling rejected the entry: no trade ever opened; the slot was
        # held overnight by a signal that died at 10:00 AM. Free it now —
        # today's scan (this evening's data) may reuse it for tomorrow.
        log.debug("entry rejected (gap) for %s on %s", symbol, entry_day)
        book.positions.pop(symbol, None)
        book.return_slot(pos.slot)
        if sector:
            book.sectors.discard(sector)
        book.closed.append(pos)
    elif not pos.is_open:
        # Filled and stopped out the same day (open at/below the stop).
        _finalize_trade(book, pos, stcg)


# ---------------------------------------------------------------------------
# Kill switch
# ---------------------------------------------------------------------------

def _consume_pending_liquidation(book: SimBook, config, cache: BarCache,
                                 d: Date, stcg: STCGAccount) -> None:
    """Liquidate open positions at today's open (kill-switch next-open rule).

    Positions whose bar is circuit-frozen are deferred to the next session —
    no counterparty, no trade, no fictional exit price.
    """
    for pos in list(book.positions.values()):
        if pos.entry_price_raw is None:
            continue
        bar = cache.bar(pos.symbol, d)
        if bar is None:
            continue
        o, high, low = float(bar["open"]), float(bar["high"]), float(bar["low"])
        if high <= low + 1e-9 and abs(o - high) <= 1e-9:
            log.info("kill-switch liquidation deferred for %s on %s "
                     "(circuit-frozen)", pos.symbol, d)
            continue
        force_exit(pos, o, d)
        _settle_position_money(book, pos, config)
        _finalize_trade(book, pos, stcg)
    book.pending_liquidation = any(
        p.entry_price_raw is not None and p.is_open
        for p in book.positions.values())


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------

@dataclass
class BacktestResult:
    start: Date
    end: Date
    events: list[TradeEvent]
    equity: pd.DataFrame
    trades: pd.DataFrame
    stcg: STCGAccount
    regime_df: pd.DataFrame
    interest_paid: float
    kill_count: int


def run_backtest(store: MarketStore, config, start: Date, end: Date,
                 carry_forward_stcg: bool | None = None) -> BacktestResult:
    """Replay the system day by day over [start, end].

    The loop is deliberately boring: look up the precomputed regime, call the
    shared pipeline for decisions, feed bars to the shared fill model for
    fills, book money, and record one equity row. Nothing here would behave
    differently if the calendar were live.
    """
    start = pd.Timestamp(start).date()
    end = pd.Timestamp(end).date()
    if start > end:
        start, end = end, start
    warm = warmup(store, start, end)
    dates = store.trading_dates(start=start, end=end)
    if not dates:
        raise RuntimeError(f"no trading dates in [{start}, {end}]; "
                           "run `nse-cash sync` first")

    cfg = config
    book = SimBook(config=cfg, cash=cfg.capital.base_capital,
                   peak_equity=cfg.capital.base_capital)
    stcg = STCGAccount(cfg.friction,
                       carry_forward=cfg.friction.stcg_carry_forward
                       if carry_forward_stcg is None else carry_forward_stcg)
    cache = BarCache(store, start, end)
    actions_by_symbol: dict[str, pd.DataFrame | None] = {}

    def _actions(symbol: str) -> pd.DataFrame | None:
        if symbol not in actions_by_symbol:
            df = store.con.execute(
                "SELECT ex_date, adjustment_factor FROM corporate_actions "
                "WHERE symbol = ?", [symbol]).df()
            actions_by_symbol[symbol] = None if df.empty else df
        return actions_by_symbol[symbol]

    interest_total = 0.0
    pending_entries: list[tuple[object, str | None]] = []   # (cand, sector)

    for i, d in enumerate(dates):
        # ---- 1. Morning: interest accrual on unallocated cash ---------------
        elapsed_days = (d - dates[i - 1]).days if i > 0 else 1
        interest = liquid_fund_interest(book.cash, elapsed_days, LIQUID_FUND_RATE)
        book.cash += interest
        interest_total += interest

        in_cooldown = book.cooldown_until is not None and i < book.cooldown_until
        if book.cooldown_until is not None and not in_cooldown:
            book.cooldown_until = None

        # ---- 2. Kill-switch liquidation queued yesterday: sell at OPEN ------
        if book.pending_liquidation:
            _consume_pending_liquidation(book, cfg, cache, d, stcg)

        # ---- 3. Entries from Day-T signals at today's open ------------------
        # Skipped entirely while a kill switch is armed or cooling down: the
        # risk manager halts new entries, including ones queued the night
        # before the drawdown was confirmed at the close.
        if not book.pending_liquidation and not in_cooldown:
            for cand, sector in pending_entries:
                if book.occupied() >= cfg.capital.num_slots:
                    break
                _execute_pending_entry(book, cfg, cache, cand.symbol, cand, d,
                                       sector, _actions(cand.symbol), stcg)
        pending_entries.clear()

        # ---- 4. Manage open positions through today's session ---------------
        for pos in list(book.positions.values()):
            if pos.entry_price_raw is None:
                continue
            if pos.entry_date == d:
                continue  # entry day already processed this morning
            bar = cache.bar(pos.symbol, d)
            if bar is None:
                continue
            factor = action_factor_for_date(_actions(pos.symbol), d)
            simulate_open_day(pos, bar, cfg, action_factor=factor)
            _settle_position_money(book, pos, cfg)
            _finalize_if_closed(book, pos, stcg)

        # ---- 5. EOD: mark equity, detect drawdown kill switch ---------------
        market_value = 0.0
        for pos in book.positions.values():
            if pos.entry_price_raw is None:
                continue
            qty = pos.tranche2_qty if pos.t1_filled else \
                pos.tranche1_qty + pos.tranche2_qty
            px = cache.close(pos.symbol, d)
            if px is None:
                px = pos.entry_price_raw  # stale mark; no bar today
            market_value += qty * px
        equity = book.cash + market_value
        book.peak_equity = max(book.peak_equity, equity)
        drawdown = (book.peak_equity - equity) / book.peak_equity

        regime = warm.regime_by_date.get(d)
        book.equity_curve.append({
            "date": d, "cash": round(book.cash, 2), "equity": round(equity, 2),
            "peak": round(book.peak_equity, 2), "occupied": book.occupied(),
            "regime": regime.state.value if regime is not None else "UNKNOWN",
        })

        if drawdown >= cfg.risk.kill_switch_drawdown and not book.pending_liquidation:
            book.kill_count += 1
            book.pending_liquidation = True
            book.cooldown_until = i + 1 + cfg.risk.kill_cooldown_days
            pending_entries.clear()   # signals from last night die with the halt
            log.warning("kill switch armed on %s: drawdown %.2f%% >= %.2f%%; "
                        "liquidate at next open, cooldown %d trading days",
                        d, drawdown * 100, cfg.risk.kill_switch_drawdown * 100,
                        cfg.risk.kill_cooldown_days)

        # ---- 6. Evening: scan today's data; entries execute tomorrow --------
        if not book.pending_liquidation and not in_cooldown:
            result = decide_entries(store, cfg, d,
                                    occupied_slots=book.occupied(),
                                    active_sectors=set(book.sectors),
                                    regime=regime)
            for cand, dec in result.accepted:
                if book.occupied() + len(pending_entries) < cfg.capital.num_slots:
                    pending_entries.append((cand, dec.sector))

    # End of data: the simulation must end flat. Last close is a real,
    # tradeable price (the operator would exit with the day's liquidity).
    if dates and any(p.is_open for p in book.positions.values()):
        last = dates[-1]
        for pos in list(book.positions.values()):
            if pos.entry_price_raw is None or not pos.is_open:
                continue
            px = cache.close(pos.symbol, last) or pos.entry_price_raw
            force_exit(pos, px, last, reason=ExitReason.END_OF_RUN)
            _settle_position_money(book, pos, cfg)
            _finalize_trade(book, pos, stcg)

    if stcg is not None:
        stcg.finalize()

    events: list[TradeEvent] = []
    for pos in book.closed:
        events.extend(pos.events)
    for pos in book.positions.values():
        events.extend(pos.events)

    return BacktestResult(
        start=dates[0], end=dates[-1], events=events,
        equity=pd.DataFrame(book.equity_curve),
        trades=_build_trades_frame(book),
        stcg=stcg, regime_df=warm.regime_df,
        interest_paid=interest_total, kill_count=book.kill_count,
    )


def _build_trades_frame(book: SimBook) -> pd.DataFrame:
    rows = []
    for pos in book.closed:
        if pos.entry_price_raw is None:
            rows.append({
                "trade_id": pos.trade_id, "symbol": pos.symbol, "setup": pos.setup,
                "entry_date": None, "entry_price": None, "exit_date": None,
                "exit_reason": "ENTRY_REJECTED_GAP", "entry_cost": 0.0,
                "exit_proceeds": 0.0, "realized_pnl": 0.0, "hold_days": None,
            })
            continue
        hold = (pos.exit_date - pos.entry_date).days if pos.exit_date else None
        rows.append({
            "trade_id": pos.trade_id, "symbol": pos.symbol, "setup": pos.setup,
            "entry_date": pos.entry_date, "entry_price": pos.entry_price_raw,
            "exit_date": pos.exit_date, "exit_reason": pos.exit_reason,
            "entry_cost": round(pos.entry_cost, 2),
            "exit_proceeds": round(pos.exit_proceeds, 2),
            "realized_pnl": round(_realized_pnl(pos), 2),
            "hold_days": hold,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------

def save_artifacts(result: BacktestResult, out_dir: Path) -> dict[str, Path]:
    """Persist the event log, equity curve and trade blotter as Parquet."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    ev_rows = [{
        "event_type": e.event_type.value, "date": e.date, "symbol": e.symbol,
        "tranche": e.tranche, "price": e.price, "qty": e.qty,
        "reason": e.reason, "ambiguous_intrabar": e.ambiguous_intrabar,
        "detail": e.detail, "setup": e.setup,
    } for e in result.events]
    ev_df = pd.DataFrame(ev_rows)
    paths["events"] = out_dir / "backtest_events.parquet"
    ev_df.to_parquet(paths["events"], index=False)

    paths["equity"] = out_dir / "backtest_equity.parquet"
    result.equity.to_parquet(paths["equity"], index=False)

    if not result.trades.empty:
        paths["trades"] = out_dir / "backtest_trades.parquet"
        result.trades.to_parquet(paths["trades"], index=False)
    return paths
