"""Daily cash Bhavcopy ingestion engine (Phase 2.2).

URL patterns tried in order (first success wins):
  1. Legacy:  archives.nseindia.com/content/historical/EQUITIES/{YYYY}/{MMM}/
              cm{DD}{MMM}{YYYY}bhav.csv.zip                (discontinued 2024-07-08)
  2. UDiFF:   nsearchives.nseindia.com/content/cm/
              BhavCopy_NSE_CM_0_0_0_{YYYYMMDD}_F_0000.csv.zip
  3. PR full: nsearchives.nseindia.com/archives/equities/bhavcopy/pr/PR{DDMMYY}.zip
              (contains sec_bhavdata_full_{DDMMYYYY}.csv; includes delivery qty)

All formats are normalized to the unified bar schema with turnover in absolute
rupees (legacy/PR sources report lakhs and are scaled by 1e5). Only `EQ`
series rows are kept — strictly cash equity.
"""

from __future__ import annotations

import io
import logging
import zipfile
from dataclasses import dataclass
from datetime import date as Date

import pandas as pd
import requests

from nse_cash.data.fetcher import NSEHttpClient

log = logging.getLogger("nse_cash.bhavcopy")


def legacy_url(d: Date) -> str:
    m = d.strftime("%b").upper()
    return (f"https://archives.nseindia.com/content/historical/EQUITIES/"
            f"{d:%Y}/{m}/cm{d:%d}{m}{d:%Y}bhav.csv.zip")


def udiff_url(d: Date) -> str:
    return (f"https://nsearchives.nseindia.com/content/cm/"
            f"BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip")


def sec_bhav_url(d: Date) -> str:
    return f"https://archives.nseindia.com/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv"


def pr_url(d: Date) -> str:
    return (f"https://nsearchives.nseindia.com/archives/equities/bhavcopy/pr/"
            f"PR{d:%d%m%y}.zip")


def legacy_cache_path(raw_dir, d: Date):
    m = d.strftime("%b").upper()
    return raw_dir / f"{d:%Y}" / f"{d:%m}" / f"cm{d:%d}{m}{d:%Y}bhav.csv.zip"


def udiff_cache_path(raw_dir, d: Date):
    return raw_dir / f"{d:%Y}" / f"{d:%m}" / f"BhavCopy_NSE_CM_{d:%Y%m%d}.zip"


def sec_bhav_cache_path(raw_dir, d: Date):
    return raw_dir / f"{d:%Y}" / f"{d:%m}" / f"sec_bhavdata_full_{d:%d%m%Y}.csv"


def pr_cache_path(raw_dir, d: Date):
    return raw_dir / f"{d:%Y}" / f"{d:%m}" / f"PR{d:%d%m%y}.zip"


@dataclass
class BhavcopyResult:
    date: Date
    df: pd.DataFrame
    source: str  # 'legacy' | 'udiff' | 'pr'


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _load_zip_csv(raw: bytes) -> pd.DataFrame:
    """In-memory decompression of the zip -> first member parsed as CSV."""
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        name = zf.namelist()[0]
        text = zf.read(name).decode("utf-8", errors="replace")
    return pd.read_csv(io.StringIO(text))


def _strip_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].astype(str).str.strip()
    return df


def _validate(df: pd.DataFrame, required: list[str], d: Date, source: str) -> pd.DataFrame:
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"{source} bhavcopy {d}: missing columns {missing}")
    if df.empty:
        raise ValueError(f"{source} bhavcopy {d}: empty file")
    return df


def parse_legacy(raw: bytes, d: Date) -> pd.DataFrame:
    df = _strip_df(_load_zip_csv(raw))
    df = _validate(df, ["SYMBOL", "SERIES", "OPEN", "HIGH", "LOW", "CLOSE",
                        "LAST", "TOTTRDQTY", "TOTTRDVAL", "TIMESTAMP"], d, "legacy")
    df = df[df["SERIES"] == "EQ"]
    ts = pd.to_datetime(df["TIMESTAMP"], format="%d-%b-%Y", errors="coerce")
    if ts.isna().all():
        raise ValueError(f"legacy bhavcopy {d}: unparseable TIMESTAMP column")
    out = pd.DataFrame({
        "symbol": df["SYMBOL"].str.upper(),
        "date": ts.dt.date,
        "series": df["SERIES"],
        "open": pd.to_numeric(df["OPEN"], errors="coerce"),
        "high": pd.to_numeric(df["HIGH"], errors="coerce"),
        "low": pd.to_numeric(df["LOW"], errors="coerce"),
        "close": pd.to_numeric(df["CLOSE"], errors="coerce"),
        "last": pd.to_numeric(df["LAST"], errors="coerce"),
        "volume": pd.to_numeric(df["TOTTRDQTY"], errors="coerce").fillna(0).astype("int64"),
        # legacy TOTTRDVAL is reported in absolute rupees
        "turnover": pd.to_numeric(df["TOTTRDVAL"], errors="coerce"),
        "deliverable_qty": pd.NA,
        "delivery_pct": pd.NA,
    })
    return out


def parse_udiff(raw: bytes, d: Date) -> pd.DataFrame:
    df = _strip_df(_load_zip_csv(raw))
    df = _validate(df, ["TckrSymb", "SctySrs", "TradDt", "OpnPric", "HghPric",
                        "LwPric", "ClsPric", "LastPric", "TtlTradgVol",
                        "TtlTrfVal"], d, "UDiFF")
    df = df[df["SctySrs"] == "EQ"]
    ts = pd.to_datetime(df["TradDt"], format="%Y-%m-%d", errors="coerce")
    out = pd.DataFrame({
        "symbol": df["TckrSymb"].str.upper(),
        "date": ts.dt.date,
        "series": df["SctySrs"],
        "open": pd.to_numeric(df["OpnPric"], errors="coerce"),
        "high": pd.to_numeric(df["HghPric"], errors="coerce"),
        "low": pd.to_numeric(df["LwPric"], errors="coerce"),
        "close": pd.to_numeric(df["ClsPric"], errors="coerce"),
        "last": pd.to_numeric(df["LastPric"], errors="coerce"),
        "volume": pd.to_numeric(df["TtlTradgVol"], errors="coerce").fillna(0).astype("int64"),
        # UDiFF TtlTrfVal is reported in absolute rupees
        "turnover": pd.to_numeric(df["TtlTrfVal"], errors="coerce"),
        "deliverable_qty": pd.NA,
        "delivery_pct": pd.NA,
    })
    return out


def parse_pr(raw: bytes, d: Date) -> pd.DataFrame:
    """Parse sec_bhavdata_full (from raw CSV bytes or from inside a zip archive)."""
    if raw[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            target_name = None
            for n in csv_names:
                if "sec_bhavdata_full" in n.lower():
                    target_name = n
                    break
            if not target_name:
                for n in csv_names:
                    sample = zf.read(n)[:1000].decode("utf-8", errors="replace")
                    if "OPEN_PRICE" in sample and "CLOSE_PRICE" in sample:
                        target_name = n
                        break
            if not target_name:
                raise ValueError(f"PR zip {d}: no matching bhavdata csv found in {csv_names}")
            text = zf.read(target_name).decode("utf-8", errors="replace")
    else:
        text = raw.decode("utf-8", errors="replace")
    df = _strip_df(pd.read_csv(io.StringIO(text)))
    df = _validate(df, ["SYMBOL", "SERIES", "DATE1", "OPEN_PRICE", "HIGH_PRICE",
                        "LOW_PRICE", "LAST_PRICE", "CLOSE_PRICE", "TTL_TRD_QNTY",
                        "TURNOVER_LACS", "DELIV_QTY", "DELIV_PER"], d, "PR")
    df = df[df["SERIES"] == "EQ"]
    ts = pd.to_datetime(df["DATE1"], format="%d-%b-%Y", errors="coerce")
    if ts.isna().all():
        ts = pd.to_datetime(df["DATE1"], dayfirst=True, errors="coerce")
    out = pd.DataFrame({
        "symbol": df["SYMBOL"].str.upper(),
        "date": ts.dt.date,
        "series": df["SERIES"],
        "open": pd.to_numeric(df["OPEN_PRICE"], errors="coerce"),
        "high": pd.to_numeric(df["HIGH_PRICE"], errors="coerce"),
        "low": pd.to_numeric(df["LOW_PRICE"], errors="coerce"),
        "close": pd.to_numeric(df["CLOSE_PRICE"], errors="coerce"),
        "last": pd.to_numeric(df["LAST_PRICE"], errors="coerce"),
        "volume": pd.to_numeric(df["TTL_TRD_QNTY"], errors="coerce").fillna(0).astype("int64"),
        # TURNOVER_LACS is reported in lakhs of rupees
        "turnover": pd.to_numeric(df["TURNOVER_LACS"], errors="coerce") * 1e5,
        "deliverable_qty": pd.to_numeric(df["DELIV_QTY"], errors="coerce").astype("Int64"),
        "delivery_pct": pd.to_numeric(df["DELIV_PER"], errors="coerce"),
    })
    return out


# ---------------------------------------------------------------------------
# Fetch orchestration
# ---------------------------------------------------------------------------

_LEGACY_CUTOFF = Date(2024, 7, 8)


def fetch_bhavcopy(client: NSEHttpClient, d: Date, raw_dir) -> BhavcopyResult | None:
    """Download + parse the bhavcopy for `d`. Returns None on market holidays."""
    if d <= _LEGACY_CUTOFF:
        candidates = [
            ("legacy", legacy_url(d), legacy_cache_path(raw_dir, d), parse_legacy),
            ("sec_bhav", sec_bhav_url(d), sec_bhav_cache_path(raw_dir, d), parse_pr),
            ("pr", pr_url(d), pr_cache_path(raw_dir, d), parse_pr),
        ]
    else:
        candidates = [
            ("udiff", udiff_url(d), udiff_cache_path(raw_dir, d), parse_udiff),
            ("sec_bhav", sec_bhav_url(d), sec_bhav_cache_path(raw_dir, d), parse_pr),
            ("pr", pr_url(d), pr_cache_path(raw_dir, d), parse_pr),
        ]
    for source, url, cache, parser in candidates:
        try:
            raw = client.get_bytes(url, cache_path=cache)
            df = parser(raw, d)
            df = df.dropna(subset=["close", "open", "high", "low"])
            if df.empty:
                raise ValueError(f"{source} bhavcopy {d}: no EQ rows")
            log.info("bhavcopy %s: %d EQ rows via %s", d, len(df), source)
            return BhavcopyResult(date=d, df=df, source=source)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status == 404:
                continue  # try next format / likely a holiday
            log.warning("bhavcopy %s via %s failed: %s", d, source, exc)
        except (ValueError, zipfile.BadZipFile, KeyError) as exc:
            log.warning("bhavcopy %s via %s invalid: %s", d, source, exc)
    log.info("bhavcopy %s: unavailable in all formats (holiday?)", d)
    return None


def join_delivery(bars: pd.DataFrame, delivery: pd.DataFrame) -> pd.DataFrame:
    """Left-join MTO delivery onto bars on (symbol, date); PR values win."""
    if bars.empty or delivery is None or delivery.empty:
        return bars
    d = delivery.rename(columns={
        "deliverable_qty": "_mto_deliv", "delivery_pct": "_mto_pct"})
    merged = bars.merge(d[["symbol", "date", "_mto_deliv", "_mto_pct"]],
                        on=["symbol", "date"], how="left")
    merged["deliverable_qty"] = merged["deliverable_qty"].fillna(merged["_mto_deliv"])
    merged["delivery_pct"] = merged["delivery_pct"].fillna(merged["_mto_pct"])
    return merged.drop(columns=["_mto_deliv", "_mto_pct"])
