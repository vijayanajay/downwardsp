"""Extra coverage for the execution presentation + receipt layer.

action_sheet.py renders from a synthetic DecisionResult; ledger_cmd receipt
functions run against a real Ledger + SystemConfig with sandboxed paths.
Every hand-checked number mirrors test_ledger.py's friction constants.
"""

from __future__ import annotations

import io
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
import rich.console

from nse_cash.core.config import SystemConfig
from nse_cash.core.types import CandidateSignal, SetupID
from nse_cash.execution.action_sheet import (
    _render_playbook, render_action_sheet, render_position_book,
)
from nse_cash.execution.ledger import Ledger, round_to_tick

CFG = SystemConfig()
D0 = date(2026, 9, 1)   # signal day
D1 = date(2026, 9, 2)   # entry day


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------

def _cand(symbol="SHOCK", entry_ref=100.0, stop=97.5, stop_trigger=0.0,
          stop_limit=0.0, s_runner=1.2):
    return CandidateSignal(
        symbol=symbol, date=D0, setup=SetupID.SETUP_1_VCP,
        entry_ref=entry_ref, structural_stop=stop,
        structural_stop_pct=(entry_ref - stop) / entry_ref,
        stop_trigger=stop_trigger, stop_limit=stop_limit,
        s_runner=s_runner)


def _decision(sector="Information Technology"):
    from nse_cash.funnel.stage4_gate import Stage4Decision
    return Stage4Decision(symbol="SHOCK", is_accepted=True,
                          structural_risk_pct=0.018, sector=sector)


def _result(cands=None, occupied=0, rejected=None):
    """A DecisionResult-shaped namespace (duck-typed like production use)."""
    cands = cands if cands is not None else [(_cand(), _decision())]
    return SimpleNamespace(candidates=[c for c, _ in cands],
                           accepted=cands, rejected=rejected or [],
                           occupied_slots=occupied)


@pytest.fixture()
def sheet_out(monkeypatch):
    """Wide, non-terminal console in place of each rendering module's console;
    returns the buffer so assertions see untruncated numbers."""
    buf = io.StringIO()
    quiet = rich.console.Console(file=buf, force_terminal=False, width=220)
    import nse_cash.cli.ledger_cmd as ledger_cmd_mod
    import nse_cash.execution.action_sheet as sheet_mod
    monkeypatch.setattr(sheet_mod, "console", quiet)
    monkeypatch.setattr(ledger_cmd_mod, "console", quiet)
    return buf


# ---------------------------------------------------------------------------
# Action sheet
# ---------------------------------------------------------------------------

class TestActionSheet:
    def test_header_and_candidate_table_render(self, sheet_out):
        render_action_sheet(CFG, _result(), D0, slot_capital=125_000.0)
        out = sheet_out.getvalue()
        assert "ACTION SHEET" in out
        assert "Portfolio Capital" in out
        assert "Open Slots:" in out and "4 of 4 Available" in out
        assert "SHOCK" in out
        assert "S1 Vcp" in out                      # setup label transformation
        assert "Information Technology" in out      # sector from decision
        # T1 target: 100 * 1.02 -> tick 102.00; T2: 106.00; max entry: 101.20
        assert "102.00" in out and "106.00" in out and "101.20" in out

    def test_quantity_split_for_gtt_pair(self, sheet_out):
        """Qty column shows (T1/T2) halves; T1 gets floor(half), T2 the rest."""
        qty = 623
        from nse_cash.backtest.engine import slot_quantity
        assert slot_quantity(CFG, 200.0) == qty   # 125k net of buy friction at 200
        render_action_sheet(CFG, _result([(_cand(entry_ref=200.0), _decision())]), D0)
        assert f"{qty} ({qty // 2}/{qty - qty // 2})" in sheet_out.getvalue()

    def test_gtt_stop_uses_candidate_levels_when_present(self, sheet_out):
        trig, lim = 96.0, 94.55
        render_action_sheet(CFG, _result([(_cand(stop_trigger=trig, stop_limit=lim),
                                           _decision())]), D0)
        out = sheet_out.getvalue()
        assert "96.00" in out and "94.55" in out

    def test_stop_falls_back_to_structural_and_buffer(self, sheet_out):
        render_action_sheet(CFG, _result([(_cand(stop=97.5, stop_trigger=0.0,
                                                 stop_limit=0.0), _decision())]), D0)
        out = sheet_out.getvalue()
        assert "97.50" in out                                   # trigger
        expected_lim = round_to_tick(97.5 * (1 - CFG.risk.gtt_stop_limit_buffer))
        assert f"{expected_lim:,.2f}" in out                    # limit leg

    def test_no_candidates_still_renders_playbook(self, sheet_out):
        render_action_sheet(CFG, _result(cands=[]), D0)
        out = sheet_out.getvalue()
        assert "No setup matched" in out
        assert "Dual-GTT Playbook" in out

    def test_rejections_are_listed_capped_at_six(self, sheet_out):
        rejected = [(_cand(symbol=f"S{i:02d}"), "capacity full") for i in range(8)]
        render_action_sheet(CFG, _result(rejected=rejected), D0)
        out = sheet_out.getvalue()
        assert "rejected S00" in out and "rejected S05" in out
        assert "rejected S06" not in out
        assert "2 more rejections" in out

    def test_unknown_sector_sentinel_when_decision_sector_missing(self, sheet_out):
        from nse_cash.funnel.sector_gate import UNKNOWN_SECTOR
        render_action_sheet(CFG, _result([(_cand(), _decision(sector=None))]), D0)
        assert UNKNOWN_SECTOR in sheet_out.getvalue()


class TestPlaybook:
    def test_playbook_mentions_both_gtts_and_limit_rule(self, sheet_out):
        _render_playbook(CFG.risk.gtt_stop_limit_buffer)
        out = sheet_out.getvalue()
        assert "GTT 1 (T1)" in out and "GTT 2 (T2)" in out
        assert "limit" in out.lower()


# ---------------------------------------------------------------------------
# Position book
# ---------------------------------------------------------------------------

def _add_trade(led: Ledger, symbol: str, slot: int = 1, sector="IT",
               trade_id: str | None = None, **over):
    kwargs = dict(
        trade_id=trade_id or f"{symbol}-{D0.isoformat()}", symbol=symbol,
        setup_id="SETUP_1_VCP", signal_date=D0, slot=slot, sector=sector,
        entry_ref=100.0, structural_stop=97.5,
        tranche1_target=102.0, tranche2_target=106.0,
        max_gap_pct=CFG.risk.max_gap_entry, tranche1_qty=623,
        tranche2_qty=624, stop_limit=None)
    kwargs.update(over)
    led.add_trade(**kwargs)
    return kwargs


class TestPositionBook:
    def test_empty_book_renders_header_only(self, tmp_path, sheet_out):
        led = Ledger(tmp_path / "empty.sqlite3")
        try:
            render_position_book(CFG, led)
        finally:
            led.close()
        out = sheet_out.getvalue()
        assert "Portfolio Ledger" in out
        assert "No open positions." in out

    def test_open_position_shows_gtt_levels(self, tmp_path, sheet_out):
        led = Ledger(tmp_path / "book.sqlite3")
        _add_trade(led, "SHOCK")
        led.record_fill("SHOCK-2026-09-01", 100.0, D1)
        try:
            render_position_book(CFG, led)
        finally:
            led.close()
        out = sheet_out.getvalue()
        assert "Open Positions — GTT Levels" in out
        assert "SHOCK" in out
        assert "102.00" in out and "106.00" in out
        # stop limit = structural * (1 - buffer), tick-rounded
        lim = round_to_tick(97.5 * (1 - CFG.risk.gtt_stop_limit_buffer))
        assert f"{lim:,.2f}" in out

    def test_snapshot_numbers_flow_into_header(self, tmp_path, sheet_out):
        led = Ledger(tmp_path / "book.sqlite3")
        _add_trade(led, "SHOCK")
        led.record_fill("SHOCK-2026-09-01", 100.0, D1)
        try:
            render_position_book(CFG, led)
        finally:
            led.close()
        led2 = Ledger(tmp_path / "book.sqlite3")
        try:
            snap = led2.cash_snapshot(CFG)
        finally:
            led2.close()
        assert f"{snap['base_capital']:,.2f}" in sheet_out.getvalue()
        assert f"{snap['invested']:,.2f}" in sheet_out.getvalue()


# ---------------------------------------------------------------------------
# ledger_cmd receipts (real Ledger + sandboxed config)
# ---------------------------------------------------------------------------

@pytest.fixture()
def sandbox_config(tmp_path):
    return SystemConfig(paths={
        "duckdb_path": tmp_path / "market.duckdb",
        "raw_dir": tmp_path / "raw",
        "parquet_dir": tmp_path / "proc",
        "ledger_db_path": tmp_path / "ledger.sqlite3",
    })


class _Ctx:
    def __init__(self, config):
        self.obj = {"config": config}


class TestReceipts:
    def test_record_fill_happy_path(self, sandbox_config, sheet_out):
        from nse_cash.cli.ledger_cmd import run_record_fill
        led = Ledger(sandbox_config.paths.ledger_db_path)
        _add_trade(led, "NEW", setup_id="SETUP_2_RUBBERBAND",
                   tranche1_qty=600, tranche2_qty=600)
        led.close()

        run_record_fill(_Ctx(sandbox_config), "NEW", 100.5, D1)
        out = sheet_out.getvalue()
        assert "fill recorded" in out
        assert "GTT OCO" in out  # the sheet's stop levels are echoed
        led = Ledger(sandbox_config.paths.ledger_db_path)
        try:
            assert led.get_trade("NEW-2026-09-01") is not None
            assert led.pending_signals() == []       # consumed by the fill
            assert len(led.open_trades()) == 1
        finally:
            led.close()

    def test_record_fill_without_pending_signal_exits(self, sandbox_config):
        from nse_cash.cli.ledger_cmd import run_record_fill
        with pytest.raises(SystemExit, match="no pending signal"):
            run_record_fill(_Ctx(sandbox_config), "GHOST", 100.0, D1)

    def test_record_fill_ambiguous_symbol_exits(self, sandbox_config):
        from nse_cash.cli.ledger_cmd import run_record_fill
        led = Ledger(sandbox_config.paths.ledger_db_path)
        for slot in (1, 2):
            _add_trade(led, "AMB", slot=slot,
                       trade_id=f"AMB-{D0.isoformat()}-{slot}",
                       setup_id="SETUP_2_RUBBERBAND",
                       tranche1_qty=600, tranche2_qty=600)
        led.close()
        with pytest.raises(SystemExit, match="ambiguous"):
            run_record_fill(_Ctx(sandbox_config), "AMB", 100.0, D1)

    def test_record_exit_closes_position_and_prints_slot_release(
            self, sandbox_config, sheet_out):
        from nse_cash.cli.ledger_cmd import run_record_exit
        led = Ledger(sandbox_config.paths.ledger_db_path)
        _add_trade(led, "EXIT", tranche1_qty=600, tranche2_qty=600)
        led.record_fill("EXIT-2026-09-01", 100.0, D1)
        led.close()

        # run_record_exit(ctx, symbol, price, reason, date_opt) — reason uppercased inside.
        run_record_exit(_Ctx(sandbox_config), "EXIT", 96.0, "stop_hit", D1)
        out = sheet_out.getvalue()
        assert "closed at" in out and "Slot 1 freed" in out

    def test_record_exit_t1_filled_prompts_breakeven(self, sandbox_config,
                                                     sheet_out):
        from nse_cash.cli.ledger_cmd import run_record_exit
        led = Ledger(sandbox_config.paths.ledger_db_path)
        _add_trade(led, "BRE", tranche1_qty=600, tranche2_qty=600)
        led.record_fill("BRE-2026-09-01", 100.0, D1)
        led.close()

        run_record_exit(_Ctx(sandbox_config), "BRE", 102.0, "T1_TARGET", D1)
        out = sheet_out.getvalue()
        assert "T1 filled" in out and "breakeven" in out

    def test_record_exit_unknown_symbol_exits(self, sandbox_config):
        from nse_cash.cli.ledger_cmd import run_record_exit
        with pytest.raises(SystemExit, match="no open trade"):
            run_record_exit(_Ctx(sandbox_config), "GHOST", 100.0, "STOP_HIT", D1)

    def test_record_gap_rejected_releases_signal(self, sandbox_config, sheet_out):
        from nse_cash.cli.ledger_cmd import run_record_gap_rejected
        led = Ledger(sandbox_config.paths.ledger_db_path)
        _add_trade(led, "GAP", sector=None, tranche1_qty=600, tranche2_qty=600)
        led.close()

        # The CLI uppercases the symbol arg; pass it through the same way.
        run_record_gap_rejected(_Ctx(sandbox_config), "GAP")
        assert "released" in sheet_out.getvalue()
        led = Ledger(sandbox_config.paths.ledger_db_path)
        try:
            assert led.get_trade("GAP-2026-09-01") is None
            assert led.pending_signals() == []
        finally:
            led.close()

    def test_record_gap_rejected_unknown_symbol_exits(self, sandbox_config):
        from nse_cash.cli.ledger_cmd import run_record_gap_rejected
        with pytest.raises(SystemExit, match="no pending signal"):
            run_record_gap_rejected(_Ctx(sandbox_config), "GHOST")

    def test_delete_trade_tombstones_with_reason(self, sandbox_config, sheet_out):
        from nse_cash.cli.ledger_cmd import run_delete_trade
        led = Ledger(sandbox_config.paths.ledger_db_path)
        _add_trade(led, "DEL", tranche1_qty=600, tranche2_qty=600)
        led.close()

        run_delete_trade(_Ctx(sandbox_config), "DEL-2026-09-01", "fat finger")
        assert "deleted" in sheet_out.getvalue()
        led = Ledger(sandbox_config.paths.ledger_db_path)
        try:
            assert led.get_trade("DEL-2026-09-01") is None
        finally:
            led.close()

    def test_delete_trade_unknown_id_exits(self, sandbox_config):
        from nse_cash.cli.ledger_cmd import run_delete_trade
        with pytest.raises(SystemExit, match="unknown trade"):
            run_delete_trade(_Ctx(sandbox_config), "NOPE-2026-09-01", "why not")

    def test_ledger_display_renders_book_and_integrity(self, sandbox_config,
                                                       sheet_out):
        from nse_cash.cli.ledger_cmd import run_ledger_display
        led = Ledger(sandbox_config.paths.ledger_db_path)
        _add_trade(led, "DIS", tranche1_qty=600, tranche2_qty=600)
        led.record_fill("DIS-2026-09-01", 100.0, D1)
        led.record_exit("DIS-2026-09-01", 102.0, D1 + timedelta(days=1),
                        "T1_TARGET")
        led.close()

        run_ledger_display(_Ctx(sandbox_config))   # no market store: None-safe path
        out = sheet_out.getvalue()
        assert "Portfolio Ledger" in out
        # T1 filled with no breakeven event yet -> integrity warning must print.
        assert "LEDGER:" in out
