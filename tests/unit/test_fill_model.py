"""Golden tests for the codified fill model (R3).

Every number below is hand-computed. `make_position` builds a canonical trade:
entry_ref 100.0, max_entry 101.2 (+1.2%), structural stop 98.0 (-2.0%),
T1 target 102.0 (+2.0%), T2 target 106.0 (+6.0%), 37 + 37 shares.

These tests ARE the specification — if a rule here changes, the model changed.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from nse_cash.backtest.fill_model import (EventType, SimPosition,
                                          action_factor_for_date,
                                          force_exit, simulate_entry_day,
                                          simulate_open_day,
                                          refresh_stops_eod)
from nse_cash.core.config import SystemConfig
from nse_cash.core.types import ExitReason

CFG = SystemConfig()

SIGNAL_DAY = date(2026, 8, 3)       # Monday (T)
ENTRY_DAY = date(2026, 8, 4)        # Tuesday (T+1)
D2 = date(2026, 8, 5)
D3 = date(2026, 8, 6)
D4 = date(2026, 8, 7)
D5 = date(2026, 8, 8)


def make_position(**overrides) -> SimPosition:
    defaults = dict(
        trade_id="T1", symbol="TEST",
        entry_ref_raw=100.0, max_entry_raw=101.2,
        structural_stop_raw=98.0,
        tranche1_target_raw=102.0, tranche2_target_raw=106.0,
        tranche1_qty=37, tranche2_qty=37,
    )
    defaults.update(overrides)
    return SimPosition(**defaults)


def bar(d, o, h, l, c):
    return {"date": d, "open": o, "high": h, "low": l, "close": c}


def events_of(pos, *types):
    return [e for e in pos.events if e.event_type in types]


# ---------------------------------------------------------------------------
# 1. Entry day
# ---------------------------------------------------------------------------

class TestEntry:
    def test_plain_fill_at_open(self):
        pos = simulate_entry_day(make_position(),
                                 bar(ENTRY_DAY, 100.5, 101.0, 99.5, 100.8), CFG)
        fill = events_of(pos, EventType.ENTRY_FILLED)[0]
        assert fill.price == 100.5 and fill.qty == 74
        assert pos.entry_price_raw == 100.5 and pos.is_open

    def test_gap_up_reject_beyond_1_2pct(self):
        # Open 102.0 > max_entry 101.2 -> no trade, whole day ignored.
        pos = simulate_entry_day(make_position(),
                                 bar(ENTRY_DAY, 102.0, 103.0, 101.5, 102.5), CFG)
        assert events_of(pos, EventType.ENTRY_REJECTED_GAP)
        assert pos.entry_price_raw is None and not pos.is_open

    def test_gap_up_capped_fill_at_max_entry(self):
        # Open 101.1 <= 101.2 fills at Open; between 101.2 exactly fills at cap.
        pos = simulate_entry_day(make_position(),
                                 bar(ENTRY_DAY, 101.2, 101.9, 100.8, 101.5), CFG)
        assert events_of(pos, EventType.ENTRY_FILLED)[0].price == 101.2

    def test_gap_down_open_accepted_at_open(self):
        pos = simulate_entry_day(make_position(),
                                 bar(ENTRY_DAY, 99.0, 99.8, 98.6, 99.5), CFG)
        assert events_of(pos, EventType.ENTRY_FILLED)[0].price == 99.0
        assert pos.is_open

    def test_entry_day_open_below_stop_same_day_scratch(self):
        # Open 97.5 < structural stop 98.0: filled at open, stop GTT placed
        # after fill triggers at once -> scratch at ~97.5, whole position.
        pos = simulate_entry_day(make_position(),
                                 bar(ENTRY_DAY, 97.5, 98.2, 97.0, 98.0), CFG)
        assert events_of(pos, EventType.ENTRY_FILLED)
        stops = events_of(pos, EventType.STOP_HIT)
        assert len(stops) == 2  # T1 (unfilled) + T2
        assert all(s.price == 97.5 for s in stops)
        assert stops[0].reason == ExitReason.STRUCTURAL_STOP_HIT.value
        assert not pos.is_open

    def test_entry_day_stop_and_target_both_inside_bar_stop_wins(self):
        # Bar 97.0-102.5 contains stop 98.0 and T1 target 102.0.
        pos = simulate_entry_day(make_position(),
                                 bar(ENTRY_DAY, 100.5, 102.5, 97.0, 101.0), CFG)
        stops = events_of(pos, EventType.STOP_HIT)
        assert len(stops) == 2 and all(s.price == 98.0 for s in stops)
        assert stops[0].ambiguous_intrabar
        assert not events_of(pos, EventType.T1_TARGET)
        assert not pos.is_open

    def test_entry_day_t1_fills_then_breakeven_arms_eod(self):
        # High 102.1 touches T1 target 102.0; close well above stall floor.
        pos = simulate_entry_day(make_position(),
                                 bar(ENTRY_DAY, 100.5, 102.1, 100.0, 101.5), CFG)
        t1 = events_of(pos, EventType.T1_TARGET)[0]
        assert t1.price == 102.0 and t1.qty == 37
        be = events_of(pos, EventType.BREAKEVEN_ARMED)
        assert be and be[0].price == 100.5  # actual fill, not Close_T
        assert pos.pending_stop_raw == 100.5


# ---------------------------------------------------------------------------
# 2. Continuation days
# ---------------------------------------------------------------------------

class TestContinuation:
    def _entered(self, o=100.5, h=101.0, l=99.5, c=100.8):
        pos = simulate_entry_day(make_position(), bar(ENTRY_DAY, o, h, l, c), CFG)
        assert pos.is_open
        return pos

    def test_gap_through_stop_exits_at_open_never_stop(self):
        # Entered at 100.5, stop 98.0; next day opens 96.0 -> exit at 96.0.
        pos = self._entered()
        pos.day_index = 1
        simulate_open_day(pos, bar(D2, 96.0, 97.5, 95.5, 97.0), CFG)
        stop = events_of(pos, EventType.STOP_HIT)[0]
        assert stop.price == 96.0, "gap-down realizes the open, not the stop"
        assert not pos.is_open

    def test_same_day_stop_and_t2_target_stop_wins_and_flagged(self):
        pos = self._entered()
        pos.day_index = 1
        # Bar 97.5-106.4: touches stop 98.0 and T2 target 106.0.
        simulate_open_day(pos, bar(D2, 100.0, 106.4, 97.5, 103.0), CFG)
        stop = events_of(pos, EventType.STOP_HIT)[0]
        assert stop.price == 98.0 and stop.ambiguous_intrabar
        assert not events_of(pos, EventType.T2_TARGET)

    def test_t2_target_fills_at_target(self):
        pos = self._entered()
        pos.day_index = 1
        simulate_open_day(pos, bar(D2, 101.0, 106.2, 100.4, 105.0), CFG)
        t2 = events_of(pos, EventType.T2_TARGET)[0]
        assert t2.price == 106.0 and t2.qty == 37
        assert t2.reason == ExitReason.TARGET_2_HIT.value
        assert not pos.is_open

    def test_stop_touch_exits_at_stop_price(self):
        pos = self._entered()
        pos.day_index = 1
        simulate_open_day(pos, bar(D2, 100.0, 100.6, 97.9, 99.0), CFG)
        stop = events_of(pos, EventType.STOP_HIT)[0]
        assert stop.price == 98.0
        assert not stop.ambiguous_intrabar

    def test_stall_exit_on_day2_at_close(self):
        # Entered 100.5; day-2 close 101.2 < 100.5*1.008 = 101.304 -> stall.
        pos = self._entered(c=100.5)
        pos.day_index = 1
        simulate_open_day(pos, bar(D2, 100.6, 101.3, 100.2, 101.2), CFG)
        stall = events_of(pos, EventType.STALL_EXITED)[0]
        assert stall.price == 101.2
        assert stall.reason == ExitReason.STALL_48H_HIT.value

    def test_stall_passes_when_close_above_threshold(self):
        # Entered 100.5; day-2 close 101.4 > 101.304 -> alive, stop refreshed.
        pos = self._entered(c=100.5)
        pos.day_index = 1
        simulate_open_day(pos, bar(D2, 100.8, 101.6, 100.5, 101.4), CFG)
        assert pos.is_open and pos.day_index == 2

    def test_stall_never_fires_on_day3(self):
        pos = self._entered(c=100.5)
        # Day 2 survives above the stall threshold.
        pos.day_index = 1
        simulate_open_day(pos, bar(D2, 100.8, 101.6, 100.5, 101.4), CFG)
        # Day 3 closes below entry*1.008 -> no stall exit, still open.
        pos.day_index = 2
        simulate_open_day(pos, bar(D3, 101.0, 101.5, 100.0, 100.6), CFG)
        assert pos.is_open
        assert not events_of(pos, EventType.STALL_EXITED)

    def test_day5_hard_time_exit_at_close(self):
        pos = self._entered(c=100.5)
        seq = [(D2, 100.8, 101.6, 100.5, 101.4),   # day 2
               (D3, 101.0, 101.9, 100.7, 101.6),   # day 3
               (D4, 101.2, 102.0, 100.9, 101.8),   # day 4
               (D5, 101.5, 101.9, 101.0, 101.7)]   # day 5
        for i, (d, o, h, l, c) in enumerate(seq, start=2):
            simulate_open_day(pos, bar(d, o, h, l, c), CFG)
        t = events_of(pos, EventType.TIME_EXITED)
        assert len(t) == 1 and t[0].price == 101.7
        assert t[0].reason == ExitReason.TIME_DAY5_HIT.value
        assert not pos.is_open

    def test_t1_unfilled_can_fill_on_continuation_day(self):
        pos = self._entered(o=100.5, h=101.0, l=99.5, c=100.8)
        pos.day_index = 1
        # Day 2: high 102.3 touches T1 target; close above stall floor.
        simulate_open_day(pos, bar(D2, 101.0, 102.3, 100.9, 101.8), CFG)
        t1 = events_of(pos, EventType.T1_TARGET)
        assert t1 and t1[0].price == 102.0
        # Breakeven armed EOD for day 3.
        assert events_of(pos, EventType.BREAKEVEN_ARMED)
        assert pos.pending_stop_raw == 100.5

    def test_stall_uses_actual_entry_price_not_close_t(self):
        # Entered at a 99.0 gap-down: stall floor = 99.0*1.008 = 99.792.
        pos = simulate_entry_day(make_position(),
                                 bar(ENTRY_DAY, 99.0, 99.6, 98.8, 99.3), CFG)
        pos.day_index = 1
        simulate_open_day(pos, bar(D2, 99.4, 99.9, 99.0, 99.7), CFG)
        assert events_of(pos, EventType.STALL_EXITED), \
            "99.7 < 99.792 must stall against the real fill, not Close_T"

    def test_circuit_locked_bar_fills_nothing(self):
        pos = self._entered()
        pos.day_index = 1
        # Band-locked: high == low == open == close.
        simulate_open_day(pos, bar(D2, 101.0, 101.0, 101.0, 101.0), CFG)
        assert pos.is_open
        assert events_of(pos, EventType.CIRCUIT_FROZEN)
        assert pos.day_index == 2


# ---------------------------------------------------------------------------
# 3. Breakeven arming / runner_trail switch
# ---------------------------------------------------------------------------

class TestStopPolicy:
    def test_breakeven_not_armed_without_t1(self):
        pos = simulate_entry_day(make_position(),
                                 bar(ENTRY_DAY, 100.5, 101.0, 99.5, 100.8), CFG)
        assert pos.pending_stop_raw == 98.0  # structural stop stands

    def test_breakeven_protects_only_from_next_session(self):
        # Entry day: T1 fills AND low dips to the structural stop — the T2
        # stop is STILL the structural one for that day (arming is EOD).
        pos = simulate_entry_day(make_position(),
                                 bar(ENTRY_DAY, 100.5, 102.1, 97.9, 101.5), CFG)
        # T1 filled at 102.0 (high touched) but stop 98.0 was NOT touched
        # (low 97.9 > 98.0 would have touched; keep low above).
        pos2 = simulate_entry_day(make_position(),
                                  bar(ENTRY_DAY, 100.5, 102.1, 99.9, 101.5), CFG)
        assert events_of(pos2, EventType.T1_TARGET)
        # Same-day gap-through below breakeven would NOT have been stopped
        # (breakeven wasn't in force yet) — structural stop 98.0 was.
        assert pos2.pending_stop_raw == 100.5

    def test_prev_low_trail_policy(self):
        cfg = SystemConfig(risk={"runner_trail": "prev_low"})
        pos = simulate_entry_day(make_position(),
                                 bar(ENTRY_DAY, 100.5, 101.0, 99.5, 100.8), cfg)
        # EOD of entry day: the trail is live from the first EOD, so the stop
        # tightens to the entry-day low 99.5 (above the structural floor 98.0).
        assert pos.pending_stop_raw == 99.5
        pos.day_index = 1
        # Close 101.5 clears the stall floor (100.5 * 1.008 = 101.304), so the
        # day ends with the stop trailing the D2 low 100.2.
        simulate_open_day(pos, bar(D2, 100.0, 101.6, 100.2, 101.5), cfg)
        assert pos.pending_stop_raw == 100.2  # trails D2 low

    def test_prev_low_trail_never_loosens_below_structural(self):
        cfg = SystemConfig(risk={"runner_trail": "prev_low"})
        pos = simulate_entry_day(make_position(),
                                 bar(ENTRY_DAY, 100.5, 101.0, 99.5, 100.8), cfg)
        assert pos.pending_stop_raw == 99.5  # max(98.0 structural, 99.5 low)


# ---------------------------------------------------------------------------
# 4. Kill switch / force exit
# ---------------------------------------------------------------------------

class TestKillSwitch:
    def test_force_exit_closes_everything_at_market(self):
        pos = simulate_entry_day(make_position(),
                                 bar(ENTRY_DAY, 100.5, 101.0, 99.5, 100.8), CFG)
        force_exit(pos, 97.3, D3)
        kills = events_of(pos, EventType.KILL_SWITCH)
        assert len(kills) == 2  # T1 (unfilled) + T2
        assert all(k.reason == ExitReason.KILL_SWITCH.value for k in kills)
        assert all(k.price == 97.3 for k in kills)
        assert not pos.is_open


# ---------------------------------------------------------------------------
# 5. Mid-trade corporate action
# ---------------------------------------------------------------------------

class TestCorporateAction:
    def _entered(self):
        return simulate_entry_day(make_position(),
                                  bar(ENTRY_DAY, 100.5, 101.0, 99.5, 100.8), CFG)

    def test_bonus_1to1_rescales_levels_and_doubles_qty(self):
        pos = self._entered()
        pos.day_index = 1
        # 1:1 bonus ex on D2: factor B/(A+B) = 1/2. No T1 fill, so the pending
        # stop is still the structural one and absorbs the same factor. Bar
        # high 50.9 deliberately stays under the adjusted T1 target 51.0.
        simulate_open_day(pos, bar(D2, 50.4, 50.9, 50.0, 50.8),
                          CFG, action_factor=0.5)
        assert pos.entry_price_raw == 50.25          # 100.5 * 0.5
        assert pos.structural_stop_raw == 49.0       # 98.0 * 0.5
        assert pos.tranche2_target_raw == 53.0       # 106.0 * 0.5
        assert pos.pending_stop_raw == 49.0          # 98.0 * 0.5
        assert pos.tranche1_qty == 74 and pos.tranche2_qty == 74
        assert events_of(pos, EventType.CORPORATE_ACTION_ADJUSTED)
        assert not pos.t1_filled
        assert pos.is_open, "a bonus must never look like a stop-out"

    def test_bonus_after_t1_fill_adjusts_breakeven_stop(self):
        pos = self._entered()
        # T1 fills on entry day at 102.0; breakeven stop 100.5 armed at EOD.
        pos.day_index = 1
        simulate_open_day(pos, bar(D2, 101.0, 102.3, 100.9, 101.8), CFG)
        assert pos.t1_filled and pos.pending_stop_raw == 100.5
        # 1:1 bonus ex on D3: breakeven stop 100.5 -> 50.25, qty doubles.
        # Bar low 50.4 stays above the adjusted stop 50.25; high 50.9 under
        # the adjusted T2 target 53.0; close 50.8 clears the stall floor.
        pos.day_index = 2
        simulate_open_day(pos, bar(D3, 50.6, 50.9, 50.4, 50.8),
                          CFG, action_factor=0.5)
        assert pos.pending_stop_raw == 50.25
        assert pos.tranche2_qty == 74
        assert pos.is_open

    def test_adjusted_stop_can_then_trigger_normally(self):
        pos = self._entered()
        pos.day_index = 1
        # Bonus ex D2; D2 low 48.9 breaches the adjusted stop 49.0 -> stop.
        simulate_open_day(pos, bar(D2, 50.4, 51.0, 48.9, 49.5),
                          CFG, action_factor=0.5)
        stop = events_of(pos, EventType.STOP_HIT)[0]
        assert stop.price == 49.0 and not pos.is_open

    def test_action_factor_for_date_multiplies_same_day_actions(self):
        actions = pd.DataFrame({
            "ex_date": [D2, D2, D3],
            "adjustment_factor": [0.5, 0.8, 0.9],
        })
        assert action_factor_for_date(actions, D2) == 0.4   # 0.5 * 0.8
        assert action_factor_for_date(actions, D3) == 0.9
        assert action_factor_for_date(actions, D4) is None
        assert action_factor_for_date(pd.DataFrame(), D2) is None
