"""Phase 8 additions: the BRD §10.1 verdict (8.3) and the 3:20 PM LTP token
parser (runbook hardening). Pure functions; hand-computed expectations."""

from __future__ import annotations

import pytest

from nse_cash.backtest.metrics import BRD_TARGETS, brd_targets_verdict, verify_brd_targets


class TestBrdVerdict:
    def test_all_pass_is_true(self):
        metrics = {
            "cagr_post_tax": 0.18, "win_rate": 0.52, "profit_factor": 1.7,
            "max_drawdown": -0.05, "trades": 100,
            "avg_win_net_pct": 2.55, "avg_loss_net_pct": -1.85,
            "expectancy_net_pct": 0.60,
        }
        rows = brd_targets_verdict(metrics)
        assert len(rows) == len(BRD_TARGETS)
        assert all(r["verdict"] == "PASS" for r in rows)
        assert verify_brd_targets(metrics) is True

    def test_one_fail_flips_verification(self):
        metrics = {
            "cagr_post_tax": 0.18, "win_rate": 0.60, "profit_factor": 1.7,
            "max_drawdown": -0.05, "trades": 100,
            "avg_win_net_pct": 2.55, "avg_loss_net_pct": -1.85,
            "expectancy_net_pct": 0.60,
        }
        rows = {r["metric"]: r["verdict"] for r in brd_targets_verdict(metrics)}
        assert rows["Win Rate"] == "FAIL"
        assert verify_brd_targets(metrics) is False

    def test_missing_values_are_missing_not_fail(self):
        """A zero-trade run must show MISSING, never PASS and not a bare FAIL:
        'no data' must not impersonate 'bad performance'."""
        rows = {r["metric"]: r["verdict"] for r in brd_targets_verdict({})}
        assert set(rows.values()) == {"MISSING"}
        assert verify_brd_targets({}) is False

    def test_zero_trades_marks_percent_rows_missing(self):
        """Percent-per-trade rows are undefined with no filled trades even if
        rupee fields exist (0.0 would silently read as a bad expectancy)."""
        metrics = {"trades": 0, "cagr_post_tax": 0.2, "win_rate": 0.5,
                   "profit_factor": None, "max_drawdown": -0.03}
        rows = {r["metric"]: r["verdict"] for r in brd_targets_verdict(metrics)}
        assert rows["Avg Win (net %)"] == "MISSING"
        assert rows["Avg Loss (net %)"] == "MISSING"
        assert rows["Expectancy (net %)"] == "MISSING"

    def test_boundary_edges_pass(self):
        metrics = {
            "cagr_post_tax": 0.14, "win_rate": 0.48, "profit_factor": 1.55,
            "max_drawdown": -0.085, "trades": 80,
            "avg_win_net_pct": 2.70, "avg_loss_net_pct": -1.90,
            "expectancy_net_pct": 0.40,
        }
        assert verify_brd_targets(metrics) is True

    def test_brd_targets_match_document(self):
        """The verdict table's ranges ARE BRD §10.1 — pinned so a doc drift or
        a stray edit to the constants cannot slip through silently."""
        table = {key: (lo, hi) for key, _label, lo, hi in BRD_TARGETS}
        assert table["cagr_post_tax"] == (0.14, None)
        assert table["win_rate"] == (0.48, 0.56)
        assert table["profit_factor"] == (1.55, 1.85)
        assert table["max_drawdown"] == (None, -0.085)
        assert table["avg_win_net_pct"] == (2.40, 2.70)
        assert table["avg_loss_net_pct"] == (-1.90, -1.80)
        assert table["expectancy_net_pct"] == (0.40, 0.75)


class TestParseLtps:
    def _make_cmd(self):
        from nse_cash.cli.ledger_cmd import run_check_eod
        return run_check_eod

    def test_parse_rejects_tokens_without_equals(self):
        """SYMBOL=PRICE is a trust boundary: a bare number (the old positional
        syntax that let swapped LTPs mis-prompt silently) must hard-fail."""
        from nse_cash.cli.ledger_cmd import _parse_ltps
        with pytest.raises(SystemExit, match="SYMBOL=PRICE"):
            _parse_ltps(("101.25",), [])

    def test_parse_rejects_unknown_symbol(self):
        from nse_cash.cli.ledger_cmd import _parse_ltps
        opens = [({"symbol": "SHOCK"}, None)]
        with pytest.raises(SystemExit, match="unknown symbol"):
            _parse_ltps(("TATASTEEL=101.25",), opens)

    def test_parse_rejects_bad_price(self):
        from nse_cash.cli.ledger_cmd import _parse_ltps
        opens = [({"symbol": "SHOCK"}, None)]
        with pytest.raises(SystemExit, match="bad price"):
            _parse_ltps(("SHOCK=abc",), opens)

    def test_parse_accepts_symbols_case_insensitive(self):
        from nse_cash.cli.ledger_cmd import _parse_ltps
        opens = [({"symbol": "SHOCK"}, None), ({"symbol": "BANKX"}, None)]
        ltps = _parse_ltps(("shock=101.25", "BANKX=502.10"), opens)
        assert ltps == {"SHOCK": 101.25, "BANKX": 502.10}
