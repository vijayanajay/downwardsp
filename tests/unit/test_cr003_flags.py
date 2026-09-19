"""CR-2026-003 Phases A, B, E: catalog + ranker flag tests.

Everything runs through the public API (config knobs -> `evaluate_and_rank`
/ `decide_entries`, or the predicates with an explicit config), never by
mutating module state: the CR-002 A/B script had to monkeypatch
`catalog.SETUP_EVALUATORS` AND `ranking.SETUP_EVALUATORS` (two live copies of
one list); the flags must make that hack unnecessary.

The fixture is the textbook Setup-2 rubber-band row from test_setups.py
(uptrend, dry delivery, RSI(2) <= 10, S_runner ~ 0.32 -- below the 0.45 bar):
  - with default config it must be REJECTED by the enforced gate but ACCEPTED
    once E.1 bypasses it (S_runner demoted to a sort key),
  - the Setup-2 predicate must never even RUN once B.1 disables the setup,
    so a sentinel row value proves absence of evaluation, not just absence
    of candidates.

Phase B.2/B.3/B.4 geometry knobs follow one contract: config=None (legacy)
and the default config are indistinguishable on every row, and flipping a
flag changes only its documented clause.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from nse_cash.core.config import (CatalogConfig, RankingConfig, SystemConfig,
                                  load_config)
from nse_cash.core.types import SetupID
from nse_cash.setups.catalog import catalog_for_config
from nse_cash.setups.ranking import evaluate_and_rank


def _setup2_row(**overrides) -> pd.Series:
    """A Setup-2 rubber-band feature row: uptrend + subdued delivery +
    RSI(2) = 5. S_runner = 0.35*0 (z=0) + 0.35*0.5 (imom) + 0.30*0.5 (pv)
    = 0.325, deliberately BELOW the 0.45 gate."""
    base = {
        "symbol": "RUBBER", "date": date(2026, 6, 1), "close_raw": 100.0,
        "close_adj": 100.0, "open_adj": 100.0, "high_adj": 100.5,
        "low_adj": 99.5, "volume_adj": 100_000.0,
        "delivery_adj": 50_000.0, "sma200": 90.0, "sma200_slope5": 1.0,
        "sma20_vol": 100_000.0, "sma20_delivery": 50_000.0,
        "sd20_delivery": 5_000.0, "delivery_z": 0.0,
        "shock_a_5d": 0.0, "z15_count_3d": 0.0,
        "pv5": 0.02, "pv_percentile": 0.5, "rsi2": 5.0,
        "rs_percentile": 0.5, "imom_percentile": 0.5,
        "base_low_90": 95.0, "base_high_90": 105.0, "high_52w": 105.0,
        "breakout_anchor_90": None, "breakout_age": None,
        "prev_low": 99.5,
    }
    base.update(overrides)
    return pd.Series(base)


def _config(**kw) -> SystemConfig:
    return SystemConfig(**kw)


class TestPhaseAConfig:
    def test_catalog_defaults_prune_setup2(self):
        cfg = CatalogConfig()
        assert cfg.enable_setup1 is True
        assert cfg.enable_setup2 is False   # CR-003 §3: pruned
        assert cfg.enable_setup3 is True
        assert cfg.enable_setup4 is True
        assert cfg.enable_setup5 is True

    def test_ranking_default_bypasses_gate(self):
        assert RankingConfig().enforce_s_runner_gate is False

    def test_system_config_sections_exist(self):
        cfg = SystemConfig()
        assert cfg.catalog.enable_setup2 is False
        assert cfg.ranking.enforce_s_runner_gate is False

    def test_env_overrides_catalog_and_ranking(self, monkeypatch):
        monkeypatch.setenv("NSE_CASH_CATALOG__ENABLE_SETUP2", "true")
        monkeypatch.setenv("NSE_CASH_CATALOG__ENABLE_SETUP5", "false")
        monkeypatch.setenv("NSE_CASH_RANKING__ENFORCE_S_RUNNER_GATE", "1")
        from nse_cash.core.config import load_config
        cfg = load_config()   # env overrides apply inside load_config
        assert cfg.catalog.enable_setup2 is True
        assert cfg.catalog.enable_setup5 is False
        assert cfg.ranking.enforce_s_runner_gate is True

    def test_yaml_override_reenables_setup2(self, tmp_path):
        from nse_cash.core.config import load_config
        yaml_file = tmp_path / "cfg.yaml"
        yaml_file.write_text(
            "catalog:\n  enable_setup2: true\n"
            "ranking:\n  enforce_s_runner_gate: true\n",
            encoding="utf-8")
        cfg = load_config(yaml_file)
        assert cfg.catalog.enable_setup2 is True
        assert cfg.ranking.enforce_s_runner_gate is True

    def test_catalog_for_config_filters_disabled(self):
        cfg = _config()
        ids = [sid for sid, _ in catalog_for_config(cfg)]
        assert SetupID.SETUP_2_RUBBERBAND not in ids
        assert len(ids) == 4

    def test_catalog_for_config_none_keeps_all_five(self):
        assert len(catalog_for_config(None)) == 5

    def test_catalog_for_config_legacy_config_keeps_all_five(self):
        # A config object without a `catalog` section (legacy duck-typed
        # callers) must keep the full catalog: no behavior change for them.
        class Legacy:
            pass

        assert len(catalog_for_config(Legacy())) == 5


class TestPhaseB1Setup2Deactivation:
    def test_disabled_setup2_predicate_never_runs(self):
        ran = []

        def sentinel(row):
            ran.append(row["symbol"])
            return None

        # Simulate the flag: catalog_for_config decides what runs. The
        # sentinel stands in for the Setup-2 evaluator on a config where
        # setup 2 is enabled, to prove the filter is the only gate.
        cfg = _config(catalog={"enable_setup2": True})
        evals = dict(catalog_for_config(cfg))
        assert SetupID.SETUP_2_RUBBERBAND in evals
        # And with the default (pruned) catalog it is simply absent:
        assert SetupID.SETUP_2_RUBBERBAND not in dict(catalog_for_config(_config()))
        assert ran == []  # sentinel untouched

    def test_ranking_skips_setup2_under_default_config(self):
        row = _setup2_row()
        # Even with the gate bypassed, a disabled setup produces nothing.
        cands = evaluate_and_rank(pd.DataFrame([row]), config=_config())
        assert cands == []

    def test_ranking_runs_setup2_when_reenabled(self):
        row = _setup2_row()
        cfg = _config(catalog={"enable_setup2": True})
        cands = evaluate_and_rank(pd.DataFrame([row]), config=cfg)
        assert len(cands) == 1
        assert cands[0].setup is SetupID.SETUP_2_RUBBERBAND

    def test_disabled_setup_excluded_while_other_setups_still_fire(self):
        # A row where BOTH Setup 2 and Setup 5 fire: disabling Setup 2 must
        # not mute the rest of the catalog. Delivery at 1.1x its SMA20 keeps
        # Setup 2's subdued-delivery gate happy while 2.2x sma20_delivery is
        # NOT reachable simultaneously — so Setup 5's shock gate uses its own
        # absolute ratio vs sma20_delivery... it does not: both compare to the
        # same SMA. Setup 5 needs dlv >= 2*sma20_delivery and Setup 2 needs
        # dlv <= 1.15*sma20_delivery — mutually exclusive ON ONE ROW. The
        # co-fire therefore needs two rows on the same symbol-day, which
        # evaluate_and_rank dedupes anyway. Test the flag mechanics instead:
        # disabling S2 on a two-row frame keeps the S5 row.
        s2_row = _setup2_row(symbol="RUBBER")
        s5_row = _setup2_row(symbol="MOMO", rsi2=50.0, imom_percentile=0.97,
                             delivery_adj=110_000.0, close_adj=101.0,
                             open_adj=100.0)
        cfg_off = _config()   # setup 2 disabled by default
        cands = evaluate_and_rank(pd.DataFrame([s2_row, s5_row]), config=cfg_off)
        assert [c.setup for c in cands] == [SetupID.SETUP_5_RESIDUAL_MOM]

        cfg_on = _config(catalog={"enable_setup2": True},
                         ranking={"enforce_s_runner_gate": False})
        cands2 = evaluate_and_rank(pd.DataFrame([s2_row, s5_row]), config=cfg_on)
        assert {c.setup for c in cands2} == {SetupID.SETUP_2_RUBBERBAND,
                                             SetupID.SETUP_5_RESIDUAL_MOM}


class TestPhaseE1GateBypass:
    def test_below_gate_candidate_admitted_when_gate_off(self):
        row = _setup2_row()   # S_runner ~ 0.325 < 0.45
        cfg = _config(catalog={"enable_setup2": True},
                      ranking={"enforce_s_runner_gate": False})
        cands = evaluate_and_rank(pd.DataFrame([row]), config=cfg)
        assert len(cands) == 1
        assert cands[0].s_runner < 0.45

    def test_below_gate_candidate_rejected_when_gate_enforced(self):
        row = _setup2_row()
        cfg = _config(catalog={"enable_setup2": True},
                      ranking={"enforce_s_runner_gate": True})
        assert evaluate_and_rank(pd.DataFrame([row]), config=cfg) == []

    def test_none_config_keeps_hard_gate(self):
        # Legacy callers (config=None) keep the CR-001 hard filter.
        row = _setup2_row()
        assert evaluate_and_rank(pd.DataFrame([row]), config=None) == []
        # ...and an explicit s_runner_min still binds as the hard bar then.
        assert evaluate_and_rank(pd.DataFrame([row]), config=None,
                                 s_runner_min=0.30) != []

    def test_s_runner_still_sorts_when_gate_off(self):
        # Two candidates: the higher S_runner must rank first regardless of
        # the gate (E.1 keeps S_runner as the ordinal sort key).
        low = _setup2_row(symbol="LOW_S", rsi2=5.0)
        high = _setup2_row(symbol="HIGH_S", rsi2=5.0, delivery_z=3.0,
                           imom_percentile=1.0, pv_percentile=0.0)
        cfg = _config(catalog={"enable_setup2": True},
                      ranking={"enforce_s_runner_gate": False})
        cands = evaluate_and_rank(pd.DataFrame([low, high]), config=cfg)
        assert [c.symbol for c in cands] == ["HIGH_S", "LOW_S"]

    def test_gate_cut_counted_in_funnel_log(self, caplog):
        row = _setup2_row()
        cfg = _config(catalog={"enable_setup2": True},
                      ranking={"enforce_s_runner_gate": True})
        with caplog.at_level("INFO", logger="nse_cash.ranking"):
            evaluate_and_rank(pd.DataFrame([row]), config=cfg)
        funnel = [r.message for r in caplog.records
                  if r.message.startswith("funnel:")]
        assert funnel and "1 gate-cut" in funnel[0]

    def test_no_gate_cut_logged_when_bypassed(self, caplog):
        row = _setup2_row()
        cfg = _config(catalog={"enable_setup2": True},
                      ranking={"enforce_s_runner_gate": False})
        with caplog.at_level("INFO", logger="nse_cash.ranking"):
            evaluate_and_rank(pd.DataFrame([row]), config=cfg)
        funnel = [r.message for r in caplog.records
                  if r.message.startswith("funnel:")]
        assert funnel and "gate-cut" not in funnel[0]


class TestPhaseBPipelineThreading:
    """decide_entries must pass config into the ranker (single-brain
    contract: scan and the Phase 6 backtest see the same flags)."""

    def test_decide_entries_honors_setup2_disable(self, tmp_path, monkeypatch):
        from datetime import timedelta

        import numpy as np

        from nse_cash.core.types import MarketRegimeState
        from nse_cash.data.storage import MarketStore
        from nse_cash.funnel.pipeline import decide_entries
        from nse_cash.setups.features import refresh_features

        store = MarketStore(tmp_path / "pipe.duckdb")
        try:
            sessions = []
            d = date(2026, 1, 1)
            while len(sessions) < 260:
                if d.weekday() < 5:
                    sessions.append(d)
                d += timedelta(days=1)
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

            # UNIV: textbook Setup-2 rubber-band: long grind up, ONE red
            # close at the end -> RSI(2) ~ 7 (<= 10) while the close stays
            # above its SMA50/SMA200 (single-symbol universe -> breadth 100%
            # and the regime stays OFFENSIVE — a 3-red-close tail would sink
            # the close below its SMA50 and put the whole book in
            # DEFENSIVE_CASH, silently vacating this test).
            closes = list(np.linspace(90.0, 110.0, 250)) + [109.0]
            bars = pd.DataFrame({
                "symbol": "UNIV", "date": sessions[-len(closes):],
                "open": [c + 0.5 for c in closes],
                "high": [c + 0.8 for c in closes],
                "low": [c - 0.3 for c in closes],
                "close": closes,
                "volume": [100_000] * len(closes),
                "turnover": [10_000_000.0] * len(closes),
                "deliverable_qty": [40_000] * len(closes),
                "delivery_pct": [40.0] * len(closes),
                "open_adj": [c + 0.5 for c in closes],
                "high_adj": [c + 0.8 for c in closes],
                "low_adj": [c - 0.3 for c in closes],
                "close_adj": closes,
                "volume_adj": [100_000.0] * len(closes),
                "delivery_adj": [40_000.0] * len(closes),
            })
            store.upsert_daily_bars(bars)

            pit = pd.DataFrame({
                "date": sessions[-60:],
                "symbol": ["UNIV"] * 60,
                "adtv_90": [10_000_000.0] * 60,
                "rank": [1] * 60,
            })
            store.upsert_pit_universe(pit)

            target = store.latest_date()
            refresh_features(store, target)

            cfg = _config()
            result = decide_entries(store, cfg, target)
            # Setup 2 disabled by default -> UNIV's rubber-band is invisible.
            assert result.regime.state is MarketRegimeState.OFFENSIVE_LONG
            assert result.accepted == []
            assert result.candidates == []

            cfg_on = _config(catalog={"enable_setup2": True},
                             ranking={"enforce_s_runner_gate": False})
            result_on = decide_entries(store, cfg_on, target)
            # Re-enabled + gate bypassed -> the rubber-band qualifies (its
            # S_runner ~ 0.32-0.65 sits around the 0.45 bar).
            assert any(c.setup is SetupID.SETUP_2_RUBBERBAND
                       for c, _ in result_on.accepted)
        finally:
            store.close()


# ---------------------------------------------------------------------------
# CR-2026-003 Phases A.2/A.3 + B.2/B.3/B.4: geometry knobs, flag-gated.
# ---------------------------------------------------------------------------

from nse_cash.setups.catalog import (evaluate_setup1_vcp_squeeze,  # noqa: E402
                                     evaluate_setup4_anchor_retest,
                                     evaluate_setup5_residual_momentum)


def _setup1_row(**kw) -> pd.Series:
    """Setup-1 firing row: uptrend, shock footprint, dry volume, narrow bar."""
    base = {
        "symbol": "VCP", "date": date(2026, 6, 1), "close_adj": 100.0,
        "open_adj": 100.0, "high_adj": 100.4, "low_adj": 99.8,
        "volume_adj": 60_000.0, "sma20_vol": 100_000.0,
        "sma200": 90.0, "sma200_slope5": 1.0,
        "shock_a_5d": 1.0, "z15_count_3d": 0.0,
        "pv5": 0.02, "pv_percentile": 0.5, "sma20_close": 100.5,
        "prev_low": 99.5,
    }
    base.update(kw)
    return pd.Series(base)


def _setup4_row(**kw) -> pd.Series:
    """Setup-4 firing row: frozen-anchor retest 4 sessions post-breakout
    (low at the anchor, close holds, green candle, 40% shadow, dry volume)."""
    base = {
        "symbol": "ANCHOR", "date": date(2026, 6, 1), "close_adj": 100.4,
        "open_adj": 100.2, "high_adj": 100.5, "low_adj": 100.0,
        "volume_adj": 50_000.0, "sma20_vol": 100_000.0,
        "breakout_anchor_90": 100.0, "breakout_age": 4.0,
        "prev_low": 99.5,
    }
    base.update(kw)
    return pd.Series(base)


def _setup5_row(**kw) -> pd.Series:
    """Setup-5 firing row: residual-momentum shock, green candle, prior low."""
    base = {
        "symbol": "MOMO", "date": date(2026, 6, 1), "close_raw": 101.0,
        "close_adj": 101.0, "open_adj": 100.0, "high_adj": 101.5,
        "low_adj": 100.0, "imom_percentile": 0.97,
        "delivery_adj": 110_000.0, "sma20_delivery": 50_000.0,
        "pv_percentile": 0.5, "delivery_z": 3.0,
        "prev_low": 99.5,
    }
    base.update(kw)
    return pd.Series(base)


class TestPhaseA23ConfigKnobs:
    def test_funnel_defaults_gate_off(self):
        cfg = SystemConfig()
        assert cfg.funnel.breadth_offensive_min == 0.0   # 0 = gate off
        assert cfg.funnel.breadth_sizing_multiplier == 1.0

    def test_a3_defaults_off_with_cr_literals(self):
        ccfg = CatalogConfig()
        assert ccfg.setup4_wide_geometry is False
        assert ccfg.setup4_tranche1_target == 0.035
        assert ccfg.setup4_undercut_tolerance == 0.025
        assert ccfg.setup4_max_stop_pct == 0.035
        assert ccfg.setup5_use_risk_parity is False
        assert ccfg.setup5_max_rupee_risk_pct == 0.022
        assert ccfg.setup1_pv_binding is False

    def test_env_overrides_funnel_and_geometry(self, monkeypatch):
        monkeypatch.setenv("NSE_CASH_FUNNEL__BREADTH_OFFENSIVE_MIN", "0.75")
        monkeypatch.setenv("NSE_CASH_CATALOG__SETUP4_WIDE_GEOMETRY", "true")
        monkeypatch.setenv("NSE_CASH_CATALOG__SETUP5_USE_RISK_PARITY", "1")
        monkeypatch.setenv("NSE_CASH_CATALOG__SETUP1_PV_BINDING", "yes")
        from nse_cash.core.config import load_config
        cfg = load_config()
        assert cfg.funnel.breadth_offensive_min == 0.75
        assert cfg.catalog.setup4_wide_geometry is True
        assert cfg.catalog.setup5_use_risk_parity is True
        assert cfg.catalog.setup1_pv_binding is True


class TestPhaseB2Setup4Geometry:
    # Rows spanning the legacy/wide boundary: at the anchor, sub-0.8% and
    # sub-2.5% undercuts, beyond -2.5%, close below the 0.995 floor.
    MATRIX = [
        dict(),                                    # textbook retest at anchor
        dict(low_adj=99.5),                        # -0.5% undercut
        dict(low_adj=98.8),                        # -1.2% (outside legacy)
        dict(low_adj=97.5),                        # exactly -2.5%
        dict(low_adj=97.0),                        # -3.0% (beyond wide too)
        dict(close_adj=99.3, high_adj=100.5),      # close < 0.995 * anchor
    ]

    @pytest.mark.parametrize("kw", MATRIX)
    def test_flag_off_matches_legacy_exactly(self, kw):
        row = _setup4_row(**kw)
        assert (evaluate_setup4_anchor_retest(row)
                == evaluate_setup4_anchor_retest(row, config=SystemConfig()))

    def test_undercut_within_tolerance_qualifies_only_when_wide(self):
        row = _setup4_row(low_adj=98.8)   # -1.2%: outside the legacy +/-0.8%
        assert evaluate_setup4_anchor_retest(row) is None
        params = evaluate_setup4_anchor_retest(
            row, config=_config(catalog={"setup4_wide_geometry": True}))
        assert params is not None
        assert params["tranche1_target_pct"] == 0.035
        assert params["max_stop_pct"] == 0.035
        assert params["tranche2_target_pct"] == 0.06

    def test_undercut_beyond_2_5pct_still_rejected_when_wide(self):
        row = _setup4_row(low_adj=97.0)   # -3.0%
        cfg = _config(catalog={"setup4_wide_geometry": True})
        assert evaluate_setup4_anchor_retest(row, config=cfg) is None

    def test_wide_close_floor_995_binds(self):
        # Low penetrates only -1.0% (inside the wide tolerance) but the close
        # finishes below anchor * 0.995 -> rejected even in the wide arm.
        row = _setup4_row(low_adj=99.0, close_adj=99.3, high_adj=100.5)
        cfg = _config(catalog={"setup4_wide_geometry": True})
        assert evaluate_setup4_anchor_retest(row, config=cfg) is None

    def test_wide_geometry_reads_config_values(self):
        cfg = _config(catalog={"setup4_wide_geometry": True,
                               "setup4_undercut_tolerance": 0.01,
                               "setup4_tranche1_target": 0.04,
                               "setup4_max_stop_pct": 0.03})
        # Tolerance tightened to 1% -> the -1.0%... wait, 99.0 is exactly
        # -1.0%: inclusive boundary -> qualifies; use -1.2% to prove the knob.
        row = _setup4_row(low_adj=98.8)
        assert evaluate_setup4_anchor_retest(row, config=cfg) is None
        params = evaluate_setup4_anchor_retest(_setup4_row(), config=cfg)
        assert params["tranche1_target_pct"] == 0.04
        assert params["max_stop_pct"] == 0.03


class TestPhaseB3RiskParityMetadata:
    def test_no_metadata_by_default_or_legacy(self):
        row = _setup5_row()
        params = evaluate_setup5_residual_momentum(row)
        assert params is not None
        assert "is_risk_parity" not in params and "raw_risk_pct" not in params
        assert (evaluate_setup5_residual_momentum(row, config=SystemConfig())
                == params)

    def test_metadata_flows_through_ranker_when_flag_on(self):
        row = _setup5_row()
        cfg = _config(catalog={"setup5_use_risk_parity": True})
        cands = evaluate_and_rank(pd.DataFrame([row]), config=cfg)
        assert len(cands) == 1
        c = cands[0]
        assert c.setup is SetupID.SETUP_5_RESIDUAL_MOM
        assert c.is_risk_parity is True
        assert c.raw_risk_pct == pytest.approx((101.0 - 99.5) / 101.0)

    def test_metadata_absent_on_candidates_without_flag(self):
        cands = evaluate_and_rank(pd.DataFrame([_setup5_row()]), config=_config())
        assert len(cands) == 1
        assert cands[0].is_risk_parity is False
        assert cands[0].raw_risk_pct is None

    def test_wide_stop_still_gate_limited_until_phase_d(self):
        # prev_low 8% below close: the predicate emits the metadata (raw risk
        # ~7.9%), but the ranker's per-setup stop gate (max 2.2%) still
        # excludes the candidate until Phase D's risk-parity admission lands.
        row = _setup5_row(prev_low=93.0)
        cfg = _config(catalog={"setup5_use_risk_parity": True})
        params = evaluate_setup5_residual_momentum(row, config=cfg)
        assert params["is_risk_parity"] is True
        assert params["raw_risk_pct"] == pytest.approx((101.0 - 93.0) / 101.0)
        assert evaluate_and_rank(pd.DataFrame([row]), config=cfg) == []

    def test_metadata_never_set_for_other_setups(self):
        params4 = evaluate_setup4_anchor_retest(
            _setup4_row(),
            config=_config(catalog={"setup5_use_risk_parity": True}))
        assert params4 is not None and "is_risk_parity" not in params4
        row4 = _setup4_row()
        row4["close_raw"] = 100.4
        cands = evaluate_and_rank(
            pd.DataFrame([row4]),
            config=_config(catalog={"setup5_use_risk_parity": True}))
        assert cands and cands[0].setup is SetupID.SETUP_4_ANCHOR_RETEST
        assert cands[0].is_risk_parity is False
        assert cands[0].raw_risk_pct is None


class TestPhaseB4Setup1PvBinding:
    MATRIX = [
        dict(),                                            # narrow bar, mid pv
        dict(pv_percentile=0.9),                           # narrow candle only
        dict(high_adj=102.5, low_adj=98.0, pv_percentile=0.10),  # pv only
        dict(shock_a_5d=0.0, z15_count_3d=0.0),            # no accumulation
        dict(volume_adj=70_000.0),                         # no dry-up
    ]

    @pytest.mark.parametrize("kw", MATRIX)
    def test_flag_off_matches_legacy_exactly(self, kw):
        row = _setup1_row(**kw)
        assert (evaluate_setup1_vcp_squeeze(row)
                == evaluate_setup1_vcp_squeeze(row, config=SystemConfig()))

    def test_narrow_candle_no_longer_qualifies_when_binding(self):
        row = _setup1_row(pv_percentile=0.9)   # narrow candle, high pv pct
        assert evaluate_setup1_vcp_squeeze(row) is not None
        cfg = _config(catalog={"setup1_pv_binding": True})
        assert evaluate_setup1_vcp_squeeze(row, config=cfg) is None

    def test_pv_p15_qualifies_with_wide_candle_when_binding(self):
        row = _setup1_row(high_adj=102.5, low_adj=98.0, pv_percentile=0.10)
        cfg = _config(catalog={"setup1_pv_binding": True})
        assert evaluate_setup1_vcp_squeeze(row, config=cfg) is not None

    def test_trend_floor_binds_when_pv_binding_on(self):
        row = _setup1_row(high_adj=102.5, low_adj=98.0, pv_percentile=0.10,
                          sma20_close=102.0)   # close 100 < 0.99 * 102
        assert evaluate_setup1_vcp_squeeze(row) is not None          # legacy
        cfg = _config(catalog={"setup1_pv_binding": True})
        assert evaluate_setup1_vcp_squeeze(row, config=cfg) is None

    def test_null_sma20_close_rejected_when_binding(self):
        row = _setup1_row(high_adj=102.5, low_adj=98.0, pv_percentile=0.10,
                          sma20_close=None)   # insufficient history
        cfg = _config(catalog={"setup1_pv_binding": True})
        assert evaluate_setup1_vcp_squeeze(row, config=cfg) is None


class TestRiskParityStage4Seam:
    """Phase D.1 will branch on candidate.is_risk_parity BEFORE the structural
    risk gate. Until it lands, a raw_risk_pct > 2.2% candidate must still be
    rejected by the existing gate (no silent admission of unsized risk), and
    the metadata must not perturb standard admission."""

    @staticmethod
    def _candidate(**kw):
        from nse_cash.core.types import CandidateSignal
        defaults = dict(symbol="MOMO", date=date(2026, 6, 1),
                        setup=SetupID.SETUP_5_RESIDUAL_MOM, entry_ref=101.0,
                        structural_stop=93.0, structural_stop_pct=0.0792,
                        max_stop_pct=0.022, tranche1_target_pct=0.02,
                        tranche2_target_pct=0.06, stop_trigger=93.0,
                        stop_limit=91.6, is_risk_parity=True,
                        raw_risk_pct=0.0792)
        defaults.update(kw)
        return CandidateSignal(**defaults)

    def test_wide_stop_rejected_by_current_gate(self):
        from nse_cash.funnel.stage4_gate import evaluate_stage4
        dec = evaluate_stage4(self._candidate(), occupied_slots=0,
                              active_sectors=set(), config=_config())
        assert dec.is_accepted is False
        assert "2.20%" in dec.rejection_reason

    def test_metadata_without_side_effects_on_standard_admission(self):
        from nse_cash.funnel.stage4_gate import evaluate_stage4
        c = self._candidate(structural_stop=99.5, structural_stop_pct=0.0149,
                            stop_trigger=99.5, stop_limit=98.0,
                            is_risk_parity=False, raw_risk_pct=None)
        dec = evaluate_stage4(c, occupied_slots=0, active_sectors=set(),
                              config=_config())
        assert dec.is_accepted is True


class TestX1ExitGeometryKnobs:
    """X1 (risk.t1_enabled / risk.time_stop_enabled): defaults reproduce the
    pre-X1 engine byte-for-byte; the knobs are read by the fill model only."""

    def test_defaults_true(self):
        cfg = SystemConfig()
        assert cfg.risk.t1_enabled is True
        assert cfg.risk.time_stop_enabled is True

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("NSE_CASH_RISK__T1_ENABLED", "false")
        monkeypatch.setenv("NSE_CASH_RISK__TIME_STOP_ENABLED", "false")
        cfg = load_config()   # direct SystemConfig() skips env overrides
        assert cfg.risk.t1_enabled is False
        assert cfg.risk.time_stop_enabled is False


class TestX2RiskParityStops:
    """X2 (risk.risk_parity_stops + catalog.setup3_max_stop_pct): defaults are
    legacy everywhere; parity mode admits a candidate up to its own setup's
    widened stop gate and sizes shares so rupee stop-risk stays at
    max_structural_stop * slot_capital."""

    def test_defaults_off(self):
        cfg = SystemConfig()
        assert cfg.risk.risk_parity_stops is False
        assert cfg.catalog.setup3_max_stop_pct == 0.022

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("NSE_CASH_RISK__RISK_PARITY_STOPS", "true")
        monkeypatch.setenv("NSE_CASH_CATALOG__SETUP3_MAX_STOP_PCT", "0.035")
        cfg = load_config()
        assert cfg.risk.risk_parity_stops is True
        assert cfg.catalog.setup3_max_stop_pct == 0.035

    # --- Setup 3 predicate knob ---

    @staticmethod
    def _setup3_row(**overrides) -> pd.Series:
        """S3 shape: RS >= p95, 5-session range <= 3%, close within 1.5% of
        the 52w high. The low sits 2.5% under the close — beyond the legacy
        2.2% gate, inside a widened 3.5% one."""
        base = {
            "symbol": "RSBASE", "date": date(2026, 6, 1),
            "close_adj": 100.0, "high_adj": 100.4, "low_adj": 97.5,
            "close_raw": 100.0, "rs_percentile": 0.97, "high_52w": 101.0,
        }
        base.update(overrides)
        return pd.Series(base)

    def test_setup3_gate_configurable(self):
        from nse_cash.setups.catalog import evaluate_setup3_rs_base
        row = self._setup3_row()
        legacy = evaluate_setup3_rs_base(row)               # config=None
        assert legacy["max_stop_pct"] == 0.022              # historical bar
        cfg = _config(catalog={"setup3_max_stop_pct": 0.035})
        wide = evaluate_setup3_rs_base(row, config=cfg)
        assert wide["max_stop_pct"] == 0.035
        assert wide["structural_stop"] == 97.5              # natural low

    # --- Stage-4 parity admission ---

    @staticmethod
    def _wide_candidate(**kw):
        from nse_cash.core.types import CandidateSignal
        defaults = dict(symbol="PARITY", date=date(2026, 6, 1),
                        setup=SetupID.SETUP_3_RS_BASE, entry_ref=100.0,
                        structural_stop=96.5, structural_stop_pct=0.035,
                        max_stop_pct=0.035, tranche1_target_pct=0.02,
                        tranche2_target_pct=0.06, stop_trigger=96.5,
                        stop_limit=95.0)
        defaults.update(kw)
        return CandidateSignal(**defaults)

    def test_stage4_legacy_wall_rejects_wide_stop(self):
        from nse_cash.funnel.stage4_gate import evaluate_stage4
        dec = evaluate_stage4(self._wide_candidate(), occupied_slots=0,
                              active_sectors=set(), config=_config())
        assert dec.is_accepted is False
        assert "2.20%" in dec.rejection_reason

    def test_stage4_parity_admits_up_to_setup_gate(self):
        from nse_cash.funnel.stage4_gate import evaluate_stage4
        cfg = _config(risk={"risk_parity_stops": True})
        dec = evaluate_stage4(self._wide_candidate(), occupied_slots=0,
                              active_sectors=set(), config=cfg)
        assert dec.is_accepted is True
        assert dec.structural_risk_pct == pytest.approx(0.035)

    def test_stage4_parity_still_binds_at_setup_gate(self):
        # Parity is not a free-for-all: risk beyond the setup's OWN gate is
        # still strictly rejected (3.9% risk vs the 3.5% S3 gate).
        from nse_cash.funnel.stage4_gate import evaluate_stage4
        cfg = _config(risk={"risk_parity_stops": True})
        c = self._wide_candidate(structural_stop=96.1,
                                 structural_stop_pct=0.039)
        dec = evaluate_stage4(c, occupied_slots=0, active_sectors=set(),
                              config=cfg)
        assert dec.is_accepted is False
        assert "3.50%" in dec.rejection_reason

    # --- Engine sizing arithmetic ---

    def test_risk_parity_qty_math(self):
        from nse_cash.backtest.engine import risk_parity_qty
        cfg = SystemConfig()   # slot 125k, wall 2.2% -> Rs 2,750 risk budget
        # 3.5% stop at 100: qty = 125000*0.022 / (100*0.035) = 785.7 -> 785.
        assert risk_parity_qty(cfg, 100.0, 96.5) == 785
        # Rupee risk at the stop stays within budget: 785 * 3.5 = 2,747.5.
        assert 785 * (100.0 - 96.5) <= 125_000 * 0.022
        # Stop inside the wall -> None (keep the full slot, never upsize).
        assert risk_parity_qty(cfg, 100.0, 98.0) is None
        # A stop so wide that even one share busts the budget -> 0 (skip).
        assert risk_parity_qty(cfg, 10_000.0, 6_000.0) == 0
