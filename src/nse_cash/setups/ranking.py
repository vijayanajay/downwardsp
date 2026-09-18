"""Asymmetric Runner Skew Score & priority ranker (Phase 5.7).

S_runner = 0.35 * Z_delivery_norm + 0.35 * iMOM_percentile + 0.30 * (1 - PV_percentile)

All three terms live on [0, 1] (CR-2026-001 Issue 3): Z_delivery is winsorized
at +3 sigma at feature time and normalized to [0, 1] here via z/3. The old
raw-z formula locked every dry-volume setup out of the 0.70 bar: measured on
593k feature rows, a dry-up day has z <= 0, capping S_runner at ~0.645 — of
50,217 Setup-2 predicate fires exactly 1 cleared the old threshold.

Candidates with S_runner >= S_RUNNER_MIN (0.45; measured admission: ~8.1% of
Setup-2 fires, 72.5% of Setup-3) are flagged high-conviction runners.

Cross-setup dedupe happens here: one symbol = one signal (highest S_runner).

Funnel diagnostics: every scan logs, per setup, how many rows fired the
predicate -> survived the stop gate -> survived the S threshold (the Phase 6
R5 attribution instrument). If a setup's counts go to zero in-sample, the
funnel log shows WHICH stage starved it — no more analytic guessing.
"""

from __future__ import annotations

import logging
from datetime import date as Date

import pandas as pd

from nse_cash.core.constants import (DELIVERY_Z_NORM_MAX, GTT_STOP_LIMIT_BUFFER,
                                     S_RUNNER_MIN)
from nse_cash.core.tick import round_to_tick
from nse_cash.core.types import CandidateSignal, SetupID
from nse_cash.setups.catalog import SETUP_EVALUATORS

log = logging.getLogger("nse_cash.ranking")

S_RUNNER_WEIGHTS = (0.35, 0.35, 0.30)  # Z_delivery_norm, iMOM_pct, (1 - PV_pct)


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
    )


def evaluate_and_rank(features_df: pd.DataFrame,
                      nifty50_above_ema: bool = True,
                      s_runner_min: float = S_RUNNER_MIN,
                      gtt_stop_limit_buffer: float = GTT_STOP_LIMIT_BUFFER) -> list[CandidateSignal]:
    """Run all 5 setup predicates over a point-in-time feature snapshot.

    Returns candidates sorted by S_runner desc, one per (symbol, date) —
    deduped to the best setup per symbol-day — filtered to
    S_runner >= s_runner_min and structural risk within the setup's own stop
    gate.
    """
    candidates: list[CandidateSignal] = []
    # Funnel attribution (Phase 6 R5): per-setup counts at each elimination
    # stage. One INFO line per scan; zero cost when nothing fires.
    fired: dict[SetupID, int] = {}
    gated: dict[SetupID, int] = {}
    qualified: dict[SetupID, int] = {}
    if features_df.empty:
        return candidates

    for _, row in features_df.iterrows():
        close_raw = float(row.get("close_raw", row.get("close_adj")) or 0.0)
        if close_raw <= 0:
            continue
        best: CandidateSignal | None = None
        for setup_id, evaluator in SETUP_EVALUATORS:
            try:
                params = evaluator(row, nifty50_above_ema) \
                    if setup_id is SetupID.SETUP_3_RS_BASE else evaluator(row)
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
        if best is not None and best.s_runner >= s_runner_min:
            candidates.append(best)
            qualified[best.setup] = qualified.get(best.setup, 0) + 1

    if fired or qualified:
        funnel = " | ".join(
            f"{sid.value}: {fired.get(sid, 0)} fired -> "
            f"{gated.get(sid, 0)} stop-ok -> {qualified.get(sid, 0)} S>={s_runner_min:.2f}"
            for sid in sorted(set(fired) | set(qualified), key=lambda s: s.value))
        log.info("funnel: %s", funnel)

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
    log.info("ranker: %d candidate(s) >= %.2f", len(candidates), s_runner_min)
    return candidates
