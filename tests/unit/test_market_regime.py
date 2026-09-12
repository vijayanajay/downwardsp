"""Unit tests for Macro Market Regime Engine (Phase 4.1)."""

from datetime import date, timedelta
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from nse_cash.core.types import MarketRegimeState
from nse_cash.data.storage import MarketStore
from nse_cash.funnel.market_regime import (compute_nifty50_ema,
                                           compute_nifty500_breadth,
                                           evaluate_market_regime,
                                           evaluate_market_regime_range)


@pytest.fixture
def mock_store(tmp_path):
    """In-memory or temporary DuckDB MarketStore with synthetic data."""
    db_file = tmp_path / "test_regime.duckdb"
    store = MarketStore(db_file)
    yield store
    store.close()


def test_compute_nifty50_ema_closed_form(mock_store):
    """Test 20-day EMA calculation against pandas ewm closed form."""
    start = date(2026, 1, 1)
    dates = [start + timedelta(days=i) for i in range(30)]
    closes = [100.0 + i * 2.0 for i in range(30)]  # linearly increasing: 100, 102, ... 158

    df = pd.DataFrame({
        "index_name": "NIFTY 50",
        "date": dates,
        "open": closes,
        "high": closes,
        "low": closes,
        "close": closes,
        "volume": 1000.0,
    })
    mock_store.upsert_market_indices(df)

    eval_date = dates[-1]
    close, ema, is_above = compute_nifty50_ema(mock_store, as_of_date=eval_date, span=20)

    # Calculate expected pandas EMA
    expected_ema = pd.Series(closes).ewm(span=20, adjust=False).mean().iloc[-1]

    assert pytest.approx(close, 1e-4) == closes[-1]
    assert pytest.approx(ema, 1e-4) == expected_ema
    assert is_above is True  # In an uptrend, close > EMA


def test_nifty50_ema_below_condition(mock_store):
    """Test when Close < EMA."""
    start = date(2026, 1, 1)
    dates = [start + timedelta(days=i) for i in range(25)]
    # High prices then sharp drop
    closes = [200.0] * 20 + [150.0, 140.0, 130.0, 120.0, 110.0]

    df = pd.DataFrame({
        "index_name": "NIFTY 50",
        "date": dates,
        "open": closes,
        "high": closes,
        "low": closes,
        "close": closes,
        "volume": 1000.0,
    })
    mock_store.upsert_market_indices(df)

    close, ema, is_above = compute_nifty50_ema(mock_store, as_of_date=dates[-1], span=20)
    assert close == 110.0
    assert close < ema
    assert is_above is False


def test_compute_nifty500_breadth_synthetic(mock_store):
    """Test market breadth with 10 synthetic stocks: 7 above SMA50 (70%) vs 3 below (30%)."""
    d1 = date(2026, 1, 1)
    d2 = date(2026, 1, 2)

    # 10 stocks on d1 and d2
    # For stock 1..7: close on d2 > close on d1 (so d2 close > sma)
    # For stock 8..10: close on d2 < close on d1 (so d2 close < sma)
    rows = []
    for i in range(1, 11):
        sym = f"SYM{i}"
        # Day 1: baseline 100.0
        rows.append({
            "symbol": sym, "date": d1, "series": "EQ",
            "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0,
            "volume": 10000, "turnover": 1000000.0, "deliverable_qty": 5000, "delivery_pct": 50.0,
        })
        # Day 2:
        c2 = 110.0 if i <= 7 else 90.0
        rows.append({
            "symbol": sym, "date": d2, "series": "EQ",
            "open": c2, "high": c2, "low": c2, "close": c2,
            "volume": 10000, "turnover": 1000000.0, "deliverable_qty": 5000, "delivery_pct": 50.0,
        })

    mock_store.upsert_daily_bars(pd.DataFrame(rows))

    breadth_pct, advancing, total, is_above_50 = compute_nifty500_breadth(mock_store, as_of_date=d2, sma_period=50)

    assert total == 10
    assert advancing == 7
    assert pytest.approx(breadth_pct, 0.1) == 70.0
    assert is_above_50 is True


def test_market_regime_truth_table(mock_store):
    """Verify the 4-way decision matrix:
    NIFTY > EMA | Breadth > 50% | State
    ------------+---------------+---------------
    True        | True          | OFFENSIVE_LONG
    True        | False         | DEFENSIVE_CASH
    False       | True          | DEFENSIVE_CASH
    False       | False         | DEFENSIVE_CASH
    """
    eval_date = date(2026, 3, 1)

    # Helper function to seed test data
    def seed(nifty_close, nifty_ema_target, advancing_count, total_count=10):
        # Seed NIFTY 50
        nifty_rows = [
            {"index_name": "NIFTY 50", "date": eval_date - timedelta(days=1), "open": nifty_ema_target, "high": nifty_ema_target, "low": nifty_ema_target, "close": nifty_ema_target, "volume": 1000.0},
            {"index_name": "NIFTY 50", "date": eval_date, "open": nifty_close, "high": nifty_close, "low": nifty_close, "close": nifty_close, "volume": 1000.0},
        ]
        mock_store.upsert_market_indices(pd.DataFrame(nifty_rows))

        # Seed universe
        bars = []
        for i in range(1, total_count + 1):
            sym = f"STK{i}"
            bars.append({"symbol": sym, "date": eval_date - timedelta(days=1), "series": "EQ", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000, "turnover": 100000.0, "deliverable_qty": 500, "delivery_pct": 50.0})
            c = 110.0 if i <= advancing_count else 90.0
            bars.append({"symbol": sym, "date": eval_date, "series": "EQ", "open": c, "high": c, "low": c, "close": c, "volume": 1000, "turnover": 100000.0, "deliverable_qty": 500, "delivery_pct": 50.0})
        mock_store.upsert_daily_bars(pd.DataFrame(bars))

    # Case 1: PASS + PASS -> OFFENSIVE_LONG (nifty 120 > ema ~102, 7/10 = 70% > 50%)
    seed(nifty_close=120.0, nifty_ema_target=100.0, advancing_count=7)
    res = evaluate_market_regime(mock_store, as_of_date=eval_date)
    assert res.state == MarketRegimeState.OFFENSIVE_LONG
    assert res.nifty50_above_ema is True
    assert res.breadth_above_50 is True

    # Case 2: PASS + FAIL -> DEFENSIVE_CASH (nifty 120 > ema ~102, 4/10 = 40% <= 50%)
    seed(nifty_close=120.0, nifty_ema_target=100.0, advancing_count=4)
    res = evaluate_market_regime(mock_store, as_of_date=eval_date)
    assert res.state == MarketRegimeState.DEFENSIVE_CASH
    assert res.nifty50_above_ema is True
    assert res.breadth_above_50 is False

    # Case 3: FAIL + PASS -> DEFENSIVE_CASH (nifty 80 <= ema ~98, 7/10 = 70% > 50%)
    seed(nifty_close=80.0, nifty_ema_target=100.0, advancing_count=7)
    res = evaluate_market_regime(mock_store, as_of_date=eval_date)
    assert res.state == MarketRegimeState.DEFENSIVE_CASH
    assert res.nifty50_above_ema is False
    assert res.breadth_above_50 is True

    # Case 4: FAIL + FAIL -> DEFENSIVE_CASH (nifty 80 <= ema ~98, 3/10 = 30% <= 50%)
    seed(nifty_close=80.0, nifty_ema_target=100.0, advancing_count=3)
    res = evaluate_market_regime(mock_store, as_of_date=eval_date)
    assert res.state == MarketRegimeState.DEFENSIVE_CASH
    assert res.nifty50_above_ema is False
    assert res.breadth_above_50 is False


def test_evaluate_market_regime_range(mock_store):
    """Test date-range vectorized regime evaluation."""
    dates = [date(2026, 4, 1) + timedelta(days=i) for i in range(5)]
    nifty_rows = []
    bars = []
    for i, d in enumerate(dates):
        # NIFTY steadily rising
        c = 20000.0 + i * 200.0
        nifty_rows.append({"index_name": "NIFTY 50", "date": d, "open": c, "high": c, "low": c, "close": c, "volume": 1000.0})
        # 5 stocks, all rising
        for s in range(5):
            sc = 100.0 + i * 5.0
            bars.append({"symbol": f"S{s}", "date": d, "series": "EQ", "open": sc, "high": sc, "low": sc, "close": sc, "volume": 1000, "turnover": 100000.0, "deliverable_qty": 500, "delivery_pct": 50.0})

    mock_store.upsert_market_indices(pd.DataFrame(nifty_rows))
    mock_store.upsert_daily_bars(pd.DataFrame(bars))

    range_df = evaluate_market_regime_range(mock_store, start_date=dates[0], end_date=dates[-1], ema_span=20, sma_period=5)
    assert len(range_df) == 5
    assert set(range_df.columns).issuperset({"date", "state", "nifty50_close", "breadth_pct"})
