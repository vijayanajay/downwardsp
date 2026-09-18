"""Live-vs-backtest equivalence: Phase 7's thesis, executed as a test.

The ledger's docstring claims "live and backtest cannot drift by construction"
— the same funnel decides, the same fill model fills, the engine's own money
path settles both books. Until now that construction was prose. This suite:

  1. replays a deterministic synthetic market through `run_backtest` (the
     simulated book),
  2. mirrors every filled engine trade into a SQLite `Ledger` exactly the way
     the operator would — the trade row is rebuilt from `decide_entries` (the
     very call `ledger --record-signal` makes), the fill and tranche exits are
     recorded as receipts (`--record-fill` / `--record-exit` semantics),
  3. asserts both books agree to the paisa: entry fills, exit dates, exit
     reasons, realized P&L, STCG, and final cash modulo the overnight
     interest the engine accrues daily and the ledger only approximates via
     wall-clock notes (the documented ponytail ceiling).

The fixture places the Setup-5 signature six sessions before the end so the
trade actually fills, fills T1 on a later session (two distinct sell days ->
DP charge twice) and time-exits on Day 5 INSIDE the data — no vacuous replay,
no END_OF_RUN fiction. If this suite fails, the Phase 7 thesis is dead and you
want to learn it here, not from a real Rs 5L account.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from nse_cash.backtest.engine import run_backtest, slot_quantity
from nse_cash.backtest.fill_model import EventType
from nse_cash.core.config import SystemConfig
from nse_cash.data.storage import MarketStore
from nse_cash.execution.ledger import Ledger
from nse_cash.funnel.pipeline import decide_entries
from nse_cash.funnel.sector_gate import UNKNOWN_SECTOR

START = date(2026, 1, 1)
N_SESSIONS = 260
SIG_INDEX = N_SESSIONS - 6          # Setup-5 signature day (signal day T)

# Exit kinds an operator can record -> the ExitReason values they produce.
OPERATOR_EXITS = {"T1_TARGET", "T2_TARGET", "STOP_HIT", "STALL_EXITED",
                  "TIME_EXITED", "KILL_SWITCH"}
EXIT_REASONS_RECORDABLE = {"TARGET_1_HIT", "TARGET_2_HIT", "TRAILING_STOP_HIT",
                           "STRUCTURAL_STOP_HIT", "STALL_48H_HIT",
                           "TIME_DAY5_HIT", "KILL_SWITCH"}


def _sessions(n: int) -> list[date]:
    days: list[date] = []
    d = START
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _store_with_equivalence_signal(tmp_path) -> MarketStore:
    """Deterministic market with exactly one Setup-5 signal at SIG_INDEX.

    SHOCK: alternating +2%/-0.5% for most of the series, then a +0.95%/session
    drift over the last 40 sessions so its residual momentum is the clear
    cross-sectional top (imom percentile 1.0). The drift's 5-day range stays
    above 3%, which blocks Setup 3 from firing on the same trend; the delivery
    shock exists only on SIG_INDEX, so Setup 5 fires exactly once. BANKX:
    alternating shape without drift or shock — the decoy that never fires.

    The trade lifecycle the fixture engineers (verified by hand):
      T+1 entry at Open (= Close_T), no touches;
      T+2 T1 fills at +2% (high pinned above target), stall check passes,
          breakeven armed at EOD;
      T+3..T+4 no touches above the breakeven stop;
      T+5 (= day_index 5 = second-to-last... final session) hard time exit.
    Two distinct sell days -> DP charge twice. prev-day low is pinned at 1.5%
    below Close_T so the <= 2.20% structural-risk gate passes comfortably.
    Indexes: geometric uptrends, so the regime is OFFENSIVE on every session.
    """
    store = MarketStore(tmp_path / "equiv.duckdb")
    sessions = _sessions(N_SESSIONS)
    n = N_SESSIONS

    idx = pd.DataFrame({
        "index_name": ["NIFTY 50"] * n + ["NIFTY 500"] * n,
        "date": sessions * 2,
        "close": [24_000.0 * 1.0005 ** i for i in range(n)]
                 + [4_600.0 * 1.0005 ** i for i in range(n)],
        "open": 0.0, "high": 0.0, "low": 0.0, "volume": 0.0,
    })
    store.upsert_market_indices(idx)

    drift_start = n - 40
    drift = 0.0095
    for sym, base in (("SHOCK", 100.0), ("BANKX", 500.0)):
        rets = np.where(np.arange(n) % 2 == 1, 0.02, -0.005)
        if sym == "SHOCK":
            rets[drift_start:] = drift
        closes = list(base * np.cumprod(1.0 + rets))
        closes[SIG_INDEX] = closes[SIG_INDEX - 1] * 1.012   # green shock candle
        # +1.2% (not 1.5%): keeps the Setup-5 stop (prev-day low = 0.998 x
        # prev open) at ~2.07% risk, inside the <= 2.20% pre-entry gate.
        opens = [closes[0]] + closes[:-1]
        highs = [max(o, c) * 1.002 for o, c in zip(opens, closes)]
        lows = [min(o, c) * 0.998 for o, c in zip(opens, closes)]
        bars = pd.DataFrame({
            "symbol": sym, "date": sessions,
            "open": opens, "high": highs, "low": lows, "close": closes,
            "volume": [100_000] * n, "turnover": [10_000_000.0] * n,
            "deliverable_qty": [40_000] * n, "delivery_pct": [40.0] * n,
            "open_adj": opens, "high_adj": highs, "low_adj": lows,
            "close_adj": closes,
            "volume_adj": [100_000.0] * n, "delivery_adj": [40_000.0] * n,
        })
        if sym == "SHOCK":
            # Structural stop = prev-day low (Setup 5): pin it at 1.5% below
            # the signal close so the 2.2% risk gate passes with margin.
            bars.loc[SIG_INDEX - 1, "low"] = closes[SIG_INDEX] * 0.985
            bars.loc[SIG_INDEX - 1, "low_adj"] = closes[SIG_INDEX] * 0.985
            # Setup-5 signature: 5x delivery, green candle, wide bar.
            bars.loc[SIG_INDEX, "delivery_adj"] = 200_000.0
            bars.loc[SIG_INDEX, "deliverable_qty"] = 200_000
            bars.loc[SIG_INDEX, "low"] = closes[SIG_INDEX] * 0.985
            bars.loc[SIG_INDEX, "low_adj"] = closes[SIG_INDEX] * 0.985
            bars.loc[SIG_INDEX, "high"] = closes[SIG_INDEX] * 1.021
            bars.loc[SIG_INDEX, "high_adj"] = closes[SIG_INDEX] * 1.021
            # T1 fill on day 2: pin that bar's high clearly above +2%.
            bars.loc[SIG_INDEX + 2, "high"] = closes[SIG_INDEX + 2] * 1.006
            bars.loc[SIG_INDEX + 2, "high_adj"] = closes[SIG_INDEX + 2] * 1.006
        store.upsert_daily_bars(bars)

    # PIT membership for the FULL range (as test_pipeline does): warmup's
    # _ensure_pit_universe would otherwise rebuild membership from bars and
    # SHOCK's fixture ADTV (close x 100k volume) never crosses the Rs 5Cr
    # 90-day gate, silently removing the signal from the universe.
    pit = pd.DataFrame({
        "date": sessions * 2,
        "symbol": ["SHOCK"] * n + ["BANKX"] * n,
        "adtv_90": [10_000_000.0] * (2 * n),
        "rank": [1] * n + [2] * n,
    })
    store.upsert_pit_universe(pit)
    return store


def _mirror_signal(led: Ledger, store: MarketStore, cfg: SystemConfig,
                   trade_id: str, fill_ev, events: list) -> None:
    """Operator workflow: rebuild the trade row via the SAME funnel call
    `ledger --record-signal` uses, then record the fill and exit receipts."""
    parts = trade_id.rsplit("-", 3)          # SHOCK-2026-12-23
    symbol = parts[0]
    signal_date = date.fromisoformat("-".join(parts[1:]))
    result = decide_entries(store, cfg, signal_date)
    matches = [(c, d) for c, d in result.accepted if c.symbol == symbol]
    assert matches, f"{trade_id}: the funnel no longer accepts its own signal"
    cand, dec = matches[0]

    qty = slot_quantity(cfg, cand.entry_ref)
    led.add_trade(
        trade_id=trade_id, symbol=symbol, setup_id=cand.setup.value,
        signal_date=signal_date, slot=1,
        sector=dec.sector if dec.sector != UNKNOWN_SECTOR else None,
        entry_ref=cand.entry_ref, structural_stop=cand.structural_stop,
        tranche1_target=cand.entry_ref * (1 + cand.tranche1_target_pct),
        tranche2_target=cand.entry_ref * (1 + cand.tranche2_target_pct),
        max_gap_pct=cfg.risk.max_gap_entry,
        tranche1_qty=qty // 2, tranche2_qty=qty - qty // 2)
    led.record_fill(trade_id, float(fill_ev.price), fill_ev.date)
    for ev in events:
        if ev.event_type is EventType.T1_TARGET:
            led.record_exit(trade_id, float(ev.price), ev.date, "T1_TARGET")
        elif (ev.tranche == 2 and ev.event_type.value in OPERATOR_EXITS):
            led.record_exit(trade_id, float(ev.price), ev.date,
                            ev.event_type.value)


def test_live_ledger_matches_backtest_book(tmp_path):
    cfg = SystemConfig()
    store = _store_with_equivalence_signal(tmp_path)
    led = Ledger(tmp_path / "ledger.sqlite3")
    try:
        dates = store.trading_dates()
        result = run_backtest(store, cfg, dates[0], dates[-1])

        # ---- the fixture must produce a real, closed, operator-shaped trade
        filled = result.trades[result.trades["entry_price"].notna()]
        assert len(filled) >= 1, "fixture produced no filled trade; it is vacuous"
        assert set(filled["exit_reason"]) <= EXIT_REASONS_RECORDABLE, \
            "fixture exits must be operator-recordable (no END_OF_RUN fiction)"

        # TradeEvent carries symbol/date but not trade_id (that lives on
        # SimPosition); the trades frame is the identity source. One event
        # stream per filled trade's symbol (the fixture fires one symbol).
        for _, t in filled.iterrows():
            evs = [e for e in result.events if e.symbol == t["symbol"]]
            fill_ev = next(e for e in evs
                           if e.event_type is EventType.ENTRY_FILLED)
            _mirror_signal(led, store, cfg, t["trade_id"], fill_ev, evs)

        # ---- the two books agree on WHICH trades exist
        ledger_ids = {t["trade_id"] for t in led.filled_trades()}
        assert ledger_ids == set(filled["trade_id"])

        # ---- per-trade equivalence: fills, exits (behavior)
        for _, t in filled.iterrows():
            pos = led.position(led.get_trade(t["trade_id"]))
            assert pos.entry_price_raw == pytest.approx(t["entry_price"])
            assert pd.Timestamp(pos.exit_date) == pd.Timestamp(t["exit_date"])
            assert pos.exit_reason == t["exit_reason"]

        # ---- whole-book equivalence: every rupee (a fresh fold is always
        # unsettled; cash_snapshot is the ledger's own settlement pass, so the
        # sums below are the settled truth for both books)
        snap = led.cash_snapshot(cfg)   # first call: zero wall-clock interest
        assert snap["cash"] == pytest.approx(
            cfg.capital.base_capital - snap["invested"] + snap["proceeds"],
            rel=1e-9)
        assert snap["invested"] == pytest.approx(
            float(filled["entry_cost"].sum()), abs=0.01), \
            "entry cost incl. buy friction must match to the paisa " \
            "(trades frame is 2dp-rounded; the ledger is the money truth)"
        assert snap["proceeds"] == pytest.approx(
            float(filled["exit_proceeds"].sum()), abs=0.01), \
            "exit proceeds incl. sell friction + DP must match to the paisa"
        engine_cash = float(result.equity["cash"].iloc[-1])
        assert engine_cash == pytest.approx(
            snap["cash"] + result.interest_paid, rel=1e-7), \
            "engine cash = ledger settled cash + daily liquid-fund interest"
        assert snap["realized_pnl"] == pytest.approx(
            float(filled["realized_pnl"].sum()), abs=0.01)
        assert snap["tax_paid"] == pytest.approx(result.stcg.tax_paid, rel=1e-9)
    finally:
        led.close()
        store.close()
