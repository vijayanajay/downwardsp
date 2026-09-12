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


import io


def _to_float(val) -> float:
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return float("nan")


def _parse_ind_close(text: str, d: Date) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(text))
    df.columns = [c.strip().upper().replace(" ", "_") for c in df.columns]
    if "INDEX_NAME" not in df.columns:
        raise ValueError("ind_close_all: missing INDEX_NAME column")
    want = {NIFTY50_NAME.upper(), NIFTY500_NAME.upper()}
    df = df[df["INDEX_NAME"].str.upper().isin(want)]
    rows = []
    for _, r in df.iterrows():
        raw_name = str(r["INDEX_NAME"]).strip().upper()
        norm_name = NIFTY50_NAME if raw_name == "NIFTY 50" else NIFTY500_NAME
        rows.append({
            "index_name": norm_name,
            "date": d,
            "open": _to_float(r.get("OPEN_INDEX_VALUE", r.get("OPEN_INDEX_VAL", r.get("OPEN")))),
            "high": _to_float(r.get("HIGH_INDEX_VALUE", r.get("HIGH_INDEX_VAL", r.get("HIGH")))),
            "low": _to_float(r.get("LOW_INDEX_VALUE", r.get("LOW_INDEX_VAL", r.get("LOW")))),
            "close": _to_float(r.get("CLOSING_INDEX_VALUE", r.get("CLOSE_INDEX_VAL", r.get("CLOSE")))),
            "volume": _to_float(r.get("VOLUME", 0.0) or 0.0),
        })
    return pd.DataFrame(rows)


def fetch_indices_for_date(client: NSEHttpClient, d: Date, raw_dir) -> pd.DataFrame | None:
    """Fetch NIFTY 50/500 closes for `d` from the index daily report."""
    cache = Path(raw_dir) / f"{d:%Y}" / f"{d:%m}" / f"ind_close_all_{d:%d%m%Y}.csv"
    try:
        raw = client.get_bytes(IND_CLOSE_URL.format(d=d), cache_path=cache, bootstrap=True)
        text = raw.decode("utf-8", errors="replace")
        df = _parse_ind_close(text, d)
        if df.empty:
            return None
        log.info("indices %s: %d rows", d, len(df))
        return df
    except Exception as exc:  # noqa: BLE001
        log.info("index report %s unavailable: %s", d, exc)
        return None


def ingest_from_local_cache(store, ohlcv_dir: Path) -> int:
    """Seed market_indices from scripts/download_data.py outputs if present."""
    nifty_path = Path(ohlcv_dir) / "_nifty.parquet"
    if not nifty_path.exists():
        return 0
    df = pd.read_parquet(nifty_path)
    df = df.reset_index().rename(columns=str.lower)
    if "date" not in df.columns:
        return 0
    out = pd.DataFrame({
        "index_name": NIFTY50_NAME,
        "date": pd.to_datetime(df["date"]).dt.date,
        "open": df.get("open"),
        "high": df.get("high"),
        "low": df.get("low"),
        "close": df.get("close"),
        "volume": df.get("volume", 0.0),
    })
    n = store.upsert_market_indices(out)
    log.info("market_indices seeded from local cache: %d NIFTY 50 rows", n)
    return n
