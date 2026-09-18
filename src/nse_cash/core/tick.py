"""NSE price-tick rounding (CR-2026-001).

Lives in `core` so both the execution layer (ledger, action sheet) and the
setup layer (ranking -> CandidateSignal stop_limit) can use it without an
import cycle: backtest.engine imports the funnel pipeline, which imports the
ranker — the ranker may not import back from execution.
"""

from __future__ import annotations


def round_to_tick(price: float, tick: float = 0.05) -> float:
    """Round a rupee price to the NSE tick (₹0.05). Kite rejects other levels."""
    return round(round(price / tick) * tick, 2)
