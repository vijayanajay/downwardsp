"""Unit tests for sync orchestration: holiday short-circuit and up-to-date checks."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from nse_cash.cli.sync_cmd import _fetch_day, _run_latest


def test_fetch_day_short_circuits_on_holiday():
    client = MagicMock()
    d = date(2026, 9, 13)
    raw_dir = MagicMock()

    with patch("nse_cash.cli.sync_cmd.fetch_bhavcopy", return_value=None) as mock_bhav, \
         patch("nse_cash.cli.sync_cmd.fetch_delivery") as mock_mto, \
         patch("nse_cash.cli.sync_cmd.fetch_indices_for_date") as mock_idx:
        res = _fetch_day(client, d, raw_dir)
        mock_bhav.assert_called_once_with(client, d, raw_dir)
        mock_mto.assert_not_called()
        mock_idx.assert_not_called()
        assert res["date"] == d
        assert res["bhav"] is None


def test_run_latest_skips_when_already_up_to_date(capsys):
    client = MagicMock()
    store = MagicMock()
    store.latest_date.return_value = date(2026, 9, 11)
    raw_dir = MagicMock()

    with patch("nse_cash.cli.sync_cmd._latest_trading_day", return_value=date(2026, 9, 11)):
        _run_latest(client, store, raw_dir, force=False)
        out = capsys.readouterr().out
        assert "already up to date" in out
        # Ensure _fetch_day is not called
        store.upsert_daily_bars.assert_not_called()
