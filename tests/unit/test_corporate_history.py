"""Historical corporate-action seeding & discontinuity audit tests (R2)."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from nse_cash.data.corporate_history import (DISCONTINUITY_THRESHOLD,
                                             detect_unexplained_gaps,
                                             seed_corporate_actions_from_yfinance,
                                             split_rows)
from nse_cash.data.storage import MarketStore


class TestSplitRows:
    def test_forward_split_maps_to_af_below_one(self):
        # 2:1 split (yfinance ratio 2.0): pre-ex prices halve -> AF = 0.5
        rows = split_rows("TEST", pd.Series([2.0], index=[pd.Timestamp("2021-03-01")]))
        assert len(rows) == 1
        assert rows.iloc[0]["adjustment_factor"] == 0.5
        assert rows.iloc[0]["action_type"] == "SPLIT"
        assert rows.iloc[0]["ratio_a"] == 2.0 and rows.iloc[0]["ratio_b"] == 1.0

    def test_reverse_split_maps_to_af_above_one(self):
        # 1:10 reverse (ratio 0.1): AF = 10 -> pre-ex prices x10
        rows = split_rows("TEST", pd.Series([0.1], index=[pd.Timestamp("2021-03-01")]))
        assert rows.iloc[0]["adjustment_factor"] == 10.0

    def test_bonus_ratio_uses_share_count_math(self):
        # 1:1 bonus doubles share count -> yfinance emits 2.0 -> AF 0.5
        rows = split_rows("TEST", pd.Series([2.0], index=[pd.Timestamp("2020-09-14")]))
        assert rows.iloc[0]["adjustment_factor"] == 0.5

    def test_identity_and_junk_rows_dropped(self):
        rows = split_rows("TEST", pd.Series([1.0, 0.0, float("nan"), 3.0],
                                            index=[pd.Timestamp("2020-01-01")] * 4))
        assert len(rows) == 1
        assert rows.iloc[0]["adjustment_factor"] == 1.0 / 3.0


def _store(tmp_path) -> MarketStore:
    store = MarketStore(tmp_path / "hist.duckdb")
    sessions = []
    d = date(2021, 1, 1)
    while len(sessions) < 10:
        if d.weekday() < 5:
            sessions.append(d)
        d += timedelta(days=1)
    # 100 -> 100 ... -> 50 on the last session: a -50% raw overnight move,
    # i.e. exactly what an unrecorded 2:1 split looks like in raw data.
    closes = [100.0] * 9 + [50.0]
    bars = pd.DataFrame({
        "symbol": "TEST", "date": sessions,
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": [100_000] * 10, "turnover": [10_000_000.0] * 10,
        "deliverable_qty": [40_000] * 10, "delivery_pct": [40.0] * 10,
    })
    store.upsert_daily_bars(bars)
    return store


class TestSeed:
    def test_seed_upserts_actions_and_is_idempotent(self, tmp_path):
        store = _store(tmp_path)
        try:
            splits = pd.Series([2.0], index=[pd.Timestamp("2021-01-14")])
            calls = []

            def fake_fetch(sym):
                calls.append(sym)
                return splits

            n1 = seed_corporate_actions_from_yfinance(store, symbols=["TEST"],
                                                      fetch_splits=fake_fetch)
            assert n1 == 1 and calls == ["TEST"]
            n2 = seed_corporate_actions_from_yfinance(store, symbols=["TEST"],
                                                      fetch_splits=fake_fetch)
            assert n2 == 1  # upsert, not duplicate
            total = store.con.execute(
                "SELECT count(*) FROM corporate_actions WHERE symbol = 'TEST'"
            ).fetchone()[0]
            assert total == 1
        finally:
            store.close()

    def test_seed_survives_failing_ticker(self, tmp_path):
        store = _store(tmp_path)
        try:
            def bad_fetch(sym):
                raise RuntimeError("yahoo down")

            n = seed_corporate_actions_from_yfinance(store, symbols=["TEST"],
                                                     fetch_splits=bad_fetch)
            assert n == 0
        finally:
            store.close()

    def test_adjuster_consumes_seeded_factor(self, tmp_path):
        """End-to-end: seeded 2:1 split halves pre-ex adjusted closes."""
        from nse_cash.core.adjuster import refresh_adjustments

        store = _store(tmp_path)
        try:
            splits = pd.Series([2.0], index=[pd.Timestamp("2021-01-14")])
            seed_corporate_actions_from_yfinance(store, symbols=["TEST"],
                                                 fetch_splits=lambda s: splits)
            refresh_adjustments(store)
            rows = store.con.execute("""
                SELECT date, close, close_adj FROM daily_bars
                WHERE symbol = 'TEST' ORDER BY date
            """).df()
            rows["date"] = pd.to_datetime(rows["date"]).dt.date
            ex = pd.Timestamp("2021-01-14").date()
            pre = rows[rows["date"] < ex]
            post = rows[rows["date"] >= ex]
            assert (pre["close_adj"] == 50.0).all()    # 100 * 0.5
            assert (post["close_adj"] == post["close"]).all()
        finally:
            store.close()


class TestGapAudit:
    def test_unexplained_gap_flagged(self, tmp_path):
        store = _store(tmp_path)
        try:
            gaps = detect_unexplained_gaps(store)
            assert len(gaps) == 1
            g = gaps.iloc[0]
            assert g["symbol"] == "TEST"
            assert g["implied_factor"] == 0.5
            assert not g["explained"]
            # Threshold sanity: a -25% move is flagged, -10% is not
            assert DISCONTINUITY_THRESHOLD == 0.25
        finally:
            store.close()

    def test_gap_explained_by_nearby_action(self, tmp_path):
        store = _store(tmp_path)
        try:
            store.upsert_corporate_actions(pd.DataFrame([{
                "symbol": "TEST", "ex_date": date(2021, 1, 13),
                "purpose": "YF SPLIT 2:1", "action_type": "SPLIT",
                "ratio_a": 2.0, "ratio_b": 1.0, "adjustment_factor": 0.5,
            }]))
            gaps = detect_unexplained_gaps(store)
            assert len(gaps) == 1
            assert gaps.iloc[0]["explained"]
            assert gaps.iloc[0]["action_af"] == 0.5
        finally:
            store.close()

    def test_no_false_positives_on_normal_vol(self, tmp_path):
        store = MarketStore(tmp_path / "calm.duckdb")
        try:
            sessions = []
            d = date(2021, 1, 1)
            while len(sessions) < 8:
                if d.weekday() < 5:
                    sessions.append(d)
                d += timedelta(days=1)
            closes = [100.0, 105.0, 99.0, 110.0, 95.0, 108.0, 102.0, 112.0]
            store.upsert_daily_bars(pd.DataFrame({
                "symbol": "CALM", "date": sessions,
                "open": closes, "high": closes, "low": closes, "close": closes,
                "volume": [100_000] * 8, "turnover": [10_000_000.0] * 8,
            }))
            assert detect_unexplained_gaps(store).empty
        finally:
            store.close()
