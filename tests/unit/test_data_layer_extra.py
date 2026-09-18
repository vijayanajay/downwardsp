"""Extra coverage for the data ingestion layer, all offline.

NSEHttpClient itself runs against a fake requests.Session (retry / cache /
JSON-retry paths); the bhavcopy / delivery / indices / corporate-actions
fetchers run against a fake client so the fallback chains, cache reads and
degradation paths are tested without network.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
import requests

from nse_cash.data import bhavcopy, corporate_actions, delivery, fetcher, indices

D = date(2026, 9, 10)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status=200, content=b"payload"):
        self.status_code = status
        self.content = content
        self.text = content.decode("utf-8", errors="replace")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)

    def json(self):
        return json.loads(self.content.decode("utf-8"))


class _FakeSession:
    """Records calls; programmable per-URL responses (NSEHttpClient tests)."""

    def __init__(self, routes: dict[str, list] | None = None):
        self.routes = routes or {}
        self.headers = {}
        self.calls: list[str] = []

    def get(self, url, timeout=None, stream=False, headers=None):
        self.calls.append(url)
        seq = self.routes.get(url, [_FakeResponse()])
        resp = seq.pop(0) if len(seq) > 1 else seq[0]
        if isinstance(resp, Exception):
            raise resp
        return resp


class _FakeClient:
    """Stand-in for NSEHttpClient at the module-fetcher boundary.

    Unrouted URLs 404 (the holiday path); routed entries are _FakeResponse
    instances or raised exceptions.
    """

    def __init__(self, routes: dict[str, list] | None = None):
        self.routes = routes or {}
        self.calls: list[str] = []

    def _next(self, url: str) -> _FakeResponse:
        self.calls.append(url)
        seq = self.routes.get(url)
        if seq is None:
            return _FakeResponse(status=404)
        resp = seq.pop(0) if len(seq) > 1 else seq[0]
        if isinstance(resp, Exception):
            raise resp
        return resp

    @staticmethod
    def _check(resp: _FakeResponse) -> None:
        if resp.status_code >= 400:
            raise requests.HTTPError(str(resp.status_code), response=resp)

    def get_bytes(self, url, cache_path=None, bootstrap=False) -> bytes:
        resp = self._next(url)
        self._check(resp)
        if cache_path is not None:
            p = Path(cache_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(resp.content)
        return resp.content

    def get_json(self, url, bootstrap=True, **_kw):
        resp = self._next(url)
        self._check(resp)
        return json.loads(resp.content.decode("utf-8"))


# ---------------------------------------------------------------------------
# NSEHttpClient against a fake session
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path):
    c = fetcher.NSEHttpClient(cache_dir=tmp_path / "raw", timeout=1.0)
    yield c
    c.close()


def _wire_session(monkeypatch, fake: _FakeSession) -> None:
    monkeypatch.setattr(fetcher.requests, "Session", lambda: fake)


class TestNSEHttpClient:
    def test_get_bytes_downloads_and_caches(self, client, monkeypatch):
        fake = _FakeSession({"https://x.test/blob": [_FakeResponse(content=b"BINARY")]})
        _wire_session(monkeypatch, fake)
        cache = client.cache_dir / "2026" / "09" / "blob.bin"
        out = client.get_bytes("https://x.test/blob", cache_path=cache)
        assert out == b"BINARY"
        assert cache.exists() and cache.read_bytes() == b"BINARY"

    def test_get_bytes_cache_hit_skips_network(self, client, monkeypatch):
        cache = client.cache_dir / "hit.bin"
        cache.parent.mkdir(parents=True)
        cache.write_bytes(b"FROM-DISK")
        fake = _FakeSession()
        _wire_session(monkeypatch, fake)
        assert client.get_bytes("https://x.test/hit", cache_path=cache) == b"FROM-DISK"
        assert fake.calls == []

    def test_get_json_parses_payload(self, client, monkeypatch):
        fake = _FakeSession({"https://api.test/j": [
            _FakeResponse(content=json.dumps({"ok": 1}).encode())]})
        _wire_session(monkeypatch, fake)
        assert client.get_json("https://api.test/j") == {"ok": 1}

    def test_get_json_recovers_after_html_block_page(self, client, monkeypatch):
        """First response is not JSON (NSE block page); the retry path must
        bootstrap cookies, resend with JSON headers, and parse the payload."""
        html = _FakeResponse(content=b"<html>blocked</html>")
        good = _FakeResponse(content=json.dumps({"after": True}).encode())
        fake = _FakeSession({"https://api.test/blocked": [html, good]})
        _wire_session(monkeypatch, fake)
        assert client.get_json("https://api.test/blocked") == {"after": True}
        assert fake.calls.count("https://api.test/blocked") == 2

    def test_thread_local_session_is_stable_per_thread(self, client, monkeypatch):
        fake = _FakeSession()
        _wire_session(monkeypatch, fake)
        assert client.session is client.session

    def test_retryable_status_triggers_rebootstrap(self, client, monkeypatch):
        """A 403 must flip needs_bootstrap so the retry gets fresh cookies."""
        forty3 = _FakeResponse(status=403)
        ok = _FakeResponse(content=b"fine")
        fake = _FakeSession({"https://x.test/rot": [forty3, ok]})
        _wire_session(monkeypatch, fake)
        assert client.get_bytes("https://x.test/rot") == b"fine"
        # homepage bootstrap + 2 endpoint calls
        assert fake.calls[0] == "https://www.nseindia.com/"

    def test_non_retryable_status_raises_immediately(self, client, monkeypatch):
        fake = _FakeSession({"https://x.test/gone": [_FakeResponse(status=404)]})
        _wire_session(monkeypatch, fake)
        with pytest.raises(requests.HTTPError):
            client.get_bytes("https://x.test/gone")


# ---------------------------------------------------------------------------
# Delivery (MTO)
# ---------------------------------------------------------------------------

MTO_TEXT = (
    "20,1,SHOCK,EQ,100000,50000,50.00\n"
    "20,2,BANKX,BE,90000,9000,10.00\n"     # BE series dropped
    "20,3,ONLY,EQ,70000,35000,50.00\n"
)


class TestFetchDelivery:
    def test_parses_and_stamps_date(self, tmp_path):
        cl = _FakeClient({delivery.MTO_URL.format(d=D): [
            _FakeResponse(content=MTO_TEXT.encode())]})
        df = delivery.fetch_delivery(cl, D, tmp_path / "raw")
        assert list(df["symbol"]) == ["SHOCK", "ONLY"]
        assert (df["date"] == D).all()
        assert df["deliverable_qty"].iloc[0] == 50_000

    def test_falls_back_to_csv_url_on_dat_404(self, tmp_path):
        cl = _FakeClient({
            delivery.MTO_URL_CSV.format(d=D): [
                _FakeResponse(content=MTO_TEXT.encode())]})
        df = delivery.fetch_delivery(cl, D, tmp_path / "raw")
        assert df is not None and len(df) == 2
        assert cl.calls[0] == delivery.MTO_URL.format(d=D)

    def test_404_on_both_urls_returns_none(self, tmp_path):
        cl = _FakeClient()
        assert delivery.fetch_delivery(cl, D, tmp_path / "raw") is None

    def test_invalid_payload_returns_none(self, tmp_path):
        cl = _FakeClient({delivery.MTO_URL.format(d=D): [
            _FakeResponse(content=b"20,1,X,ZZ,1,2,3\n")]})
        assert delivery.fetch_delivery(cl, D, tmp_path / "raw") is None


# ---------------------------------------------------------------------------
# Indices
# ---------------------------------------------------------------------------

IND_CSV = (
    "Index Name,Open Index Value,High Index Value,Low Index Value,"
    "Closing Index Value,Volume\n"
    'NIFTY 50,"24,000.50","24,100.00","23,950.00","24,050.25","100000"\n'
    'NIFTY 500,"4,600.10","4,650.00","4,590.00","4,620.75","200000"\n'
    'SOME OTHER INDEX,1,2,3,4,5\n'
)


class TestIndices:
    def test_to_float_strips_commas_and_junk(self):
        assert indices._to_float("1,234.5") == 1234.5
        assert pd.isna(indices._to_float("n/a"))

    def test_fetch_ind_close_parses_and_caches(self, tmp_path):
        cl = _FakeClient({
            indices.IND_CLOSE_URL_TEMPLATES[0].format(d=D): [
                _FakeResponse(content=IND_CSV.encode())]})
        df = indices.fetch_indices_for_date(cl, D, tmp_path / "raw")
        assert set(df["index_name"]) == {"NIFTY 50", "NIFTY 500"}
        assert df["close"].iloc[0] == pytest.approx(24_050.25)
        cache = tmp_path / "raw" / "2026" / "09" / f"ind_close_all_{D:%d%m%Y}.csv"
        assert cache.exists()

    def test_fetch_uses_mirror_when_primary_404s(self, tmp_path):
        cl = _FakeClient({
            indices.IND_CLOSE_URL_TEMPLATES[1].format(d=D): [
                _FakeResponse(content=IND_CSV.encode())]})
        df = indices.fetch_indices_for_date(cl, D, tmp_path / "raw")
        assert df is not None
        assert cl.calls[0].startswith("https://nsearchives.nseindia.com")

    def test_all_sources_down_returns_none(self, tmp_path):
        cl = _FakeClient()
        assert indices.fetch_indices_for_date(cl, D, tmp_path / "raw") is None

    def test_parse_rejects_missing_index_column(self):
        with pytest.raises(ValueError, match="INDEX_NAME"):
            indices._parse_ind_close("Foo,Bar\n1,2\n", D)

    def test_ingest_from_local_cache_seeds_store(self, tmp_path):
        from nse_cash.data.storage import MarketStore
        ohlcv = tmp_path / "ohlcv"
        ohlcv.mkdir()
        pd.DataFrame({
            "date": pd.date_range("2026-01-04", periods=3),
            "open": 1.0, "high": 2.0, "low": 0.5,
            "close": [100.0, 101.0, 102.0], "volume": 5.0,
        }).to_parquet(ohlcv / "_nifty.parquet")
        store = MarketStore(tmp_path / "i.duckdb")
        try:
            n = indices.ingest_from_local_cache(store, ohlcv)
            assert n == 3
            got = store.con.execute(
                "SELECT index_name, count(*) FROM market_indices GROUP BY 1").fetchall()
            assert got == [("NIFTY 50", 3)]
        finally:
            store.close()

    def test_ingest_skips_missing_files(self, tmp_path):
        from nse_cash.data.storage import MarketStore
        store = MarketStore(tmp_path / "i2.duckdb")
        try:
            assert indices.ingest_from_local_cache(store, tmp_path) == 0
        finally:
            store.close()


# ---------------------------------------------------------------------------
# Corporate actions
# ---------------------------------------------------------------------------

class TestCorporateActionsParsing:
    def test_parse_normalizes_and_classifies(self):
        payload = {"data": [
            {"symbol": "abc", "exDate": "10-Sep-2026",
             "subject": "SPLIT FROM RS 10 TO RS 2"},
            {"symbol": "ABC", "exDate": "10-Sep-2026",
             "subject": "SPLIT FROM RS 10 TO RS 2"},        # deduped
            {"Symbol": "DEF", "ex_date": "2026-09-12",
             "purpose": "BONUS 3:1", "series": "EQ"},
            {"symbol": "", "exDate": "10-Sep-2026", "subject": "BONUS 1:1"},
            {"symbol": "NOEX", "subject": "BONUS 1:1"},
            {"symbol": "BADD", "exDate": "garbage", "subject": "BONUS 1:1"},
        ]}
        df = corporate_actions.parse_corporate_actions(payload)
        assert list(df["symbol"]) == ["ABC", "DEF"]
        assert df["action_type"].iloc[0] == "SPLIT"
        assert df["ratio_a"].iloc[0] == pytest.approx(4.0)
        assert df["action_type"].iloc[1] == "BONUS"
        assert df["ratio_a"].iloc[1] == pytest.approx(3.0)

    def test_date_window_filter(self):
        payload = {"data": [
            {"symbol": "EARLY", "exDate": "01-Aug-2026", "subject": "BONUS 1:1"},
            {"symbol": "IN", "exDate": "10-Sep-2026", "subject": "BONUS 1:1"},
            {"symbol": "LATE", "exDate": "01-Oct-2026", "subject": "BONUS 1:1"},
        ]}
        df = corporate_actions.parse_corporate_actions(
            payload, date(2026, 9, 1), date(2026, 9, 30))
        assert list(df["symbol"]) == ["IN"]

    def test_adjustment_factor_numeric_and_junk(self):
        payload = {"data": [
            {"symbol": "A", "exDate": "10-Sep-2026", "subject": "DIVIDEND",
             "adjustment_factor": "1.5"},
            {"symbol": "B", "exDate": "10-Sep-2026", "subject": "DIVIDEND",
             "adjustment_factor": "bogus"},
        ]}
        df = corporate_actions.parse_corporate_actions(payload)
        assert df["adjustment_factor"].iloc[0] == pytest.approx(1.5)
        assert pd.isna(df["adjustment_factor"].iloc[1])

    def test_fetch_failure_returns_none(self):
        class Boom:
            def get_json(self, url, bootstrap=True):
                raise ConnectionError("no network")
        assert corporate_actions.fetch_corporate_actions(Boom()) is None

    def test_load_seed_classifies_when_action_type_missing(self, tmp_path):
        seed = tmp_path / "seed.csv"
        seed.write_text(
            "symbol,ex_date,purpose\n"
            "SHOCK,2026-09-10,SPLIT FROM RS 10 TO RS 2\n", encoding="utf-8")
        df = corporate_actions.load_seed_corporate_actions(seed)
        assert df["action_type"].iloc[0] == "SPLIT"
        assert df["ratio_a"].iloc[0] == pytest.approx(4.0)

    def test_load_seed_missing_columns_falls_through_to_repo_seed(self, tmp_path, monkeypatch):
        """A candidate without symbol/ex_date columns is skipped, and the
        search continues down the candidate list (the repo's real seed)."""
        seed = tmp_path / "bad.csv"
        seed.write_text("ticker,when\nSHOCK,2026-09-10\n", encoding="utf-8")
        df = corporate_actions.load_seed_corporate_actions(seed)
        assert df is not None
        assert "symbol" in df.columns and "ex_date" in df.columns

    def test_load_seed_none_when_no_candidates_exist(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert corporate_actions.load_seed_corporate_actions() is None

    def test_load_seed_prefers_explicit_path(self, tmp_path):
        seed = tmp_path / "mine.csv"
        seed.write_text("symbol,ex_date,purpose\nMINE,2026-01-05,BONUS 1:1\n",
                        encoding="utf-8")
        df = corporate_actions.load_seed_corporate_actions(seed)
        assert list(df["symbol"]) == ["MINE"]


# ---------------------------------------------------------------------------
# Bhavcopy: URL helpers + fetch fallback chain
# ---------------------------------------------------------------------------

def _zip_of(name: str, text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, text)
    return buf.getvalue()


LEGACY_CSV = (
    "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,TOTTRDQTY,TOTTRDVAL,TIMESTAMP\n"
    "SHOCK,EQ,100,101,99,100.5,100.4,1000,100500,10-Sep-2026\n"
    "SHOCK,BE,100,101,99,100.5,100.4,1000,100500,10-Sep-2026\n"
)
UDIFF_CSV = (
    "TckrSymb,SctySrs,TradDt,OpnPric,HghPric,LwPric,ClsPric,LastPric,"
    "TtlTradgVol,TtlTrfVal\n"
    "SHOCK,EQ,2026-09-10,100,101,99,100.5,100.4,1000,100500\n"
)
PR_CSV = (
    "SYMBOL,SERIES,DATE1,OPEN_PRICE,HIGH_PRICE,LOW_PRICE,LAST_PRICE,"
    "CLOSE_PRICE,TTL_TRD_QNTY,TURNOVER_LACS,DELIV_QTY,DELIV_PER\n"
    "SHOCK,EQ,10-Sep-2026,100,101,99,100.4,100.5,1000,1005.0,500,50.0\n"
)


class TestBhavcopyChain:
    def test_url_and_cache_path_shapes(self):
        assert bhavcopy.legacy_url(D).endswith("cm10SEP2026bhav.csv.zip")
        assert "BhavCopy_NSE_CM_0_0_0_20260910" in bhavcopy.udiff_url(D)
        assert "sec_bhavdata_full_10092026" in bhavcopy.sec_bhav_url(D)
        assert bhavcopy.pr_url(D).endswith("PR100926.zip")
        assert bhavcopy.legacy_cache_path(Path("r"), D).name == "cm10SEP2026bhav.csv.zip"
        assert bhavcopy.udiff_cache_path(Path("r"), D).name == "BhavCopy_NSE_CM_20260910.zip"
        assert bhavcopy.sec_bhav_cache_path(Path("r"), D).name == "sec_bhavdata_full_10092026.csv"
        assert bhavcopy.pr_cache_path(Path("r"), D).name == "PR100926.zip"

    def test_udiff_wins_after_cutoff(self, tmp_path):
        assert D > bhavcopy._LEGACY_CUTOFF
        cl = _FakeClient({
            bhavcopy.udiff_url(D): [_FakeResponse(content=_zip_of("u.csv", UDIFF_CSV))]})
        res = bhavcopy.fetch_bhavcopy(cl, D, tmp_path / "raw")
        assert res.source == "udiff"
        assert res.df["symbol"].iloc[0] == "SHOCK"
        assert cl.calls == [bhavcopy.udiff_url(D)]  # first hit stops the chain

    def test_falls_through_to_sec_bhav_when_udiff_404s(self, tmp_path):
        cl = _FakeClient({
            bhavcopy.sec_bhav_url(D): [_FakeResponse(content=PR_CSV.encode())]})
        res = bhavcopy.fetch_bhavcopy(cl, D, tmp_path / "raw")
        assert res.source == "sec_bhav"
        assert res.df["delivery_pct"].iloc[0] == pytest.approx(50.0)

    def test_falls_through_to_pr_zip(self, tmp_path):
        cl = _FakeClient({
            bhavcopy.pr_url(D): [_FakeResponse(content=_zip_of(
                "sec_bhavdata_full_10092026.csv", PR_CSV))]})
        res = bhavcopy.fetch_bhavcopy(cl, D, tmp_path / "raw")
        assert res.source == "pr"

    def test_uses_legacy_before_cutoff(self, tmp_path):
        old = date(2020, 1, 2)
        cl = _FakeClient({
            bhavcopy.legacy_url(old): [
                _FakeResponse(content=_zip_of("l.csv", LEGACY_CSV))]})
        res = bhavcopy.fetch_bhavcopy(cl, old, tmp_path / "raw")
        assert res.source == "legacy"
        assert (res.df["series"] == "EQ").all()
        assert res.df["turnover"].iloc[0] == pytest.approx(100_500.0)  # absolute rupees

    def test_holiday_returns_none_after_all_404(self, tmp_path):
        cl = _FakeClient()
        assert bhavcopy.fetch_bhavcopy(cl, D, tmp_path / "raw") is None

    def test_corrupt_zip_tries_next_source(self, tmp_path):
        cl = _FakeClient({
            bhavcopy.udiff_url(D): [_FakeResponse(content=b"not-a-zip")],
            bhavcopy.sec_bhav_url(D): [_FakeResponse(content=PR_CSV.encode())]})
        res = bhavcopy.fetch_bhavcopy(cl, D, tmp_path / "raw")
        assert res.source == "sec_bhav"

    def test_all_ohlc_free_rows_kill_every_source(self, tmp_path):
        """A source whose every row lacks OHLC is rejected, the chain moves on,
        and the day ends unavailable — never an all-NaN bar in storage."""
        csv = PR_CSV.replace("100,101,99,100.4,100.5,1000,1005.0,500,50.0",
                             ",,,,100.5,1000,1005.0,500,50.0")
        cl = _FakeClient({
            bhavcopy.udiff_url(D): [_FakeResponse(status=404)],
            bhavcopy.sec_bhav_url(D): [_FakeResponse(content=csv.encode())],
            bhavcopy.pr_url(D): [_FakeResponse(content=csv.encode())],
        })
        assert bhavcopy.fetch_bhavcopy(cl, D, tmp_path / "raw") is None

    def test_join_delivery_mto_fills_missing_values(self):
        bars = pd.DataFrame({
            "symbol": ["SHOCK", "SHOCK", "ONLY"],
            "date": [D, D, D],
            "deliverable_qty": [pd.NA, 10, pd.NA],
            "delivery_pct": [pd.NA, 5.0, pd.NA],
        })
        mto = pd.DataFrame({
            "symbol": ["SHOCK", "ONLY"], "date": [D, D],
            "deliverable_qty": [50, 70], "delivery_pct": [50.0, 70.0],
        })
        merged = bhavcopy.join_delivery(bars, mto)
        # PR value (10) wins over MTO (50); missing cells take MTO.
        assert merged["deliverable_qty"].tolist()[0] == 50
        assert merged["deliverable_qty"].tolist()[1] == 10
        assert merged["delivery_pct"].tolist()[2] == pytest.approx(70.0)

    def test_join_delivery_noop_on_empty(self):
        bars = pd.DataFrame({"symbol": ["X"], "date": [D],
                             "deliverable_qty": [1], "delivery_pct": [1.0]})
        out = bhavcopy.join_delivery(bars, None)
        assert out is bars
        empty = pd.DataFrame()
        assert bhavcopy.join_delivery(empty, None) is empty
