"""Extra coverage for the governance engine (core/governance.py).

Live NSE fetchers are exercised through fake clients; the deterministic
circuit-hit detector, persistence and exclusion query run against a real
in-memory MarketStore.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from nse_cash.core.governance import (
    circuit_hits_from_bars, excluded_symbols, fetch_asm_gsm,
    fetch_board_meetings, fetch_circuit_bands, persist_governance,
)
from nse_cash.data.storage import MarketStore

D = date(2026, 9, 10)  # a Thursday


@pytest.fixture()
def store(tmp_path):
    s = MarketStore(tmp_path / "gov.duckdb")
    yield s
    s.close()


def _bars(symbol: str, rows: list[tuple[date, float, float, float, float, int]],
          prev_close: float | None = None) -> pd.DataFrame:
    """rows: (date, open, high, low, close, volume)."""
    out = []
    for i, (d, o, h, l, c, v) in enumerate(rows):
        out.append({
            "symbol": symbol, "date": d, "series": "EQ",
            "open": o, "high": h, "low": l, "close": c,
            "volume": v, "turnover": c * v,
            "deliverable_qty": v // 2, "delivery_pct": 50.0,
        })
    return pd.DataFrame(out)


def _upsert_bars(store: MarketStore, df: pd.DataFrame) -> None:
    cols = ["symbol", "date", "series", "open", "high", "low", "close",
            "volume", "turnover", "deliverable_qty", "delivery_pct"]
    store.upsert_daily_bars(df[cols])


class _FakeClient:
    """Programmable stand-in for NSEHttpClient.get_json."""

    def __init__(self, payloads: dict[str, object] | None = None,
                 error: Exception | None = None):
        self.payloads = payloads or {}
        self.error = error
        self.calls: list[str] = []

    def get_json(self, url: str, bootstrap: bool = False, **_kw):
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        for key, payload in self.payloads.items():
            if key in url:
                return payload
        return {}


# ---------------------------------------------------------------------------
# 1. ASM / GSM parsing
# ---------------------------------------------------------------------------

class TestAsmGsm:
    def test_dict_payload_sections_are_flattened(self):
        client = _FakeClient({"reportASM": {
            "longterm": {"data": [{"symbol": "abc"}, {"symbol": "XYZ"}]},
            "shortterm": {"data": [{"symbol": "FOO"}]},
        }, "reportGSM": [{"symbol": "BAR"}]})
        out = fetch_asm_gsm(client, D)
        assert out["ASM"] == {"ABC", "XYZ", "FOO"}
        assert out["GSM"] == {"BAR"}

    def test_list_payload_and_symbol_key_variant(self):
        client = _FakeClient({"reportGSM": [{"Symbol": "lower"}]})
        out = fetch_asm_gsm(client, D)
        assert out["GSM"] == {"LOWER"}

    def test_fetch_failure_degrades_to_empty_with_warning(self):
        client = _FakeClient(error=ConnectionError("NSE down"))
        out = fetch_asm_gsm(client, D)
        assert out == {"ASM": set(), "GSM": set()}


# ---------------------------------------------------------------------------
# 2. Circuit bands (best-effort shape)
# ---------------------------------------------------------------------------

class TestCircuitBands:
    def test_rows_extracted_from_data_key(self):
        client = _FakeClient({"equity-stockIndices": {
            "data": [{"symbol": "abc", "percentChange": 4.2},
                     {"symbol": "", "percentChange": 1.0},
                     {"symbol": "NOBAND"}]}})
        df = fetch_circuit_bands(client, D)
        assert list(df["symbol"]) == ["ABC"]
        assert df["band_pct"].iloc[0] == pytest.approx(4.2)

    def test_failure_returns_empty_frame(self):
        client = _FakeClient(error=RuntimeError("boom"))
        df = fetch_circuit_bands(client, D)
        assert df.empty
        assert list(df.columns) == ["symbol", "band_pct"]


# ---------------------------------------------------------------------------
# 3. Deterministic circuit-hit detection from bars
# ---------------------------------------------------------------------------

class TestCircuitHits:
    def test_full_day_lock_high_equals_low_with_volume(self):
        rows = [(D - timedelta(days=1), 100.0, 105.0, 95.0, 101.0, 10_000),
                (D, 110.0, 110.0, 110.0, 110.0, 5_000)]  # locked flat, +8.9%
        store_bars = _bars("LOCKED", rows)
        with_market = MarketStore(":memory:")
        try:
            _upsert_bars(with_market, store_bars)
            hits = circuit_hits_from_bars(with_market.con, D - timedelta(days=5), D)
            assert list(hits["symbol"]) == ["LOCKED"]
        finally:
            with_market.close()

    def test_normal_trend_candle_is_not_a_hit(self):
        # +5% close-strong candle with a real range: momentum, not a lock.
        rows = [(D - timedelta(days=1), 100.0, 105.0, 95.0, 101.0, 10_000),
                (D, 102.0, 107.0, 101.5, 106.0, 20_000)]
        m = MarketStore(":memory:")
        try:
            _upsert_bars(m, _bars("TREND", rows))
            hits = circuit_hits_from_bars(m.con, D - timedelta(days=5), D)
            assert hits.empty
        finally:
            m.close()

    def test_upper_10pct_band_lock_detected(self):
        rows = [(D - timedelta(days=1), 100.0, 101.0, 99.0, 100.0, 10_000),
                (D, 111.0, 111.0, 109.8, 111.0, 10_000)]  # +11%, close == high
        m = MarketStore(":memory:")
        try:
            _upsert_bars(m, _bars("UPLOCK", rows))
            hits = circuit_hits_from_bars(m.con, D - timedelta(days=5), D)
            assert list(hits["symbol"]) == ["UPLOCK"]
        finally:
            m.close()

    def test_lower_band_lock_detected(self):
        rows = [(D - timedelta(days=1), 100.0, 101.0, 99.0, 100.0, 10_000),
                (D, 88.0, 89.0, 88.0, 88.0, 10_000)]  # -12%, close == low
        m = MarketStore(":memory:")
        try:
            _upsert_bars(m, _bars("DNLOCK", rows))
            hits = circuit_hits_from_bars(m.con, D - timedelta(days=5), D)
            assert list(hits["symbol"]) == ["DNLOCK"]
        finally:
            m.close()

    def test_zero_volume_flat_bar_is_not_a_lock(self):
        # Flat at yesterday's close with zero volume: full-day lock needs
        # volume, and no band predicate fires at a ~0% return.
        rows = [(D - timedelta(days=1), 100.0, 101.0, 99.0, 100.0, 10_000),
                (D, 100.0, 100.0, 100.0, 100.0, 0)]
        m = MarketStore(":memory:")
        try:
            _upsert_bars(m, _bars("NOVOL", rows))
            assert circuit_hits_from_bars(m.con, D - timedelta(days=5), D).empty
        finally:
            m.close()

    def test_empty_database_returns_empty_frame(self, store):
        hits = circuit_hits_from_bars(store.con, D - timedelta(days=5), D)
        assert hits.empty
        assert list(hits.columns) == ["symbol", "date"]


# ---------------------------------------------------------------------------
# 4. Board meetings
# ---------------------------------------------------------------------------

class TestBoardMeetings:
    def test_meetings_within_window_are_kept(self):
        payload = {"data": [
            {"symbol": "abc", "date": (D + timedelta(days=2)).isoformat()},
            {"symbol": "outside", "date": (D + timedelta(days=30)).isoformat()},
            {"symbol": "", "date": (D + timedelta(days=1)).isoformat()},
            {"symbol": "bad", "date": "not-a-date"},
        ]}
        df = fetch_board_meetings(_FakeClient({"event-calendar": payload}),
                                  D, D + timedelta(days=7))
        assert list(df["symbol"]) == ["ABC"]
        assert df["meeting_date"].iloc[0] == D + timedelta(days=2)

    def test_list_payload_without_data_key(self):
        payload = [{"symbol": "XYZ", "date": D.isoformat()}]
        df = fetch_board_meetings(_FakeClient({"event-calendar": payload}),
                                  D, D + timedelta(days=7))
        assert list(df["symbol"]) == ["XYZ"]

    def test_fetch_failure_degrades_to_empty(self):
        df = fetch_board_meetings(_FakeClient(error=RuntimeError("x")),
                                  D, D + timedelta(days=7))
        assert df.empty


# ---------------------------------------------------------------------------
# 5. Persistence + exclusion query
# ---------------------------------------------------------------------------

class TestPersistAndExclude:
    def test_no_rows_persists_nothing(self, store):
        assert persist_governance(store, D, {"ASM": set(), "GSM": set()},
                                  pd.DataFrame(columns=["symbol", "date"]),
                                  pd.DataFrame(columns=["symbol", "meeting_date"])) == 0
        assert excluded_symbols(store.con, D) == set()

    def test_asm_gsm_circuit_board_rows_written(self, store):
        circuit = pd.DataFrame([{"symbol": "LOCKED", "date": D}])
        board = pd.DataFrame([{"symbol": "MEET", "meeting_date": D + timedelta(days=2)}])
        n = persist_governance(store, D, {"ASM": {"AAA"}, "GSM": {"BBB"}},
                               circuit, board)
        assert n == 4
        # ASM/GSM/CIRCUIT look back 7 calendar days; BOARD looks ahead.
        assert excluded_symbols(store.con, D) == {"AAA", "BBB", "LOCKED", "MEET"}

    def test_asm_flag_expires_after_seven_days(self, store):
        persist_governance(store, D, {"ASM": {"AAA"}}, pd.DataFrame(columns=["symbol", "date"]),
                           pd.DataFrame(columns=["symbol", "meeting_date"]))
        later = D + timedelta(days=8)
        assert "AAA" not in excluded_symbols(store.con, later)

    def test_board_meeting_expires_after_lookahead(self, store):
        board = pd.DataFrame([{"symbol": "MEET", "meeting_date": D + timedelta(days=2)}])
        persist_governance(store, D, {"ASM": set(), "GSM": set()},
                           pd.DataFrame(columns=["symbol", "date"]), board)
        later = D + timedelta(days=12)
        assert "MEET" not in excluded_symbols(store.con, later)

    def test_persist_is_idempotent(self, store):
        circuit = pd.DataFrame([{"symbol": "LOCKED", "date": D}])
        for _ in range(2):
            persist_governance(store, D, {"ASM": {"AAA"}}, circuit,
                               pd.DataFrame(columns=["symbol", "meeting_date"]))
        n = store.con.execute(
            "SELECT count(*) FROM governance WHERE list_type = 'CIRCUIT'").fetchone()[0]
        assert n == 1
