"""Asymmetric Runner Skew Score & priority ranker (Phase 5.7).

S_runner = 0.35 * Z_delivery_clamped + 0.35 * iMOM_percentile + 0.30 * (1 - PV_percentile)

Both percentile terms live on [0, 1]; Z_delivery is winsorized at +3 sigma at
feature time so the first term is on the same scale. Candidates with
S_runner >= 0.70 (S_RUNNER_MIN) are flagged high-conviction runners.

Cross-setup dedupe happens here: one symbol = one signal (highest S_runner).
"""

from __future__ import annotations

import logging
from datetime import date as Date

import pandas as pd

from nse_cash.core.constants import S_RUNNER_MIN
from nse_cash.core.types import CandidateSignal, SetupID
from nse_cash.setups.catalog import SETUP_EVALUATORS

log = logging.getLogger("nse_cash.ranking")

S_RUNNER_WEIGHTS = (0.35, 0.35, 0.30)  # Z_delivery, iMOM_pct, (1 - PV_pct)


def compute_s_runner(delivery_z: float, imom_percentile: float,
                     pv_percentile: float) -> float:
    """Composite runner score. NULL inputs score 0 on that term."""
    w_z, w_m, w_pv = S_RUNNER_WEIGHTS
    z = 0.0 if delivery_z is None or pd.isna(delivery_z) else float(delivery_z)
    m = 0.0 if imom_percentile is None or pd.isna(imom_percentile) else float(imom_percentile)
    pv = 1.0 if pv_percentile is None or pd.isna(pv_percentile) else float(pv_percentile)
    return w_z * z + w_m * m + w_pv * (1.0 - pv)


def _run_id(row: pd.Series, setup_id: SetupID, params: dict,
            entry_ref_raw: float) -> CandidateSignal:
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
        s_runner=compute_s_runner(row.get("delivery_z"), row.get("imom_percentile"),
                                  row.get("pv_percentile")),
        delivery_z=float(row.get("delivery_z") or 0.0),
        imom_percentile=float(row.get("imom_percentile") or 0.0),
        pv_percentile=float(row.get("pv_percentile") or 1.0),
    )


def evaluate_and_rank(features_df: pd.DataFrame,
                      nifty50_above_ema: bool = True,
                      s_runner_min: float = S_RUNNER_MIN) -> list[CandidateSignal]:
    """Run all 5 setup predicates over a point-in-time feature snapshot.

    Returns candidates sorted by S_runner desc, one per (symbol, date) —
    deduped to the best setup per symbol-day — filtered to
    S_runner >= s_runner_min and structural risk within the setup's own stop
    gate.
    """
    candidates: list[CandidateSignal] = []
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
            sig = _run_id(row, setup_id, params, close_raw)
            gate = sig.max_stop_pct + 1e-7
            if sig.structural_stop_pct > gate:
                continue  # setup's own stop gate (pre-entry risk gate, Stage 4 re-checks)
            if best is None or sig.s_runner > best.s_runner:
                best = sig
        if best is not None and best.s_runner >= s_runner_min:
            candidates.append(best)

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
