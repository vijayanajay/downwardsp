"""Integration test: storage layer + adjuster + PIT universe + governance on synthetic data."""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, "src")

from nse_cash.data.storage import MarketStore


@pytest.fixture()
def store(tmp_path):
    s = MarketStore(tmp_path / "test.duckdb")
    yield s
    s.close()


def _bars(symbol: str, dates: list, base: float = 100.0, vol: int = 600_000) -> pd.DataFrame:
    n = len(dates)
    return pd.DataFrame({
        "symbol": [symbol] * n,
        "date": dates,
        "series": ["EQ"] * n,
        "open": [base] * n,
        "high": [base * 1.02] * n,
        "low": [base * 0.98] * n,
        "close": [base * 1.01] * n,
        "last": [base * 1.01] * n,
        "volume": [vol] * n,
        "turnover": [base * vol] * n,
        "deliverable_qty": [int(vol * 0.5)] * n,
        "delivery_pct": [50.0] * n,
    })


def test_upsert_daily_bars_idempotent(store):
    dates = [date(2026, 9, 1) + timedelta(days=i) for i in range(3)]
    df = _bars("TCS", dates)
    assert store.upsert_daily_bars(df) == 3
    # Re-upsert same data: no duplicates
    store.upsert_daily_bars(df)
    n = store.con.execute("SELECT count(*) FROM daily_bars").fetchone()[0]
    assert n == 3
    dates_out = store.trading_dates()
    assert len(dates_out) == 3
    assert store.symbol_count() == 1


def test_upsert_daily_bars_replaces_changed_row(store):
    dates = [date(2026, 9, 1)]
    df = _bars("TCS", dates, base=100.0)
    store.upsert_daily_bars(df)
    df2 = _bars("TCS", dates, base=200.0)
    store.upsert_daily_bars(df2)
    row = store.con.execute("SELECT close FROM daily_bars").fetchone()
    assert row[0] == pytest.approx(202.0)


def test_parquet_export_partitioned_by_year(store, tmp_path):
    dates = [date(2025, 12, 31), date(2026, 1, 2)]
    store.upsert_daily_bars(_bars("TCS", dates))
    out_dir = tmp_path / "processed"
    written = store.parquet_export(out_dir)
    assert [p.name for p in written] == ["daily_bars_2025.parquet", "daily_bars_2026.parquet"]
    for p in written:
        assert p.exists()


def test_parquet_export_selective_year(store, tmp_path):
    dates = [date(2025, 12, 31), date(2026, 1, 2)]
    store.upsert_daily_bars(_bars("TCS", dates))
    out_dir = tmp_path / "processed_single"
    written = store.parquet_export(out_dir, year=2026)
    assert [p.name for p in written] == ["daily_bars_2026.parquet"]
    assert written[0].exists()


def test_adjuster_and_universe_end_to_end(store, tmp_path):
    """Full Phase 3 flow on synthetic data: bars -> actions -> adjustments -> PIT."""
    from nse_cash.core.adjuster import refresh_adjustments
    from nse_cash.core.universe import build_pit_universe

    # 120 trading days of history for 3 symbols with distinct liquidity
    start = date(2026, 1, 1)
    dates, day = [], start
    while len(dates) < 120:
        if day.weekday() < 5:
            dates.append(day)
        day += timedelta(days=1)

    store.upsert_daily_bars(_bars("MEGA", dates, base=1000.0, vol=2_000_000))     # 200 Cr
    store.upsert_daily_bars(_bars("MID", dates, base=100.0, vol=1_000_000))       # 10 Cr
    store.upsert_daily_bars(_bars("PENNY", dates, base=10.0, vol=5_000_000))      # fails price floor

    # A 1:1 bonus on MID mid-series
    actions = pd.DataFrame({
        "symbol": ["MID"], "ex_date": [dates[100]], "purpose": ["BONUS 1:1"],
        "action_type": ["BONUS"], "ratio_a": [1.0], "ratio_b": [1.0],
        "adjustment_factor": [None],
    })
    store.upsert_corporate_actions(actions)

    n = refresh_adjustments(store)
    assert n >= 1
    # MID pre-bonus prices halved
    pre = store.con.execute(
        "SELECT close_adj FROM daily_bars WHERE symbol='MID' AND date = ?",
        [dates[0]]).fetchone()[0]
    assert pre == pytest.approx(101.0 * 0.5)
    post = store.con.execute(
        "SELECT close_adj FROM daily_bars WHERE symbol='MID' AND date = ?",
        [dates[119]]).fetchone()[0]
    assert post == pytest.approx(101.0)
    # Action factor recorded for audit
    af = store.con.execute(
        "SELECT adjustment_factor FROM corporate_actions WHERE symbol='MID'").fetchone()[0]
    assert af == pytest.approx(0.5)

    # PIT universe: MEGA + MID pass, PENNY fails price floor
    membership = build_pit_universe(store, dates[-1])
    symbols = set(membership["symbol"])
    assert "MEGA" in symbols and "MID" in symbols and "PENNY" not in symbols
    assert list(membership["rank"]) == list(range(1, len(membership) + 1))
    # Ranked by ADTV descending
    adtvs = list(membership["adtv_90"])
    assert adtvs == sorted(adtvs, reverse=True)


def test_governance_persist_and_query(store):
    from nse_cash.core.governance import excluded_symbols, persist_governance

    d = date(2026, 9, 10)
    persist_governance(
        store, d,
        {"ASM": {"SKETCHY"}, "GSM": set()},
        pd.DataFrame([{"symbol": "LOCKED", "date": d}]),
        pd.DataFrame([
            {"symbol": "RESULT", "meeting_date": date(2026, 9, 12)},       # in 2 days -> must be excluded
            {"symbol": "FAR_RESULT", "meeting_date": date(2026, 12, 10)},   # in 3 months -> must NOT be excluded
        ]),
    )
    excluded = excluded_symbols(store.con, d)
    assert {"SKETCHY", "LOCKED", "RESULT"} <= excluded
    assert "FAR_RESULT" not in excluded


def test_status_cli_renders(store, tmp_path, monkeypatch, capsys):
    """`nse-cash status` end-to-end against a populated synthetic store."""
    import click

    from nse_cash.cli.status_cmd import run_status

    dates = [date(2026, 9, 1) + timedelta(days=i) for i in range(3)]
    store.upsert_daily_bars(_bars("TCS", dates))

    class Ctx:
        obj = {"config": type("C", (), {"paths": type("P", (), {
            "duckdb_path": store.db_path,
            "parquet_dir": tmp_path,
        })()})()}

    run_status(Ctx())  # should not raise
    out = capsys.readouterr().out
    assert "Dates ingested" in out
