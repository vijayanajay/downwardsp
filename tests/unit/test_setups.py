"""Phase 5 unit tests: feature engine, setup predicates, S_runner ranking.

All tests are deterministic: synthetic bars engineered to trigger exactly one
setup's conditions, so a failing predicate or a broken feature value shows up
as a specific assertion, not a vague no-candidates.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from nse_cash.core.types import SetupID
from nse_cash.data.storage import MarketStore
from nse_cash.setups.catalog import (evaluate_setup1_vcp_squeeze,
                                     evaluate_setup2_rubberband,
                                     evaluate_setup3_rs_base,
                                     evaluate_setup4_anchor_retest,
                                     evaluate_setup5_residual_momentum)
from nse_cash.setups.features import _add_symbol_features, compute_features
from nse_cash.setups.ranking import compute_s_runner, evaluate_and_rank

START = date(2026, 1, 1)


def _sessions(n: int) -> list[date]:
    days = []
    d = START
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _flat_bars(sessions: list[date], price: float = 100.0,
               volume: int = 100_000, delivery: int = 50_000) -> pd.DataFrame:
    """Perfectly flat bars: no setup can fire on noise."""
    n = len(sessions)
    return pd.DataFrame({
        "symbol": "TEST",
        "date": sessions,
        "open": [price] * n,
        "high": [price * 1.001] * n,
        "low": [price * 0.999] * n,
        "close": [price] * n,
        "volume": [volume] * n,
        "volume_adj": [float(volume)] * n,
        "open_adj": [price] * n,
        "high_adj": [price * 1.001] * n,
        "low_adj": [price * 0.999] * n,
        "close_adj": [price] * n,
        "delivery_adj": [float(delivery)] * n,
        "deliverable_qty": [delivery] * n,
    })


# ---------------------------------------------------------------------------
# Feature math
# ---------------------------------------------------------------------------

class TestFeatures:
    def _rows(self, closes, highs=None, lows=None, volumes=None, deliveries=None):
        n = len(closes)
        sessions = _sessions(n)
        highs = highs or [c * 1.01 for c in closes]
        lows = lows or [c * 0.99 for c in closes]
        volumes = volumes or [100_000] * n
        deliveries = deliveries or [50_000] * n
        df = pd.DataFrame({
            "date": sessions,
            "close_adj": closes,
            "high_adj": highs,
            "low_adj": lows,
            "volume_adj": [float(v) for v in volumes],
            "delivery_adj": [float(d) for d in deliveries],
        })
        return _add_symbol_features(df)

    def test_parkinson_matches_closed_form(self):
        # Constant 2% daily range -> PV = ln(1.02/sqrt(4 ln 2)) exactly
        highs = [102.0] * 10
        lows = [100.0] * 10
        rows = self._rows([101.0] * 10, highs, lows)
        expected = np.sqrt((np.log(1.02) ** 2) * 5 / (4 * np.log(2) * 5))
        assert rows["pv5"].iloc[-1] == pytest.approx(expected, rel=1e-9)

    def test_rsi2_extreme_low_on_three_down_closes(self):
        closes = [100.0, 99.0, 98.0, 97.0]
        rows = self._rows(closes)
        # Two consecutive losses: up-EMA -> ~0, RSI(2) -> ~0
        assert rows["rsi2"].iloc[-1] < 10.0

    def test_rsi2_extreme_high_on_straight_up(self):
        closes = [100.0, 101.0, 102.0, 103.0]
        rows = self._rows(closes)
        assert rows["rsi2"].iloc[-1] > 90.0

    def test_delivery_z_winsorized_at_3(self):
        vols = [100_000] * 40
        dlvs = [50_000] * 40
        dlvs[-1] = 500_000  # ~9 sigma shock
        rows = self._rows([100.0] * 40, deliveries=dlvs, volumes=vols)
        assert rows["delivery_z"].iloc[-1] == pytest.approx(3.0)

    def test_delivery_z_zero_when_no_shock(self):
        rows = self._rows([100.0] * 40)
        assert rows["delivery_z"].iloc[-1] == pytest.approx(0.0, abs=1e-9)

    def test_pv_percentile_bounds(self):
        rng = np.random.default_rng(42)
        highs = list(100.0 + rng.uniform(0.5, 4.0, 80))
        lows = [h - r for h, r in zip(highs, rng.uniform(0.5, 3.0, 80))]
        closes = [(h + l) / 2 for h, l in zip(highs, lows)]
        rows = self._rows(closes, list(highs), list(lows))
        pv_pct = rows["pv_percentile"].dropna()
        assert ((pv_pct > 0) & (pv_pct <= 1.0)).all()

    def test_imom_nan_without_market_column(self):
        rows = self._rows([100.0] * 80)
        assert rows["imom"].isna().all()


# ---------------------------------------------------------------------------
# Setup predicates (one engineered trigger per setup)
# ---------------------------------------------------------------------------

def _row_with(**overrides):
    """A feature row that matches nothing, selectively overridden per test."""
    base = {
        "close_adj": 100.0, "open_adj": 100.0, "high_adj": 100.5, "low_adj": 99.5,
        "volume_adj": 100_000.0, "delivery_adj": 50_000.0,
        "sma200": 90.0, "sma200_slope5": 1.0,
        "sma20_vol": 100_000.0, "sma20_delivery": 50_000.0,
        "sd20_delivery": 5_000.0, "delivery_z": 0.0,
        "pv5": 0.02, "pv_percentile": 0.5, "rsi2": 50.0,
        "rs_percentile": 0.5, "imom_percentile": 0.5,
        "base_low_90": 95.0, "base_high_90": 105.0, "high_52w": 105.0,
        "prev_low": 99.5,
        "symbol": "TEST", "date": date(2026, 6, 1),
    }
    base.update(overrides)
    return pd.Series(base)


class TestSetupPredicates:
    def test_setup1_vcp_squeeze_triggers(self):
        # Single shock day: delivery >= 2.2x SMA20 AND green candle, dry volume,
        # narrow candle
        row = _row_with(
            delivery_adj=120_000.0,      # >= 2.2 * 50k
            open_adj=99.9, close_adj=100.2,  # green
            volume_adj=60_000.0,         # <= 0.65 * 100k
            high_adj=100.4, low_adj=99.8,  # range ~0.6% <= 1.5%
            delivery_z=2.4,
        )
        params = evaluate_setup1_vcp_squeeze(row)
        assert params is not None
        assert params["max_stop_pct"] == 0.022
        # min(Low_T, Low_{T-1}) = min(99.8, 99.5 default prev_low) = 99.5
        assert params["structural_stop"] == pytest.approx(99.5)

    def test_setup1_rejects_when_no_accumulation(self):
        row = _row_with(volume_adj=60_000.0, high_adj=100.4, low_adj=99.8)
        assert evaluate_setup1_vcp_squeeze(row) is None

    def test_setup2_rubberband_triggers(self):
        row = _row_with(
            delivery_adj=50_000.0,       # <= 1.15 * 50k
            rsi2=5.0,                    # <= 10
        )
        params = evaluate_setup2_rubberband(row)
        assert params is not None
        assert params["tranche1_target_pct"] == 0.018
        assert params["structural_stop"] == pytest.approx(row["low_adj"] * 0.998)

    def test_setup2_rejects_on_high_rsi(self):
        assert evaluate_setup2_rubberband(_row_with(rsi2=50.0)) is None

    def test_setup3_rs_base_triggers(self):
        row = _row_with(
            rs_percentile=0.97,          # top 5%
            high_adj=100.5, low_adj=100.0,  # 5d range proxy <= 3%
            close_adj=104.0,             # within 1.5% of 52w high 105
        )
        params = evaluate_setup3_rs_base(row, nifty50_above_ema=True)
        assert params is not None
        assert params["tranche2_target_pct"] == 0.06

    def test_setup3_blocked_when_regime_bearish(self):
        row = _row_with(rs_percentile=0.97, high_adj=100.5, low_adj=100.0,
                        close_adj=104.0)
        assert evaluate_setup3_rs_base(row, nifty50_above_ema=False) is None

    def test_setup3_rejects_far_from_52w_high(self):
        row = _row_with(rs_percentile=0.97, high_adj=100.5, low_adj=100.0,
                        close_adj=95.0)  # 9.5% below the 105 high
        assert evaluate_setup3_rs_base(row, nifty50_above_ema=True) is None

    def test_setup4_anchor_retest_triggers(self):
        # base_low 95 -> breakout level 96.9; today's low retests within 0.8%
        # candle: open 97.4, close 97.5, low 96.9, high 97.8 -> green, and the
        # lower shadow (min(O,C)-L)/(H-L) = 0.5/0.9 = 0.556 >= 0.40 (rejection tail)
        row = _row_with(
            base_low_90=95.0,
            low_adj=96.9,                # |96.9 - 96.9| / 96.9 = 0% <= 0.8%
            close_adj=97.5, open_adj=97.4,  # green
            high_adj=97.8,
            volume_adj=50_000.0,         # <= 0.55 * 100k
        )
        params = evaluate_setup4_anchor_retest(row)
        assert params is not None
        assert params["max_stop_pct"] == 0.020  # tighter anchor gate

    def test_setup4_rejects_no_retest(self):
        row = _row_with(base_low_90=95.0, low_adj=99.0,
                        close_adj=99.5, open_adj=98.5, high_adj=100.0,
                        volume_adj=50_000.0)
        assert evaluate_setup4_anchor_retest(row) is None

    def test_setup5_residual_momentum_triggers(self):
        row = _row_with(
            imom_percentile=0.97,        # top 5%
            delivery_adj=110_000.0,      # >= 2.0 * 50k
            close_adj=101.0, open_adj=100.0,  # green
        )
        params = evaluate_setup5_residual_momentum(row)
        assert params is not None
        assert params["structural_stop"] == pytest.approx(row["low_adj"])

    def test_setup5_rejects_red_candle_despite_shock(self):
        row = _row_with(imom_percentile=0.97, delivery_adj=110_000.0,
                        close_adj=99.0, open_adj=100.0)
        assert evaluate_setup5_residual_momentum(row) is None

    def test_setup5_stop_uses_prior_day_low(self):
        row = _row_with(imom_percentile=0.97, delivery_adj=110_000.0,
                        close_adj=101.0, open_adj=100.0,
                        low_adj=99.0, prev_low=97.5)
        params = evaluate_setup5_residual_momentum(row)
        assert params is not None
        assert params["structural_stop"] == pytest.approx(97.5)

    def test_setup1_stop_floors_at_prev_low(self):
        row = _row_with(
            delivery_adj=120_000.0, open_adj=99.9, close_adj=100.2,
            volume_adj=60_000.0, high_adj=100.4, low_adj=100.3,
            prev_low=98.0, delivery_z=2.4,
        )
        params = evaluate_setup1_vcp_squeeze(row)
        assert params is not None
        assert params["structural_stop"] == pytest.approx(98.0)  # min(T, T-1)

    def test_null_features_never_match(self):
        empty = pd.Series({"symbol": "X", "date": date(2026, 6, 1)})
        for evaluator in (evaluate_setup1_vcp_squeeze, evaluate_setup2_rubberband,
                          evaluate_setup3_rs_base, evaluate_setup4_anchor_retest,
                          evaluate_setup5_residual_momentum):
            if evaluator is evaluate_setup3_rs_base:
                assert evaluator(empty, nifty50_above_ema=True) is None
            else:
                assert evaluator(empty) is None


# ---------------------------------------------------------------------------
# S_runner & ranking
# ---------------------------------------------------------------------------

class TestRanking:
    def test_s_runner_formula(self):
        # 0.35*3.0 + 0.35*0.9 + 0.30*(1-0.1) = 1.05 + 0.315 + 0.27 = 1.635
        assert compute_s_runner(3.0, 0.9, 0.1) == pytest.approx(1.635)

    def test_s_runner_null_terms_score_zero(self):
        assert compute_s_runner(None, None, None) == 0.0
        # NULL pv treated as worst (1.0) -> contributes 0
        assert compute_s_runner(2.0, 0.9, None) == pytest.approx(0.7 + 0.315)

    def _store_with_universe(self, tmp_path):
        """Store with NIFTY indices + two symbols so cross-sectional ranks exist."""
        store = MarketStore(tmp_path / "feat.duckdb")
        sessions = _sessions(260)
        # Seeded random walks: a perfectly linear index would give
        # var(r_m) ~ 0 and explode the closed-form iMOM beta.
        rng = np.random.default_rng(7)
        n50 = list(24_000.0 * np.cumprod(1.0 + rng.normal(0.0004, 0.007, len(sessions))))
        n500 = list(4_600.0 * np.cumprod(1.0 + rng.normal(0.0004, 0.006, len(sessions))))
        idx = pd.DataFrame({
            "index_name": ["NIFTY 50"] * len(sessions) + ["NIFTY 500"] * len(sessions),
            "date": sessions * 2,
            "close": n50 + n500,
            "open": 0.0, "high": 0.0, "low": 0.0, "volume": 0.0,
        })
        store.upsert_market_indices(idx)

        # UNIV: textbook Setup 2 rubber-band (uptrend, 3 red closes, RSI(2) ~ 0)
        closes = list(np.linspace(90.0, 110.0, 250)) + [109.0, 108.0, 107.0]
        bars = _flat_bars(sessions, volume=100_000, delivery=50_000).iloc[-len(closes):].copy()
        bars["symbol"] = "UNIV"
        bars["date"] = sessions[-len(closes):]
        bars["close"] = closes
        bars["close_adj"] = closes
        bars["open"] = [c + 0.5 for c in closes]   # red candles
        bars["open_adj"] = [c + 0.5 for c in closes]
        bars["high_adj"] = [c + 0.8 for c in closes]
        bars["low_adj"] = [c - 0.3 for c in closes]
        bars["high"] = bars["high_adj"]
        bars["low"] = bars["low_adj"]
        bars["open"] = bars["open_adj"]
        bars["volume_adj"] = [100_000.0] * len(closes)
        bars["delivery_adj"] = [40_000.0] * len(closes)
        bars["volume"] = 100_000
        bars["deliverable_qty"] = 40_000
        store.upsert_daily_bars(bars)

        # SHOCK: idiosyncratic runner (Setup 5) - zig-zag uptrend whose residual
        # momentum tops the cross-section, ending on a delivery shock + green
        # candle. S_runner = 0.35*3.0 + 0.35*1.0 + 0.30*(1 - PV%) >= 1.40 clears
        # the 0.70 bar; UNIV (subdued delivery by design, z = 0) cannot.
        n = len(sessions)
        # Odd indices up => the session before the final shock day is a DOWN
        # day, so its low sits ~1.7% below the close (inside the 2.2% gate).
        rets = np.where(np.arange(n) % 2 == 1, 0.02, -0.005)
        shock_close = list(100.0 * np.cumprod(1.0 + rets))
        shock_close[-1] = shock_close[-2] * 1.015          # green shock day
        shock_open = [shock_close[0]] + shock_close[:-1]
        shock_high = [max(o, c) * 1.002 for o, c in zip(shock_open, shock_close)]
        shock_low = [min(o, c) * 0.998 for o, c in zip(shock_open, shock_close)]
        shock = _flat_bars(sessions, volume=100_000, delivery=40_000).copy()
        shock["symbol"] = "SHOCK"
        shock["close"] = shock_close
        shock["close_adj"] = shock_close
        shock["open"] = shock_open
        shock["open_adj"] = shock_open
        shock["high"] = shock_high
        shock["high_adj"] = shock_high
        shock["low"] = shock_low
        shock["low_adj"] = shock_low
        # Final day is a wide delivery-shock candle (+3.6% range) so Setup 3's
        # 5-session range cap can never co-fire with Setup 5.
        shock.loc[shock.index[-1], "high"] = shock_close[-1] * 1.021
        shock.loc[shock.index[-1], "high_adj"] = shock_close[-1] * 1.021
        shock.loc[shock.index[-1], "low"] = shock_close[-1] * 0.985
        shock.loc[shock.index[-1], "low_adj"] = shock_close[-1] * 0.985
        shock.loc[shock.index[-1], "delivery_adj"] = 200_000.0   # z ~ 4.4 -> clamped 3.0
        shock.loc[shock.index[-1], "deliverable_qty"] = 200_000
        store.upsert_daily_bars(shock)

        # PIT universe: tail sessions for both symbols so joins resolve
        pit = pd.DataFrame({
            "date": sessions[-60:] * 2,
            "symbol": ["UNIV"] * 60 + ["SHOCK"] * 60,
            "adtv_90": [10_000_000.0] * 120,
            "rank": [1] * 60 + [2] * 60,
        })
        store.upsert_pit_universe(pit)
        return store

    def test_compute_and_rank_end_to_end(self, tmp_path):
        store = self._store_with_universe(tmp_path)
        try:
            target = store.latest_date()
            feats = compute_features(store, target)
            assert not feats.empty
            assert "imom_percentile" in feats.columns
            # persistence: same path the scan CLI takes
            from nse_cash.setups.features import refresh_features, load_features
            refresh_features(store, target)
            feats = load_features(store, target)  # PIT read: target date only
            # SHOCK tops the cross-sectional iMOM rank (needs 36d regression
            # + 20d sum -> ~56 warm-up sessions)
            assert feats["imom_percentile"].max() == pytest.approx(1.0)

            feats["close_raw"] = feats["close_adj"]
            cands = evaluate_and_rank(feats, nifty50_above_ema=True)
            assert len(cands) >= 1
            top = cands[0]
            # Only SHOCK clears the 0.70 conviction bar; UNIV's subdued-delivery
            # Setup 2 caps S_runner near 0.33 and is filtered by design.
            assert top.symbol == "SHOCK"
            assert top.setup == SetupID.SETUP_5_RESIDUAL_MOM
            assert top.s_runner >= 0.70
            assert all(c.symbol != "UNIV" for c in cands)
            assert top.structural_stop_pct <= top.max_stop_pct + 1e-7
            # persistence: features land in DuckDB
            n = store.con.execute(
                "SELECT count(*) FROM features WHERE date = ?",
                [target]).fetchone()[0]
            assert n == len(feats)
        finally:
            store.close()

    def test_rank_dedupes_per_symbol(self, tmp_path):
        store = self._store_with_universe(tmp_path)
        try:
            target = store.latest_date()
            feats = compute_features(store, target)
            feats = feats[feats["date"] == target]  # scan = one PIT date
            feats["close_raw"] = feats["close_adj"]
            feats = pd.concat([feats, feats], ignore_index=True)  # duplicate rows
            cands = evaluate_and_rank(feats, nifty50_above_ema=True)
            symbols = [c.symbol for c in cands]
            assert len(symbols) == len(set(symbols))
        finally:
            store.close()
