"""NIFTY 50 & NIFTY 500 benchmark ingestion (Phase 2.5).

Sources, in order:
  1. NSE index daily report CSV archives (ind_close_all_{DDMMYYYY}.csv)
  2. Local Parquet cache produced by scripts/download_data.py
     (data/ohlcv/_nifty.parquet for NIFTY 50)

The NIFTY 500 index is used as the RS/breadth denominator; NIFTY 50 drives the
20-EMA regime gate.
"""

from __future__ import annotations

import logging
from datetime import date as Date
from pathlib import Path

import pandas as pd

from nse_cash.data.fetcher import NSEHttpClient

log = logging.getLogger("nse_cash.indices")

IND_CLOSE_URL = ("https://nsearchives.nseindia.com/content/indices/"
                 "ind_close_all_{d:%d%m%Y}.csv")

NIFTY50_NAME = "NIFTY 50"
NIFTY500_NAME = "NIFTY 500"

# Canonical alias mapping covering historical S&P/CRISIL co-branding (pre-Nov 2015)
# and reporting variations across NSE index archives.
INDEX_ALIASES: dict[str, str] = {
    "NIFTY 50": NIFTY50_NAME,
    "NIFTY": NIFTY50_NAME,
    "CNX NIFTY": NIFTY50_NAME,
    "S&P CNX NIFTY": NIFTY50_NAME,
    "NIFTY50": NIFTY50_NAME,
    "NIFTY 500": NIFTY500_NAME,
    "CNX 500": NIFTY500_NAME,
    "S&P CNX 500": NIFTY500_NAME,
    "NIFTY500": NIFTY500_NAME,
}

IND_CLOSE_URL_TEMPLATES = [
    "https://nsearchives.nseindia.com/content/indices/ind_close_all_{d:%d%m%Y}.csv",
    "https://archives.nseindia.com/content/indices/ind_close_all_{d:%d%m%Y}.csv",
]


import io


def _to_float(val) -> float:
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return float("nan")


def _parse_ind_close(text: str, d: Date) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(text))
    df.columns = [c.strip().upper().replace(" ", "_") for c in df.columns]
    idx_col = next((c for c in ("INDEX_NAME", "INDEX") if c in df.columns), None)
    if not idx_col:
        raise ValueError("ind_close_all: missing INDEX_NAME column")

    rows = []
    for _, r in df.iterrows():
        raw_val = r.get(idx_col)
        if pd.isna(raw_val):
            continue
        raw_name = " ".join(str(raw_val).strip().upper().split())
        norm_name = INDEX_ALIASES.get(raw_name)
        if not norm_name:
            continue

        open_val = _to_float(r.get("OPEN_INDEX_VALUE", r.get("OPEN_INDEX_VAL", r.get("OPEN", r.get("OPEN_PRICE")))))
        high_val = _to_float(r.get("HIGH_INDEX_VALUE", r.get("HIGH_INDEX_VAL", r.get("HIGH", r.get("HIGH_PRICE")))))
        low_val = _to_float(r.get("LOW_INDEX_VALUE", r.get("LOW_INDEX_VAL", r.get("LOW", r.get("LOW_PRICE")))))
        close_val = _to_float(r.get("CLOSING_INDEX_VALUE", r.get("CLOSE_INDEX_VAL", r.get("CLOSE", r.get("CLOSE_PRICE")))))
        vol_val = _to_float(r.get("VOLUME", r.get("TOTAL_TRADED_VOLUME", r.get("TRADED_QTY", 0.0))) or 0.0)

        rows.append({
            "index_name": norm_name,
            "date": d,
            "open": open_val,
            "high": high_val,
            "low": low_val,
            "close": close_val,
            "volume": vol_val,
        })
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.drop_duplicates(subset=["index_name", "date"])
    return out


def fetch_indices_for_date(client: NSEHttpClient, d: Date, raw_dir) -> pd.DataFrame | None:
    """Fetch NIFTY 50/500 closes for `d` from the index daily report (with mirror fallback)."""
    cache = Path(raw_dir) / f"{d:%Y}" / f"{d:%m}" / f"ind_close_all_{d:%d%m%Y}.csv"
    for template in IND_CLOSE_URL_TEMPLATES:
        try:
            raw = client.get_bytes(template.format(d=d), cache_path=cache, bootstrap=True)
            text = raw.decode("utf-8", errors="replace")
            df = _parse_ind_close(text, d)
            if df.empty:
                continue
            log.info("indices %s: %d rows", d, len(df))
            return df
        except Exception as exc:  # noqa: BLE001
            log.debug("index report %s via %s unavailable: %s", d, template, exc)
    log.info("index report %s unavailable in all locations", d)
    return None


def ingest_from_local_cache(store, ohlcv_dir: Path) -> int:
    """Seed market_indices from scripts/download_data.py outputs if present."""
    total = 0
    candidates = [
        ("_nifty.parquet", NIFTY50_NAME),
        ("_nifty500.parquet", NIFTY500_NAME),
        ("_nifty_500.parquet", NIFTY500_NAME),
    ]
    for filename, norm_name in candidates:
        path = Path(ohlcv_dir) / filename
        if not path.exists():
            continue
        try:
            df = pd.read_parquet(path)
            df = df.reset_index().rename(columns=str.lower)
            if "date" not in df.columns:
                continue
            out = pd.DataFrame({
                "index_name": norm_name,
                "date": pd.to_datetime(df["date"]).dt.date,
                "open": df.get("open"),
                "high": df.get("high"),
                "low": df.get("low"),
                "close": df.get("close"),
                "volume": df.get("volume", 0.0),
            }).dropna(subset=["close"])
            if not out.empty:
                n = store.upsert_market_indices(out)
                total += n
                log.info("market_indices seeded from %s: %d %s rows", filename, n, norm_name)
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to seed index from %s: %s", path, exc)
    return total
