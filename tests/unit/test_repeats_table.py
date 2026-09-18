"""Self-check for scripts/analyze.py::repeats_table (FINDINGS §8 observations).

The repeater table is a candidate pool, not a buy list. These tests pin the
three properties that keep it honest:
  1. every row carries its breakeven bar and per-stock edge (no bare win rates),
  2. tradeability is an explicit ADTV flag, not eyeballed,
  3. ordering is consistency first, then edge — never win rate alone.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "analyze.py"
_spec = importlib.util.spec_from_file_location("analyze", _SCRIPT)
assert _spec and _spec.loader
analyze = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("analyze", analyze)
_spec.loader.exec_module(analyze)

YEARS = (2020, 2021, 2022, 2023, 2024)
DAYS_PER_YEAR = 50


def _events(wins_per_year: dict[str, list[int]]) -> pd.DataFrame:
    """Deterministic events: wins_per_year[symbol] = wins in each of 5 years."""
    frames = []
    for sym, wins in wins_per_year.items():
        for year, w in zip(YEARS, wins):
            hits = np.zeros(DAYS_PER_YEAR, dtype=int)
            hits[:w] = 1
            frames.append(pd.DataFrame({"symbol": sym, "year": year, "fp_0.080": hits}))
    return pd.concat(frames, ignore_index=True)


def _stock(adtv: dict[str, float]) -> pd.DataFrame:
    return pd.DataFrame({"adtv_cr": adtv}, index=pd.Index(adtv.keys(), name="symbol"))


def test_repeats_carries_breakeven_edge_and_tradeable() -> None:
    ev = _events({"DURABLE.NS": [50, 50, 50, 50, 50], "FRAGILE.NS": [40, 40, 40, 40, 40]})
    stock = _stock({"DURABLE.NS": 120.0, "FRAGILE.NS": 5.0})
    rep = analyze.repeats_table(stock, ev, target=0.08)

    assert {"breakeven_at_target", "edge_at_target", "tradeable"} <= set(rep.columns)
    # breakeven bar matches the pooled breakeven for the target
    assert (rep["breakeven_at_target"] == round(analyze.BE_RATES[0.08], 4)).all()
    # edge = win rate minus breakeven, on every row
    assert np.allclose(
        rep["edge_at_target"],
        (rep["fp_rate_at_target"] - rep["breakeven_at_target"]).round(4),
    )
    # tradeable is an explicit ADTV threshold flag
    tradeable = rep["tradeable"].astype(bool)
    assert bool(tradeable["DURABLE.NS"]) and not bool(tradeable["FRAGILE.NS"])


def test_repeats_orders_by_consistency_then_edge_not_win_rate() -> None:
    # DURABLE: wins in all 5 years but only 20/yr. FRAGILE: wins in 4 of 5 years
    # but 50/yr — the higher win rate must NOT outrank the consistency tier.
    ev = _events({"DURABLE.NS": [20, 20, 20, 20, 20], "FRAGILE.NS": [50, 50, 50, 50, 0]})
    stock = _stock({"DURABLE.NS": 120.0, "FRAGILE.NS": 120.0})
    rep = analyze.repeats_table(stock, ev, target=0.08)

    assert list(rep.index) == ["DURABLE.NS", "FRAGILE.NS"]
    assert rep.loc["FRAGILE.NS", "fp_rate_at_target"] > rep.loc["DURABLE.NS", "fp_rate_at_target"]


def test_within_tier_higher_edge_sorts_first() -> None:
    ev = _events({"DURABLE.NS": [50, 50, 50, 50, 50], "FRAGILE.NS": [40, 40, 40, 40, 40]})
    stock = _stock({"DURABLE.NS": 120.0, "FRAGILE.NS": 120.0})
    rep = analyze.repeats_table(stock, ev, target=0.08)

    assert list(rep.index) == ["DURABLE.NS", "FRAGILE.NS"]
    assert (rep["edge_at_target"].diff().dropna() <= 0).all()


def test_durability_filter_still_applies() -> None:
    # 3 qualifying years is below the floor of 4 -> excluded entirely
    ev = _events({"DURABLE.NS": [50, 50, 50, 50, 50], "FRAGILE.NS": [50, 50, 50, 0, 0]})
    stock = _stock({"DURABLE.NS": 120.0, "FRAGILE.NS": 120.0})
    rep = analyze.repeats_table(stock, ev, target=0.08)

    assert "DURABLE.NS" in rep.index
    assert "FRAGILE.NS" not in rep.index
