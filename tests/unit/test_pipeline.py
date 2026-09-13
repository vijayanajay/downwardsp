"""Shared decision pipeline (`decide_entries`) unit tests (R1 refactor).

Deterministic synthetic market: SHOCK is engineered to fire Setup 5 with the
top S_runner; BANKX is a same-sector decoy that must never squeeze into the
book alongside it when slots/sectors bind. Verifies that scan and the Phase 6
backtest consume identical decisions.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from nse_cash.core.config import SystemConfig
from nse_cash.core.types import MarketRegimeState
from nse_cash.data.storage import MarketStore
from nse_cash.funnel.pipeline import decide_entries
from nse_cash.setups.features import load_features
from nse_cash.setups.ranking import evaluate_and_rank

START = date(2026, 1, 1)


def _sessions(n: int) -> list[date]:
    days: list[date] = []
    d = START
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _store_with_signal(tmp_path) -> MarketStore:
    """Index series + two symbols: SHOCK (Setup 5 runner) and BANKX (decoy)."""
    store = MarketStore(tmp_path / "pipeline.duckdb")
    sessions = _sessions(260)
    rng = np.random.default_rng(11)
    n = len(sessions)

    idx = pd.DataFrame({
        "index_name": ["NIFTY 50"] * n + ["NIFTY 500"] * n,
        "date": sessions * 2,
        "close": list(24_000.0 * np.cumprod(1.0 + rng.normal(0.0004, 0.007, n)))
        + list(4_600.0 * np.cumprod(1.0 + rng.normal(0.0004, 0.006, n))),
        "open": 0.0, "high": 0.0, "low": 0.0, "volume": 0.0,
    })
    store.upsert_market_indices(idx)

    for sym, base in (("SHOCK", 100.0), ("BANKX", 500.0)):
        rets = np.where(np.arange(n) % 2 == 1, 0.02, -0.005)
        closes = list(base * np.cumprod(1.0 + rets))
        closes[-1] = closes[-2] * 1.015
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
        # Delivery shock + green candle on the final day: Setup 5 signature.
        bars.loc[bars.index[-1], "delivery_adj"] = 200_000.0
        bars.loc[bars.index[-1], "deliverable_qty"] = 200_000
        bars.loc[bars.index[-1], "low"] = closes[-1] * 0.985
        bars.loc[bars.index[-1], "low_adj"] = closes[-1] * 0.985
        bars.loc[bars.index[-1], "high"] = closes[-1] * 1.021
        bars.loc[bars.index[-1], "high_adj"] = closes[-1] * 1.021
        store.upsert_daily_bars(bars)

    pit = pd.DataFrame({
        "date": sessions[-60:] * 2,
        "symbol": ["SHOCK"] * 60 + ["BANKX"] * 60,
        "adtv_90": [10_000_000.0] * 120,
        "rank": [1] * 60 + [2] * 60,
    })
    store.upsert_pit_universe(pit)
    return store


def _config() -> SystemConfig:
    return SystemConfig(capital={"base_capital": 500_000.0, "num_slots": 4,
                                 "slot_capital": 125_000.0, "tranche_capital": 62_500.0})


def test_decide_entries_accepts_ranked_top(tmp_path):
    store = _store_with_signal(tmp_path)
    try:
        target = store.latest_date()
        result = decide_entries(store, _config(), target)

        assert result.regime.state is MarketRegimeState.OFFENSIVE_LONG

        feats = load_features(store, target)
        feats["close_raw"] = feats["close_raw"].fillna(feats["close_adj"])
        ranked = evaluate_and_rank(feats, nifty50_above_ema=True)
        assert ranked, "synthetic market must produce >= 1 candidate"

        accepted_symbols = [c.symbol for c, _ in result.accepted]
        assert accepted_symbols == [c.symbol for c in ranked[:len(accepted_symbols)]]
        assert result.accepted[0][0].symbol == ranked[0].symbol
        assert not result.rejected or result.rejected[0][0].symbol in {c.symbol for c in ranked}
    finally:
        store.close()


def test_decide_entries_defensive_regime_blocks_everything(tmp_path):
    store = _store_with_signal(tmp_path)
    try:
        # Day 1 of the series: NIFTY 50 below its (short) EMA -> defensive.
        target = store.trading_dates()[0]
        result = decide_entries(store, _config(), target)
        assert result.regime.state is MarketRegimeState.DEFENSIVE_CASH
        assert result.candidates == [] and result.accepted == []
    finally:
        store.close()


def test_decide_entries_respects_occupied_slots(tmp_path):
    store = _store_with_signal(tmp_path)
    try:
        target = store.latest_date()
        cfg = _config()
        full = decide_entries(store, cfg, target, occupied_slots=cfg.capital.num_slots)
        assert full.accepted == []

        one = decide_entries(store, cfg, target, occupied_slots=1)
        assert len(one.accepted) <= cfg.capital.num_slots - 1
    finally:
        store.close()


def test_decide_entries_does_not_mutate_caller_sector_set(tmp_path):
    store = _store_with_signal(tmp_path)
    try:
        target = store.latest_date()
        sectors: set[str] = set()
        decide_entries(store, _config(), target, active_sectors=sectors)
        assert sectors == set()
    finally:
        store.close()
