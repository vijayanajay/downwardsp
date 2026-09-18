"""The entry path end-to-end: candidate -> SimPosition -> fill -> money.

Bypasses `decide_entries` (its ranking behavior is covered by test_pipeline.py)
by injecting a pending entry directly into a SimBook, then walking the exact
engine code paths (`_execute_pending_entry`, settlement, management).

Golden facts, hand-computed with SystemConfig() friction:
  buy_rate = stt .001 + stamp .00015 + turnover .0000345 + sebi .000001
             + slippage .0005 + (turnover+sebi)*gst .00000639 = 0.00169189
  entry_ref 100.0 -> qty = 1,25,000 // (100 * 1.00169189) = 1247 (623 T1 + 624 T2)
  entry fill at open 100.0: buy value 1,24,700, friction 210.98
"""

from __future__ import annotations

from datetime import date

import pytest

from nse_cash.backtest.engine import (SimBook, _execute_pending_entry)
from nse_cash.backtest.fill_model import EventType
from nse_cash.core.config import SystemConfig
from nse_cash.core.types import CandidateSignal, SetupID

CFG = SystemConfig()
F = CFG.friction
SLOT = CFG.capital.slot_capital
BUY_RATE = (F.stt_delivery + F.stamp_duty + F.nse_turnover + F.sebi_fee
            + F.slippage_per_side
            + (F.nse_turnover + F.sebi_fee) * F.gst_rate)

ENTRY = date(2026, 8, 4)
NEXT = date(2026, 8, 5)


def _cand(symbol: str = "SHOCK", **overrides) -> CandidateSignal:
    defaults = dict(
        symbol=symbol, date=date(2026, 8, 3), setup=SetupID.SETUP_5_RESIDUAL_MOM,
        entry_ref=100.0, structural_stop=98.0, structural_stop_pct=0.02,
        tranche1_target_pct=0.02, tranche2_target_pct=0.06, s_runner=1.4,
    )
    defaults.update(overrides)
    return CandidateSignal(**defaults)


def _book(num_slots: int = 4) -> SimBook:
    cfg = CFG.model_copy(deep=True)
    cfg.capital.num_slots = num_slots
    return SimBook(config=cfg, cash=SLOT * num_slots,
                   peak_equity=SLOT * num_slots)


class _OneBarCache:
    """Serves one bar for one symbol on the entry date."""

    def __init__(self, o: float, h: float, lo: float, c: float,
                 only_for: str | None = None) -> None:
        self.bar_data = {"open": o, "high": h, "low": lo, "close": c}
        self.only_for = only_for

    def bar(self, symbol, d):
        if self.only_for is not None and symbol != self.only_for:
            return None
        if d != ENTRY:
            return None
        return {"date": d, **self.bar_data}


class TestEntryPath:
    def test_fill_at_open_sized_net_of_buy_friction(self):
        book = _book()
        qty = int(SLOT // (100.0 * (1.0 + BUY_RATE)))
        assert qty == 1247          # golden: 1,25,000 // (100 * 1.00169189)
        _execute_pending_entry(book, CFG, _OneBarCache(100.0, 102.5, 99.5, 101.5),
                               "SHOCK", _cand(), ENTRY, "IT", None, None)

        pos = book.positions["SHOCK"]
        assert pos.entry_price_raw == 100.0
        assert (pos.tranche1_qty, pos.tranche2_qty) == (623, 624)
        # Cash debited by value + exact buy friction, credited by the same-day
        # T1 sale (623 @ 102) net of sell friction.
        t1_value = 623 * 102.0
        assert book.cash == pytest.approx(
            SLOT * 4 - (1247 * 100.0) * (1 + BUY_RATE)
            + t1_value * (1 - (F.stt_delivery + F.nse_turnover + F.sebi_fee
                               + F.slippage_per_side
                               + (F.nse_turnover + F.sebi_fee) * F.gst_rate))
            - F.dp_charge_per_sell)
        assert pos.entry_cost == pytest.approx(124_700.0 * (1 + BUY_RATE))
        # T1 target 102.0 touched by high 102.5 -> T1 fills on entry day.
        t1 = [e for e in pos.events if e.event_type is EventType.T1_TARGET]
        assert t1 and t1[0].qty == 623 and t1[0].setup == "SETUP_5_RESIDUAL_MOM"
        # Breakeven armed at EOD of the entry day.
        assert pos.pending_stop_raw == 100.0
        assert book.sectors == {"IT"} and pos.slot in {1, 2, 3, 4}

    def test_gap_rejection_frees_slot_and_records_event(self):
        book = _book()
        n_closed = len(book.closed)
        _execute_pending_entry(book, CFG, _OneBarCache(102.0, 103.0, 101.5, 102.5),
                               "SHOCK", _cand(), ENTRY, None, None, None)
        assert "SHOCK" not in book.positions
        assert len(book.free_slots) == 4
        assert len(book.closed) == n_closed + 1
        assert book.closed[-1].events[0].event_type is EventType.ENTRY_REJECTED_GAP

    def test_same_day_stop_out_finalizes_immediately(self):
        book = _book()
        _execute_pending_entry(book, CFG, _OneBarCache(97.5, 98.2, 97.0, 98.0),
                               "SHOCK", _cand(), ENTRY, None, None, None)
        assert "SHOCK" not in book.positions       # finalized
        assert len(book.free_slots) == 4           # slot returned
        assert len(book.closed) == 1               # moved to closed
        assert not book.closed[0].is_open

    def test_unknown_sector_sentinel_does_not_block_second_entry(self):
        book = _book()
        cache = _OneBarCache(100.0, 102.5, 99.5, 101.5)
        _execute_pending_entry(book, CFG, cache, "AAA", _cand("AAA"),
                               ENTRY, "Unknown", None, None)
        _execute_pending_entry(book, CFG, cache, "BBB", _cand("BBB"),
                               ENTRY, "Unknown", None, None)
        assert set(book.positions) == {"AAA", "BBB"}
        assert book.sectors == set()               # sentinel never claimed

    def test_known_sector_collision_blocks_entry(self):
        book = _book()
        cache = _OneBarCache(100.0, 102.5, 99.5, 101.5)
        _execute_pending_entry(book, CFG, cache, "AAA", _cand("AAA"),
                               ENTRY, "IT", None, None)
        _execute_pending_entry(book, CFG, cache, "BBB", _cand("BBB"),
                               ENTRY, "IT", None, None)
        assert set(book.positions) == {"AAA"}
        assert book.occupied() == 1

    def test_no_bar_on_entry_day_skips_cleanly(self):
        book = _book()
        _execute_pending_entry(book, CFG, _OneBarCache(100.0, 102.5, 99.5, 101.5,
                                                       only_for="OTHER"),
                               "SHOCK", _cand(), ENTRY, None, None, None)
        assert book.positions == {} and len(book.closed) == 0
        assert len(book.free_slots) == 4

    def test_capital_never_goes_negative_on_entry(self):
        book = _book()
        _execute_pending_entry(book, CFG, _OneBarCache(100.0, 102.5, 99.5, 101.5),
                               "SHOCK", _cand(), ENTRY, None, None, None)
        assert book.cash > 0
