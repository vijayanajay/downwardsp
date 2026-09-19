"""Asymmetric Runner Skew Score & priority ranker (Phase 5.7).

S_runner = 0.35 * Z_delivery_norm + 0.35 * iMOM_percentile + 0.30 * (1 - PV_percentile)

All three terms live on [0, 1] (CR-2026-001 Issue 3): Z_delivery is winsorized
at +3 sigma at feature time and normalized to [0, 1] here via z/3. The old
raw-z formula locked every dry-volume setup out of the 0.70 bar: measured on
593k feature rows, a dry-up day has z <= 0, capping S_runner at ~0.645 — of
50,217 Setup-2 predicate fires exactly 1 cleared the old threshold.

Candidates with S_runner >= S_RUNNER_MIN (0.45) are flagged high-conviction
runners. CR-2026-003 Phase E.1: the 0.45 bar is demoted to an ORDINAL SORT KEY
by default (config.ranking.enforce_s_runner_gate=False) — within-setup mining
showed it anti-selects (Setup 4's admitted cohort lost more than the rejected
one) and the realized book clusters just above the bar. Set the flag True to
restore the hard admission filter.

Cross-setup dedupe happens here: one symbol = one signal (highest S_runner).

Funnel diagnostics: every scan logs, per setup, how many rows fired the
predicate -> survived the stop gate -> qualified, plus how many qualifying
rows the S_runner gate cut when enforced (the Phase 6 R5 attribution
instrument, extended by CR-2026-003). If a setup's counts go to zero
in-sample, the funnel log shows WHICH stage starved it — no more analytic
guessing.
"""

from __future__ import annotations

import logging
from datetime import date as Date

import pandas as pd

from nse_cash.core.constants import (DELIVERY_Z_NORM_MAX, GTT_STOP_LIMIT_BUFFER,
                                     S_RUNNER_MIN)
from nse_cash.core.tick import round_to_tick
from nse_cash.core.types import CandidateSignal, SetupID
from nse_cash.setups.catalog import (SETUP_EVALUATORS, catalog_for_config)

log = logging.getLogger("nse_cash.ranking")

S_RUNNER_WEIGHTS = (0.35, 0.35, 0.30)  # Z_delivery_norm, iMOM_pct, (1 - PV_pct)

# CR-2026-003 Phase B.1 diagnostics: the complete five-setup catalog is the
# reference for reporting which setups a config disabled.
SETUP_EVALUATORS_ALL = list(SETUP_EVALUATORS)


def compute_s_runner(delivery_z: float, imom_percentile: float,
                     pv_percentile: float) -> float:
    """Composite runner score. NULL inputs score 0 on that term.

    Z_delivery is normalized from its winsorized [0, 3] scale to [0, 1]
    (max(0, z) / 3) so all three terms share one scale and a dry-up day
    (z <= 0) contributes 0 instead of a negative penalty.
    """
    w_z, w_m, w_pv = S_RUNNER_WEIGHTS
    z_raw = 0.0 if delivery_z is None or pd.isna(delivery_z) else float(delivery_z)
    z_norm = min(max(z_raw, 0.0), DELIVERY_Z_NORM_MAX) / DELIVERY_Z_NORM_MAX
    m = 0.0 if imom_percentile is None or pd.isna(imom_percentile) else float(imom_percentile)
    pv = 1.0 if pv_percentile is None or pd.isna(pv_percentile) else float(pv_percentile)
    return w_z * z_norm + w_m * m + w_pv * (1.0 - pv)


def _run_id(row: pd.Series, setup_id: SetupID, params: dict,
            entry_ref_raw: float,
            gtt_stop_limit_buffer: float = GTT_STOP_LIMIT_BUFFER) -> CandidateSignal:
    """Build a CandidateSignal from one predicate hit.

    CR-2026-003 Phase B.3: when the predicate emits risk-parity metadata
    (`is_risk_parity` / `raw_risk_pct` — Setup 5 with its flag on), it is
    carried on the candidate for Phase D's admission. Candidates without it
    default to is_risk_parity=False so no consumer can misread absent
    metadata as risk data."""
    stop_adj = float(params["structural_stop"])  # adjusted-price space
    # Raw-space stop for order placement: scale by raw/adj close ratio.
    close_raw = float(row.get("close_raw", row.get("close_adj")) or 0.0)
    close_adj = float(row.get("close_adj") or 0.0)
    scale = (close_raw / close_adj) if close_adj > 0 else 1.0
    stop_raw = stop_adj * scale

    risk = (entry_ref_raw - stop_raw) / entry_ref_raw if entry_ref_raw > 0 else 1.0
    return CandidateSignal(
        symbol=str(row["symbol"]),
        date=pd.Timestamp(row["date"]).date(),
        setup=setup_id,
        entry_ref=entry_ref_raw,
        structural_stop=round(stop_raw, 2),
        structural_stop_pct=risk,
        max_stop_pct=float(params["max_stop_pct"]),
        tranche1_target_pct=float(params["tranche1_target_pct"]),
        tranche2_target_pct=float(params["tranche2_target_pct"]),
        # CR-2026-001 Issue 4: stop as two Kite GTT inputs. The buffer comes
        # from config (threaded by the pipeline) so the sheet, the book, the
        # ledger and the backtest all carry identical trigger/limit levels.
        stop_trigger=round(stop_raw, 2),
        stop_limit=round_to_tick(stop_raw * (1.0 - gtt_stop_limit_buffer)),
        s_runner=compute_s_runner(row.get("delivery_z"), row.get("imom_percentile"),
                                  row.get("pv_percentile")),
        delivery_z=float(row.get("delivery_z") or 0.0),
        imom_percentile=float(row.get("imom_percentile") or 0.0),
        pv_percentile=float(row.get("pv_percentile") or 1.0),
        is_risk_parity=bool(params.get("is_risk_parity", False)),
        raw_risk_pct=(float(params["raw_risk_pct"])
                      if params.get("raw_risk_pct") is not None else None),
    )


def evaluate_and_rank(features_df: pd.DataFrame,
                      nifty50_above_ema: bool = True,
                      s_runner_min: float = S_RUNNER_MIN,
                      gtt_stop_limit_buffer: float = GTT_STOP_LIMIT_BUFFER,
                      config=None) -> list[CandidateSignal]:
    """Run the setup predicates over a point-in-time feature snapshot.

    Returns candidates sorted by S_runner desc, one per (symbol, date) —
    deduped to the best setup per symbol-day — and structural risk within the
    setup's own stop gate.

    `config` (optional, CR-2026-003 Phases B.1 + E.1):
      - config.catalog.enable_setupN=False removes that setup's evaluator
        before any predicate runs (Setup 2 pruned by default);
      - config.ranking.enforce_s_runner_gate=False (default) demotes the
        S_runner >= s_runner_min bar from a hard admission filter to an
        ordinal sort key: below-gate candidates still qualify and trade;
        True restores the CR-001 hard filter.
    Legacy callers (config=None) keep the exact pre-CR-003 behavior: full
    catalog, hard gate, and predicates evaluated without a config (their
    geometry flags read None -> historical behavior).

    Funnel diagnostics: every scan logs, per setup, how many rows fired the
    predicate -> survived the stop gate -> survived the S threshold (when
    enforced) -> or were cut by the gate while qualifying anyway (Phase E
    instrumentation; the log shows WHICH stage starved a setup).
    """
    candidates: list[CandidateSignal] = []
    # Funnel attribution (Phase 6 R5 + CR-2026-003 Phase E): per-setup counts
    # at each elimination stage. One INFO line per scan; zero cost when
    # nothing fires. `gate_cut` counts qualifying rows removed ONLY by the
    # S_runner bar — zero whenever the gate is bypassed.
    fired: dict[SetupID, int] = {}
    gated: dict[SetupID, int] = {}
    qualified: dict[SetupID, int] = {}
    gate_cut: dict[SetupID, int] = {}
    disabled: list[str] = []
    if features_df.empty:
        return candidates

    evaluators = catalog_for_config(config)
    enabled_ids = {sid for sid, _ in evaluators}
    disabled = [sid.value for sid, _ in SETUP_EVALUATORS_ALL
                if sid not in enabled_ids]
    enforce_gate = True if config is None else \
        bool(getattr(getattr(config, "ranking", None), "enforce_s_runner_gate", True))

    for _, row in features_df.iterrows():
        close_raw = float(row.get("close_raw", row.get("close_adj")) or 0.0)
        if close_raw <= 0:
            continue
        best: CandidateSignal | None = None
        for setup_id, evaluator in evaluators:
            try:
                params = evaluator(row, nifty50_above_ema, config) \
                    if setup_id is SetupID.SETUP_3_RS_BASE else evaluator(row, config)
            except Exception as exc:  # noqa: BLE001 - predicate must never crash a scan
                log.warning("setup %s evaluation failed for %s: %s",
                            setup_id, row.get("symbol"), exc)
                continue
            if params is None:
                continue
            fired[setup_id] = fired.get(setup_id, 0) + 1
            sig = _run_id(row, setup_id, params, close_raw,
                          gtt_stop_limit_buffer)
            gate = sig.max_stop_pct + 1e-7
            if sig.structural_stop_pct > gate:
                continue  # setup's own stop gate (pre-entry risk gate, Stage 4 re-checks)
            gated[setup_id] = gated.get(setup_id, 0) + 1
            if best is None or sig.s_runner > best.s_runner:
                best = sig
        if best is None:
            continue
        if best.s_runner >= s_runner_min:
            candidates.append(best)
            qualified[best.setup] = qualified.get(best.setup, 0) + 1
        elif not enforce_gate:
            # CR-2026-003 Phase E.1: the gate is a sort key, not a lockout.
            candidates.append(best)
        else:
            gate_cut[best.setup] = gate_cut.get(best.setup, 0) + 1

    if fired or qualified:
        funnel = " | ".join(
            f"{sid.value}: {fired.get(sid, 0)} fired -> "
            f"{gated.get(sid, 0)} stop-ok -> {qualified.get(sid, 0)} qualified"
            + (f" ({gate_cut[sid]} gate-cut)" if gate_cut.get(sid) else "")
            for sid in sorted(set(fired) | set(qualified) | set(gate_cut),
                              key=lambda s: s.value))
        log.info("funnel: %s", funnel)
    if disabled:
        log.info("catalog: disabled by config -> %s", ", ".join(disabled))

    # Dedupe per (symbol, date): duplicated feature rows or co-firing setups
    # must never yield two signals for the same slot.
    best_by_key: dict[tuple[str, Date], CandidateSignal] = {}
    for c in candidates:
        key = (c.symbol, c.date)
        if key not in best_by_key or c.s_runner > best_by_key[key].s_runner:
            best_by_key[key] = c
    # Symbol tiebreak: on equal S_runner, allocation must never depend on
    # set-iteration order (dict insertion order leaks into the book).
    candidates = sorted(best_by_key.values(),
                        key=lambda c: (-c.s_runner, c.symbol))
    log.info("ranker: %d candidate(s)%s", len(candidates),
             "" if enforce_gate else f" (S_runner as sort key; hard gate off, bar {s_runner_min:.2f})")
    return candidates
