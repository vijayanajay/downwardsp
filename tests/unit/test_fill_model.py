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

import pytest

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


# CR-2026-001: config default buffer 1.5% -> stop trigger 98.00, limit leg
# 98.0 * 0.985 = 96.53 -> tick-rounded 96.55. Every gap test below is
# hand-computed against it.
STOP_TRIGGER = 98.0
STOP_LIMIT = 96.55


def bar(d, o, h, l, c):
    return {"date": d, "open": o, "high": h, "low": l, "close": c}


def events_of(pos, *types):
    return [e for e in pos.events if e.event_type in types]


class TestExcursionTelemetry:
    """CR-2026-003 MFE/MAE tracking: measurement only, never gates behavior.
    Hand-computed against the canonical make_position trade (fill 100, stop
    98, T1 102, T2 106)."""

    def test_mfe_mae_track_entry_and_open_days_then_exit_print(self):
        pos = make_position()
        # Entry day: fill at 100; high 101.4 -> MFE 1.4%; low 99.7 -> MAE 0.3%.
        simulate_entry_day(pos, bar(ENTRY_DAY, 100.0, 101.4, 99.7, 101.0), CFG)
        assert pos.mfe_pct == pytest.approx(0.014)
        assert pos.mae_pct == pytest.approx(0.003)   # adverse kept POSITIVE
        assert pos.mfe_day == 1 and pos.mae_day == 1
        # Day 2: high 103.4 fills T1 (102) and sets MFE 3.4%; low 98.1 stays
        # a hair above the 98 stop but digs DEEPER than day 1's 0.3% ->
        # MAE renews to 1.9% (it is a running maximum).
        simulate_open_day(pos, bar(D2, 99.5, 103.4, 98.1, 102.5), CFG)
        assert pos.mfe_pct == pytest.approx(0.034)
        assert pos.mae_pct == pytest.approx(0.019)
        assert pos.mfe_day == 2 and pos.mae_day == 2
        # Forced exit ABOVE the running MFE must print (exit is an excursion).
        force_exit(pos, 104.5, D3)
        assert pos.mfe_pct == pytest.approx(0.045)
        assert pos.mfe_day == 2                        # day not updated twice

    def test_gap_rejected_entry_is_never_tracked(self):
        pos = make_position()
        # Open 101.3 > max_entry 101.2 -> rejected, never filled, never tracked.
        simulate_entry_day(pos, bar(ENTRY_DAY, 101.3, 101.5, 100.9, 101.2), CFG)
        assert pos.entry_price_raw is None
        assert pos.mfe_pct == 0.0 and pos.mae_pct == 0.0


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
        # Entered at 100.5, stop 98.0; next day opens 96.0. The open is above
        # the limit leg 96.53? NO — 96.0 < 96.53, but the day's high 97.5
        # recovers through the limit -> fills AT THE LIMIT 96.53 (GTT rule b).
        pos = self._entered()
        pos.day_index = 1
        simulate_open_day(pos, bar(D2, 96.0, 97.5, 95.5, 97.0), CFG)
        stop = events_of(pos, EventType.STOP_HIT)[0]
        assert stop.price == STOP_LIMIT, \
            "open below the limit leg fills on recovery at the limit"
        assert not pos.is_open

    def test_gap_to_open_within_limit_fills_at_open(self):
        # Open 96.8 >= limit leg 96.53 -> the sell limit crosses immediately:
        # fill at the open 96.8 (GTT rule a), never at the trigger.
        pos = self._entered()
        pos.day_index = 1
        simulate_open_day(pos, bar(D2, 96.8, 97.5, 96.2, 97.0), CFG)
        stop = events_of(pos, EventType.STOP_HIT)[0]
        assert stop.price == 96.8
        assert not pos.is_open

    def test_gap_beyond_limit_leg_survives_then_stall_exits(self):
        # Open 95.0 is below the limit leg 96.53 AND the day's high 96.4 never
        # reaches it -> GTT_STOP_UNFILLED: the position survives the session
        # (GTT rule c). Day-2 close 95.6 < stall floor 101.304 -> the stall
        # rule exits at the close the same day.
        pos = self._entered()
        pos.day_index = 1
        simulate_open_day(pos, bar(D2, 95.0, 96.4, 94.8, 95.6), CFG)
        assert events_of(pos, EventType.GTT_STOP_UNFILLED)
        stall = events_of(pos, EventType.STALL_EXITED)[0]
        assert stall.price == 95.6
        assert not pos.is_open

    def test_gap_beyond_limit_leg_survives_to_next_session_stop(self):
        # Unfilled day 2 that clears the stall floor, then day 3 opens back
        # above the limit leg -> the still-armed stop fills at that open.
        pos = self._entered()
        pos.day_index = 1
        simulate_open_day(pos, bar(D2, 95.0, 96.4, 94.8, 101.5), CFG)
        assert events_of(pos, EventType.GTT_STOP_UNFILLED) and pos.is_open
        pos.day_index = 2
        simulate_open_day(pos, bar(D3, 97.2, 99.0, 96.9, 98.5), CFG)
        stop = events_of(pos, EventType.STOP_HIT)[0]
        assert stop.price == 97.2  # open 97.2 >= limit 96.53: fill at open

    def test_recorded_stop_limit_overrides_config_buffer(self):
        # A recorded GTT limit (from the CandidateSignal) is authoritative.
        pos = simulate_entry_day(
            make_position(stop_limit_raw=95.0),
            bar(ENTRY_DAY, 100.5, 101.0, 99.5, 100.8), CFG)
        pos.day_index = 1
        # Open 96.0: below trigger 98.0, above recorded limit 95.0 -> open fill.
        simulate_open_day(pos, bar(D2, 96.0, 97.0, 95.2, 96.5), CFG)
        assert events_of(pos, EventType.STOP_HIT)[0].price == 96.0

    def test_breakeven_gap_uses_relative_limit_leg(self):
        # After a T1 fill the standing stop is the breakeven 100.5; the limit
        # leg moves WITH it: 100.5 * 0.985 = 98.9925 -> tick 99.00.
        pos = simulate_entry_day(make_position(),
                                 bar(ENTRY_DAY, 100.5, 102.1, 99.9, 101.5), CFG)
        assert pos.t1_filled and pos.pending_stop_raw == 100.5
        pos.day_index = 1
        # Day 2 opens 98.9 < 99.00; high 99.2 recovers -> fill at the limit 99.0.
        simulate_open_day(pos, bar(D2, 98.9, 99.2, 98.5, 99.0), CFG)
        stop = events_of(pos, EventType.STOP_HIT)[0]
        assert stop.price == 99.0
        assert stop.reason == ExitReason.TRAILING_STOP_HIT.value

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


class TestGttStopLimit:
    """CR-2026-001 Issue 4: the GTT stop is trigger + buffered limit."""

    def test_entry_day_open_below_stop_fills_at_open_when_above_limit(self):
        # Entry open 97.5 < trigger 98.0 but >= limit 96.53 -> immediate
        # scratch at the open (unchanged from the pre-CR behavior).
        pos = simulate_entry_day(make_position(),
                                 bar(ENTRY_DAY, 97.5, 98.2, 97.0, 98.0), CFG)
        stops = events_of(pos, EventType.STOP_HIT)
        assert len(stops) == 2 and all(s.price == 97.5 for s in stops)

    def test_entry_day_open_below_limit_survives_entry_session(self):
        # Extreme case: entry open 96.0 < limit 96.53 and high never reaches
        # it -> the stop cannot execute on day 1; the position is carried.
        pos = simulate_entry_day(make_position(),
                                 bar(ENTRY_DAY, 96.0, 96.4, 95.8, 96.3), CFG)
        assert events_of(pos, EventType.ENTRY_FILLED)
        assert events_of(pos, EventType.GTT_STOP_UNFILLED)
        assert pos.is_open


# ---------------------------------------------------------------------------
# 6. CR-2026-003 X1: t1_enabled / time_stop_enabled exit geometry
# ---------------------------------------------------------------------------

class TestX1ExitGeometry:
    """X1 fill-model knobs (risk.t1_enabled / risk.time_stop_enabled).
    t1_enabled=False: one full-size position — no T1 fill, no breakeven arm,
    a T2 touch exits everything, the structural stop is the only stop.
    time_stop_enabled=False: the Day-2 stall and Day-5 time exits vanish;
    positions live only by stop/target (max_holding_days stays armed).
    Defaults (True/True) reproduce the golden tests above byte-for-byte."""

    @staticmethod
    def _x1_cfg(**kw) -> SystemConfig:
        return SystemConfig(risk={"t1_enabled": False,
                                  "time_stop_enabled": False, **kw})

    def test_defaults_reproduce_legacy_geometry(self):
        # The X1 contract: default config must be indistinguishable pre-X1.
        pos = simulate_entry_day(make_position(),
                                 bar(ENTRY_DAY, 100.5, 102.1, 99.9, 101.5), CFG)
        assert events_of(pos, EventType.T1_TARGET)
        assert pos.pending_stop_raw == 100.5   # breakeven armed as ever

    def test_no_t1_fill_no_breakeven_arm(self):
        pos = simulate_entry_day(make_position(),
                                 bar(ENTRY_DAY, 100.5, 102.1, 99.9, 101.5),
                                 self._x1_cfg())
        assert not events_of(pos, EventType.T1_TARGET)
        assert not events_of(pos, EventType.BREAKEVEN_ARMED)
        assert pos.pending_stop_raw == 98.0    # structural stop stands
        assert pos.is_open

    def test_t2_touch_exits_full_position_at_target(self):
        # Built as the engine builds it under X1: the whole slot in tranche 2
        # (tranche1_qty=0), so a T2 touch books the full 74 shares.
        pos = simulate_entry_day(
            make_position(tranche1_qty=0, tranche2_qty=74),
            bar(ENTRY_DAY, 100.5, 101.0, 99.5, 100.8), self._x1_cfg())
        pos.day_index = 1
        simulate_open_day(pos, bar(D2, 101.0, 106.2, 100.4, 105.0),
                          self._x1_cfg())
        t2 = events_of(pos, EventType.T2_TARGET)[0]
        assert t2.price == 106.0
        assert t2.qty == 74, "the whole position exits, not a 37-share runner"
        assert not events_of(pos, EventType.T1_TARGET)
        assert not pos.is_open

    def test_move_short_of_t2_keeps_position_open(self):
        pos = simulate_entry_day(make_position(),
                                 bar(ENTRY_DAY, 100.5, 101.0, 99.5, 100.8),
                                 self._x1_cfg())
        pos.day_index = 1
        # High 104.0: no tranche fills (t1 gone) and 104 < T2 106 -> ride on.
        simulate_open_day(pos, bar(D2, 101.0, 104.0, 100.4, 103.5),
                          self._x1_cfg())
        assert pos.is_open and not events_of(pos, EventType.T2_TARGET)

    def test_no_stall_no_day5_exit(self):
        pos = simulate_entry_day(make_position(),
                                 bar(ENTRY_DAY, 100.5, 101.0, 99.5, 100.8),
                                 self._x1_cfg())
        seq = [(D2, 100.0, 100.6, 99.8, 99.0),    # day 2: below stall floor
               (D3, 99.2, 101.2, 99.0, 101.0),
               (D4, 100.8, 101.6, 100.5, 101.4),
               (D5, 101.2, 101.9, 101.0, 101.7)]  # day 5: legacy time exit
        for d, o, h, l, c in seq:
            simulate_open_day(pos, bar(d, o, h, l, c), self._x1_cfg())
        assert pos.is_open
        assert not events_of(pos, EventType.STALL_EXITED)
        assert not events_of(pos, EventType.TIME_EXITED)

    def test_stall_still_fires_when_only_t1_disabled(self):
        # Flag independence: t1_enabled=False alone leaves the time-stop ladder
        # armed — the Day-2 stall still exits at the close.
        cfg = self._x1_cfg(time_stop_enabled=True)
        pos = simulate_entry_day(make_position(),
                                 bar(ENTRY_DAY, 100.5, 101.0, 99.5, 100.8), cfg)
        pos.day_index = 1
        simulate_open_day(pos, bar(D2, 100.0, 100.6, 99.8, 99.0), cfg)
        assert events_of(pos, EventType.STALL_EXITED)
        assert not pos.is_open

    def test_stop_path_unchanged_books_both_tranches(self):
        # make_position's 37/37 split: the structural stop still exits the
        # whole book through the legacy _exit_all path (X1 touches only T1
        # fills and time exits, never stops).
        pos = simulate_entry_day(make_position(),
                                 bar(ENTRY_DAY, 100.5, 101.0, 99.5, 100.8),
                                 self._x1_cfg())
        pos.day_index = 1
        simulate_open_day(pos, bar(D2, 100.0, 100.6, 97.9, 99.0),
                          self._x1_cfg())
        stops = events_of(pos, EventType.STOP_HIT)
        assert [s.qty for s in stops] == [37, 37]
        assert not pos.is_open

    def test_stop_and_t2_same_bar_stop_wins(self):
        pos = simulate_entry_day(make_position(),
                                 bar(ENTRY_DAY, 100.5, 101.0, 99.5, 100.8),
                                 self._x1_cfg())
        pos.day_index = 1
        simulate_open_day(pos, bar(D2, 100.0, 106.4, 97.5, 103.0),
                          self._x1_cfg())
        stop = events_of(pos, EventType.STOP_HIT)[0]
        assert stop.price == 98.0 and stop.ambiguous_intrabar
        assert not events_of(pos, EventType.T2_TARGET)
