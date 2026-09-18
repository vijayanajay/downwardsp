"""Step 2.5 — Seed deep NIFTY 50 / NIFTY 500 index history into local cache.

The NSE `ind_close_all_*.csv` daily reports only reach back to Feb 2012, but
the Phase 6 in-sample window starts 2010-01-01 — the regime gate (NIFTY 50
20-EMA) and breadth denominator (NIFTY 500) need index closes from day one.
yfinance serves ^NSEI and ^CRSLDX back to 2007.

Writes data/ohlcv/_nifty.parquet and data/ohlcv/_nifty500.parquet (the same
files scripts/download_data.py produces), then `nse-cash sync` (or any run of
run_sync) upserts them into market_indices via ingest_from_local_cache.

Usage:
    .venv/Scripts/python.exe scripts/seed_index_history.py
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
OHLCV_DIR = ROOT / "data" / "ohlcv"

INDICES = {
    "_nifty.parquet": "^NSEI",      # NIFTY 50
    "_nifty500.parquet": "^CRSLDX",  # NIFTY 500
}


def main() -> None:
    OHLCV_DIR.mkdir(parents=True, exist_ok=True)
    for filename, ticker in INDICES.items():
        df = yf.download(ticker, start="2007-01-01", auto_adjust=True, progress=False)
        if df is None or df.empty:
            print(f"[!] {ticker}: no data returned")
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.reset_index()
        out = df.rename(columns=str.lower)[["date", "open", "high", "low", "close", "volume"]]
        out["date"] = pd.to_datetime(out["date"]).dt.date
        out = out.dropna(subset=["close"])
        path = OHLCV_DIR / filename
        out.to_parquet(path, index=False)
        print(f"[ok] {filename}: {len(out):,} rows  {out['date'].min()} -> {out['date'].max()}")
        print(f"     upsert with: `.venv/Scripts/nse-cash.exe sync --from 2010-01-01 --to 2026-09-18`"
              " (sync re-runs ingest_from_local_cache) or any future sync call")


if __name__ == "__main__":
    main()
