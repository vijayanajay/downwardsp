"""Golden tests for the event-sourced production ledger (Phase 7.1).

Facts, hand-computed with SystemConfig():
  buy_rate  = .001 + .00015 + .0000345 + .000001 + .0005
              + (.0000345 + .000001) * .18 = 0.00169189
  sell_rate = .001 + .0000345 + .000001 + .0005
              + (.0000345 + .000001) * .18 = 0.00154189
  DP charge ₹15.93 once per (trade, sell day).

The fold must agree with fill_model semantics: no double fills, no exits
before entry, breakeven arming on T1 fill, whole-position exits.
"""

from __future__ import annotations

from datetime import date

import pytest

from nse_cash.backtest.fill_model import EventType
from nse_cash.core.config import SystemConfig
from nse_cash.execution.ledger import Ledger, round_to_tick

CFG = SystemConfig()
F = CFG.friction
BUY_RATE = (F.stt_delivery + F.stamp_duty + F.nse_turnover + F.sebi_fee
            + F.slippage_per_side
            + (F.nse_turnover + F.sebi_fee) * F.gst_rate)
SELL_RATE = (F.stt_delivery + F.nse_turnover + F.sebi_fee
             + F.slippage_per_side
             + (F.nse_turnover + F.sebi_fee) * F.gst_rate)

D0 = date(2026, 9, 1)   # signal day
D1 = date(2026, 9, 2)   # entry day
D2 = date(2026, 9, 3)


def _trade_kwargs(symbol="SHOCK", slot=1, sector="IT", entry_ref=100.0,
                  stop=98.0) -> dict:
    return dict(
        trade_id=f"{symbol}-{D0.isoformat()}", symbol=symbol,
        setup_id="SETUP_1_VCP", signal_date=D0, slot=slot, sector=sector,
        entry_ref=entry_ref, structural_stop=stop,
        tranche1_target=entry_ref * 1.02, tranche2_target=entry_ref * 1.06,
        max_gap_pct=CFG.risk.max_gap_entry, tranche1_qty=623, tranche2_qty=624)


def _filled_ledger(tmp_path, **kw) -> Ledger:
    led = Ledger(tmp_path / "ledger.sqlite3")
    led.add_trade(**_trade_kwargs(**kw))
    led.record_fill(_trade_kwargs(**kw)["trade_id"], 100.0, D1)
    return led


class TestTick:
    def test_round_to_tick(self):
        assert round_to_tick(998.7116) == pytest.approx(998.70)
        assert round_to_tick(998.73) == pytest.approx(998.75)
        assert round_to_tick(100.0) == 100.0


class TestReceipts:
    def test_fill_then_exit_roundtrip(self, tmp_path):
        led = _filled_ledger(tmp_path)
        led.record_exit(led.get_trade("SHOCK-2026-09-01")["trade_id"],
                        102.0, D2, "T1_TARGET")
        pos = led.position(led.get_trade("SHOCK-2026-09-01"))
        assert pos.t1_filled and pos.is_open
        led.record_exit("SHOCK-2026-09-01", 106.0, D2, "T2_TARGET")
        pos = led.position(led.get_trade("SHOCK-2026-09-01"))
        assert not pos.is_open and pos.exit_reason == "TARGET_2_HIT"
        assert pos.exit_date == D2
        led.close()

    def test_double_fill_rejected(self, tmp_path):
        led = _filled_ledger(tmp_path)
        with pytest.raises(ValueError, match="already"):
            led.record_fill("SHOCK-2026-09-01", 100.5, D1)
        led.close()

    def test_exit_before_fill_rejected(self, tmp_path):
        led = Ledger(tmp_path / "l.sqlite3")
        led.add_trade(**_trade_kwargs())
        with pytest.raises(ValueError, match="first"):
            led.record_exit("SHOCK-2026-09-01", 102.0, D1, "T1_TARGET")
        led.close()

    def test_unknown_reason_rejected(self, tmp_path):
        led = _filled_ledger(tmp_path)
        with pytest.raises(ValueError, match="reason"):
            led.record_exit("SHOCK-2026-09-01", 102.0, D1, "FAKE_REASON")
        led.close()

    def test_double_t1_rejected(self, tmp_path):
        led = _filled_ledger(tmp_path)
        led.record_exit("SHOCK-2026-09-01", 102.0, D1, "T1_TARGET")
        with pytest.raises(ValueError, match="T1 already"):
            led.record_exit("SHOCK-2026-09-01", 102.5, D1, "T1_TARGET")
        led.close()

    def test_exit_after_close_rejected(self, tmp_path):
        led = _filled_ledger(tmp_path)
        led.record_exit("SHOCK-2026-09-01", 96.0, D2, "STOP_HIT")
        with pytest.raises(ValueError, match="already closed"):
            led.record_exit("SHOCK-2026-09-01", 96.5, D2, "STOP_HIT")
        led.close()


class TestFold:
    def test_full_position_exit_when_t1_unfilled(self, tmp_path):
        """A stop-out with T1 unfilled closes the WHOLE position (fold splits
        the event into a T1 + T2 exit, mirroring fill_model._exit_all)."""
        led = _filled_ledger(tmp_path)
        led.record_exit("SHOCK-2026-09-01", 96.0, D2, "STOP_HIT")
        pos = led.position(led.get_trade("SHOCK-2026-09-01"))
        assert not pos.is_open
        exits = [e for e in pos.events if e.event_type is EventType.STOP_HIT]
        assert len(exits) == 2 and {e.tranche for e in exits} == {1, 2}
        led.close()

    def test_breakeven_arming(self, tmp_path):
        led = _filled_ledger(tmp_path)
        tid = "SHOCK-2026-09-01"
        led.append_event(tid, EventType.BREAKEVEN_ARMED, D2, 0, price=100.0)
        pos = led.position(led.get_trade(tid))
        assert pos.pending_stop_raw == pos.breakeven_raw == 100.0
        led.close()

    def test_exit_before_entry_is_a_corrupt_ledger(self, tmp_path):
        led = Ledger(tmp_path / "l.sqlite3")
        led.add_trade(**_trade_kwargs())
        led.append_event("SHOCK-2026-09-01", EventType.STOP_HIT, D1, 2,
                         price=96.0, reason="STRUCTURAL_STOP_HIT")
        with pytest.raises(ValueError, match="exit before entry"):
            led.position(led.get_trade("SHOCK-2026-09-01"))
        led.close()

    def test_double_entry_is_a_corrupt_ledger(self, tmp_path):
        led = Ledger(tmp_path / "l.sqlite3")
        led.add_trade(**_trade_kwargs())
        led.append_event("SHOCK-2026-09-01", EventType.ENTRY_FILLED, D1, 0,
                         price=100.0, qty=1247)
        led.append_event("SHOCK-2026-09-01", EventType.ENTRY_FILLED, D1, 0,
                         price=101.0, qty=1247)
        with pytest.raises(ValueError, match="double ENTRY_FILLED"):
            led.position(led.get_trade("SHOCK-2026-09-01"))
        led.close()


class TestMoney:
    def test_cash_snapshot_matches_engine_math(self, tmp_path):
        """Open trade at 100: cash = 500000 - 1247*100*(1+BUY_RATE)."""
        led = _filled_ledger(tmp_path)
        snap = led.cash_snapshot(CFG)
        # First call stamps last_interest_date = today with no prior date:
        # zero interest accrues, so the number is pure settlement.
        assert snap["invested"] == pytest.approx(1247 * 100.0 * (1 + BUY_RATE))
        assert snap["proceeds"] == 0.0
        assert snap["cash"] == pytest.approx(
            CFG.capital.base_capital - 1247 * 100.0 * (1 + BUY_RATE))
        led.close()

    def test_cash_after_t1_target_same_day(self, tmp_path):
        led = _filled_ledger(tmp_path)
        led.record_exit("SHOCK-2026-09-01", 102.0, D1, "T1_TARGET")
        snap = led.cash_snapshot(CFG)
        t1_value = 623 * 102.0
        dp = F.dp_charge_per_sell  # a T1 sell day is a sell day
        expected_proceeds = t1_value * (1 - SELL_RATE) - dp
        assert snap["proceeds"] == pytest.approx(expected_proceeds)
        assert snap["cash"] == pytest.approx(
            CFG.capital.base_capital - 1247 * 100.0 * (1 + BUY_RATE)
            + expected_proceeds)
        led.close()

    def test_closed_trade_pnl_and_stcg(self, tmp_path):
        led = _filled_ledger(tmp_path)
        led.record_exit("SHOCK-2026-09-01", 102.0, D2, "T1_TARGET")
        led.record_exit("SHOCK-2026-09-01", 106.0, D2, "T2_TARGET")
        snap = led.cash_snapshot(CFG)
        # Same sell day for both tranches -> exactly one DP charge.
        t1 = 623 * 102.0 * (1 - SELL_RATE)
        t2 = 624 * 106.0 * (1 - SELL_RATE)
        expected = t1 + t2 - F.dp_charge_per_sell
        assert snap["proceeds"] == pytest.approx(expected)
        realized = expected - 1247 * 100.0 * (1 + BUY_RATE)
        assert snap["realized_pnl"] == pytest.approx(realized)
        assert snap["tax_paid"] == pytest.approx(max(0.0, realized) * F.stcg_tax_rate)
        led.close()

    def test_stcg_ignores_open_trade_t1_partial(self, tmp_path):
        """A T1 fill is a partial exit, not a taxable close: realized P&L and
        tax stay zero until the trade fully closes (engine parity)."""
        led = _filled_ledger(tmp_path)
        led.record_exit("SHOCK-2026-09-01", 102.0, D1, "T1_TARGET")
        snap = led.cash_snapshot(CFG)
        assert snap["realized_pnl"] == 0.0
        assert snap["tax_paid"] == 0.0
        assert snap["proceeds"] > 0            # money did move
        led.close()

    def test_two_trades_one_sell_day_each(self, tmp_path):
        """DP charge is per (trade, day): two trades sold same day pay 2×15.93."""
        led = Ledger(tmp_path / "l.sqlite3")
        for sym, slot in (("AAA", 1), ("BBB", 2)):
            led.add_trade(**_trade_kwargs(symbol=sym, slot=slot, sector=None))
            led.record_fill(f"{sym}-{D0.isoformat()}", 100.0, D1)
            led.record_exit(f"{sym}-{D0.isoformat()}", 102.0, D2, "T1_TARGET")
        snap = led.cash_snapshot(CFG)
        per_trade_proceeds = 1247 // 2 * 102.0 * (1 - SELL_RATE) - F.dp_charge_per_sell
        # AAA: 623 shares, BBB: 624 (623+624 split = 623 T1 / 624 T2 per trade;
        # both trades sized identically so T1 qty is 623 for each).
        assert snap["proceeds"] == pytest.approx(2 * per_trade_proceeds)
        led.close()


class TestBookkeeping:
    def test_slots_and_sectors(self, tmp_path):
        led = Ledger(tmp_path / "l.sqlite3")
        led.add_trade(**_trade_kwargs())
        assert led.occupied_slots() == 0          # signal, not yet filled
        assert led.active_sectors() == set()
        led.record_fill("SHOCK-2026-09-01", 100.0, D1)
        assert led.occupied_slots() == 1
        assert led.active_sectors() == {"IT"}
        assert led.free_slots(CFG) == {2, 3, 4}
        assert led.next_slot(CFG) == 2
        led.close()

    def test_pending_signals_hold_slots(self, tmp_path):
        led = Ledger(tmp_path / "l.sqlite3")
        led.add_trade(**_trade_kwargs())
        assert led.pending_signals() and len(led.pending_signals()) == 1
        led.record_fill("SHOCK-2026-09-01", 100.0, D1)
        assert led.pending_signals() == []
        led.close()

    def test_gap_rejection_tombstone(self, tmp_path):
        led = Ledger(tmp_path / "l.sqlite3")
        led.add_trade(**_trade_kwargs())
        led.append_event("SHOCK-2026-09-01", "ENTRY_REJECTED_GAP", D1, 0)
        led.delete_trade("SHOCK-2026-09-01", "gap")
        assert led.get_trade("SHOCK-2026-09-01") is None
        assert led.open_trades() == [] and led.pending_signals() == []
        led.close()

    def test_cooldown_blocker(self, tmp_path):
        led = Ledger(tmp_path / "l.sqlite3")
        assert led.kill_blocker() is None
        led.arm_cooldown(date(2026, 1, 1))
        assert led.kill_blocker() is None          # expired
        led.arm_cooldown(date(2099, 1, 1))
        assert led.kill_blocker() is not None      # active
        led.close()

    def test_integrity_catches_missing_breakeven(self, tmp_path):
        led = _filled_ledger(tmp_path)
        led.record_exit("SHOCK-2026-09-01", 102.0, D2, "T1_TARGET")
        # T1 filled but no breakeven event and no movement: must flag.
        problems = led.integrity_problems(CFG)
        assert any("not at breakeven" in p for p in problems)
        led.close()


class TestReconcile:
    def test_model_replay_flags_unclosed_trade(self, tmp_path):
        """Ledger says open; bars stop the trade out -> DRIFT must fire."""
        from nse_cash.execution.ledger import reconcile

        class FakeStore:
            class con:  # noqa: N801 - mimic DuckDB connection
                @staticmethod
                def execute(sql, *params):
                    class R:
                        def df(self):
                            import pandas as pd
                            return pd.DataFrame()
                        def fetchall(self):
                            if "corporate_actions" in sql:
                                return []
                            return [
                                ("2026-09-02", 100.0, 100.4, 99.6, 100.2),
                                ("2026-09-03", 97.0, 97.5, 96.5, 96.8),
                            ]
                    return R()

        led = _filled_ledger(tmp_path)
        drift = reconcile(FakeStore(), CFG, led)
        assert drift and "model exited on 2026-09-03" in drift[0]
        led.close()

    def test_no_drift_when_book_matches(self, tmp_path):
        from nse_cash.execution.ledger import reconcile

        class FakeStore:
            class con:  # noqa: N801
                @staticmethod
                def execute(sql, *params):
                    class R:
                        def df(self):
                            import pandas as pd
                            return pd.DataFrame()
                        def fetchall(self):
                            if "corporate_actions" in sql:
                                return []
                            return [("2026-09-02", 100.0, 100.4, 99.6, 100.2)]
                    return R()

        led = _filled_ledger(tmp_path)
        assert reconcile(FakeStore(), CFG, led) == []
        led.close()
