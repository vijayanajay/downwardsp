"""The daily decision pipeline (Phases 4, 5 and the Phase 6 backtest entry path).

One function, `decide_entries`, answers: "which trades would the system take
on `trade_date`, given the current book?" `nse-cash scan` renders its output as
the 10:00 AM Action Sheet; the Phase 6 backtest engine replays it day by day.
Scan and backtest cannot disagree by construction — this is the single brain.

Pure over the store + inputs: no CLI I/O, no ledger writes. Governance
exclusions are live (ASM/GSM/board-meeting flags), so backtest results are
valid wherever the governance table has history.
"""

from __future__ import annotations

import logging
from datetime import date as Date

from nse_cash.core.governance import excluded_symbols
from nse_cash.core.types import CandidateSignal, MarketRegimeState
from nse_cash.funnel.market_regime import evaluate_market_regime
from nse_cash.funnel.sector_gate import UNKNOWN_SECTOR, SectorGate
from nse_cash.funnel.stage4_gate import evaluate_stage4
from nse_cash.setups.features import load_features, refresh_features
from nse_cash.setups.ranking import evaluate_and_rank

log = logging.getLogger("nse_cash.pipeline")


class DecisionResult:
    """Everything one trading day's funnel pass produced."""

    __slots__ = ("date", "regime", "excluded_count", "candidates",
                 "accepted", "rejected")

    def __init__(self, date: Date, regime, excluded_count: int,
                 candidates: list[CandidateSignal],
                 accepted: list, rejected: list) -> None:
        self.date = date
        self.regime = regime
        self.excluded_count = excluded_count
        self.candidates = candidates
        self.accepted = accepted          # [(CandidateSignal, Stage4Decision)]
        self.rejected = rejected          # [(CandidateSignal, reason)]


def decide_entries(store, config, trade_date: Date,
                   occupied_slots: int = 0,
                   active_sectors: set[str] | None = None,
                   sector_gate: SectorGate | None = None) -> DecisionResult:
    """Run the 4-stage funnel for one trading date.

    Stage 4 capacity/sector state comes from the caller (scan passes its
    current book; the backtest passes the simulated book), so the same call
    serves both.
    """
    sectors = active_sectors if active_sectors is not None else set()
    gate = sector_gate or SectorGate()

    regime = evaluate_market_regime(store, as_of_date=trade_date)

    candidates: list[CandidateSignal] = []
    accepted: list = []
    rejected: list = []
    excluded_count = 0

    if regime.state is MarketRegimeState.OFFENSIVE_LONG:
        excluded = excluded_symbols(store.con, trade_date)
        excluded_count = len(excluded)

        if not load_features(store, trade_date).shape[0]:
            refresh_features(store, trade_date)
        feat = load_features(store, trade_date)
        if "close_raw" in feat.columns:
            feat["close_raw"] = feat["close_raw"].fillna(feat["close_adj"])

        candidates = evaluate_and_rank(
            feat[~feat["symbol"].isin(excluded)],
            nifty50_above_ema=regime.nifty50_above_ema,
        )

        for cand in candidates:
            if occupied_slots + len(accepted) >= config.capital.num_slots:
                break
            dec = evaluate_stage4(cand, occupied_slots=occupied_slots + len(accepted),
                                  active_sectors=sectors, sector_gate=gate)
            if dec.is_accepted:
                if dec.sector and dec.sector != UNKNOWN_SECTOR:
                    sectors.add(dec.sector)
                accepted.append((cand, dec))
            else:
                rejected.append((cand, dec.rejection_reason))

    return DecisionResult(trade_date, regime, excluded_count,
                          candidates, accepted, rejected)
