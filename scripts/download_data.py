"""Step 1/3 — Download 5 years of adjusted daily OHLCV for the NIFTY 500 universe.

Universe priority:
  1. NSE official "NIFTY 500" constituents CSV (archives.nseindia.com)
  2. NIFTY 100 + Midcap 150 + Smallcap 250 (= 500) CSVs, same host
  3. Hardcoded NIFTY 50 fallback (smoke testing only)

Output:
  data/ohlcv/{SYMBOL}.parquet   per-ticker adjusted OHLCV (auto_adjust=True)
  data/ohlcv/_nifty.parquet     NIFTY 50 index (^NSEI) for regime conditioning
  data/universe_downloaded.csv  per-symbol download audit (bars, coverage, ok flag)

Usage:
  python scripts/download_data.py                # full universe
  python scripts/download_data.py --limit 50     # first N symbols (smoke test)
  python scripts/download_data.py --universe data/universe_nifty500.csv
"""

from __future__ import annotations

import argparse
import io
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
OHLCV_DIR = ROOT / "data" / "ohlcv"
YEARS = 5
BATCH_SIZE = 40
MIN_COVERAGE = 0.80  # drop stocks with < 80% of the longest bar count

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

NIFTY50_FALLBACK = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL",
    "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL",
    "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HEROMOTOCO",
    "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDUSINDBK", "INFY",
    "ITC", "JIOFIN", "JSWSTEEL", "KOTAKBANK", "LT",
    "M&M", "MARUTI", "NESTLEIND", "NTPC", "ONGC",
    "POWERGRID", "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN",
    "SUNPHARMA", "TATACONSUM", "TATAMOTORS", "TATASTEEL", "TCS",
    "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO",
]


def _get_csv(url: str) -> pd.DataFrame | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        if len(df) > 10:
            return df
    except Exception as exc:  # noqa: BLE001
        print(f"  ! {url} -> {type(exc).__name__}: {exc}")
    return None


def fetch_universe() -> tuple[list[str], str]:
    """Return (yahoo_symbols, source_label). Yahoo symbol = NSE symbol + '.NS'."""
    sym_col_opts = ["Symbol", "SYMBOL"]
    for url, label in [
        ("https://archives.nseindia.com/content/indices/ind_nifty500list.csv", "NIFTY 500 (official)"),
        ("https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv", "NIFTY 500 (official)"),
    ]:
        df = _get_csv(url)
        if df is not None:
            col = next((c for c in sym_col_opts if c in df.columns), None)
            if col:
                syms = df[col].astype(str).str.strip().unique().tolist()
                return [f"{s}.NS" for s in syms], label

    print("  NIFTY 500 CSV unavailable; trying NIFTY 100 + Midcap 150 + Smallcap 250 ...")
    combined: list[str] = []
    for name in ["ind_nifty100list", "ind_niftymidcap150list", "ind_niftysmallcap250list"]:
        df = _get_csv(f"https://archives.nseindia.com/content/indices/{name}.csv")
        if df is not None:
            col = next((c for c in sym_col_opts if c in df.columns), None)
            if col:
                combined += df[col].astype(str).str.strip().unique().tolist()
    if len(combined) >= 400:
        return [f"{s}.NS" for s in dict.fromkeys(combined)], "NIFTY 100+MC150+SC250"

    print("  !! All NSE endpoints failed — falling back to hardcoded NIFTY 50.")
    return [f"{s}.NS" for s in NIFTY50_FALLBACK], "NIFTY 50 fallback"


def _clean_frame(df: pd.DataFrame) -> pd.DataFrame | None:
    """Normalize a yfinance frame (raw or ticker-keyed MultiIndex) to standard OHLCV."""
    if df is None or len(df) == 0:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
    df = df.dropna(subset=["close"])
    df = df[df["close"] > 0]
    if len(df) == 0:
        return None
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df.index.name = "date"
    return df.astype("float64")


def download_one(symbol: str, start: str, end: str) -> pd.DataFrame | None:
    try:
        df = yf.download(symbol, start=start, end=end, auto_adjust=True,
                         progress=False, threads=False)
        return _clean_frame(df)
    except Exception:  # noqa: BLE001
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0, help="only first N symbols (smoke test)")
    ap.add_argument("--universe", type=str, default="", help="optional CSV with a Symbol/SYMBOL column")
    args = ap.parse_args()

    OHLCV_DIR.mkdir(parents=True, exist_ok=True)

    if args.universe:
        df = pd.read_csv(args.universe)
        col = next((c for c in ("Symbol", "SYMBOL", "symbol") if c in df.columns), None)
        symbols = [f"{s.strip()}.NS" for s in df[col].astype(str)]
        source = f"file:{args.universe}"
    else:
        symbols, source = fetch_universe()
    if args.limit:
        symbols = symbols[: args.limit]

    end = pd.Timestamp.today().strftime("%Y-%m-%d")
    start = (pd.Timestamp.today() - pd.DateOffset(years=YEARS)).strftime("%Y-%m-%d")
    print(f"Universe: {len(symbols)} symbols from [{source}] | {start} -> {end}")

    # --- index for regime conditioning -------------------------------------
    nifty_path = OHLCV_DIR / "_nifty.parquet"
    nifty = download_one("^NSEI", start, end)
    if nifty is not None:
        nifty.to_parquet(nifty_path)
        print(f"^NSEI saved: {len(nifty)} bars ({nifty.index.min().date()} .. {nifty.index.max().date()})")
    else:
        print("!! Could not download ^NSEI — regime analysis will be skipped.")

    # --- batched equity download -------------------------------------------
    audit: list[dict] = []
    batches = [symbols[i : i + BATCH_SIZE] for i in range(0, len(symbols), BATCH_SIZE)]
    t0 = time.time()
    for bi, batch in enumerate(batches, 1):
        try:
            raw = yf.download(batch, start=start, end=end, auto_adjust=True,
                              group_by="ticker", progress=False, threads=True)
        except Exception:  # noqa: BLE001
            raw = None

        for sym in batch:
            df = None
            if raw is not None and sym in getattr(raw, "columns", pd.Index([])).get_level_values(0):
                df = _clean_frame(raw[sym])
            if df is None:  # individual retry
                df = download_one(sym, start, end)
            if df is None:
                audit.append({"symbol": sym, "bars": 0, "first": "", "last": "", "ok": False})
                continue
            df.to_parquet(OHLCV_DIR / f"{sym.replace('.', '_')}.parquet")
            audit.append({"symbol": sym, "bars": len(df),
                          "first": str(df.index.min().date()), "last": str(df.index.max().date()),
                          "ok": True})
        print(f"  batch {bi}/{len(batches)} done ({time.time() - t0:.0f}s elapsed)")

    audit_df = pd.DataFrame(audit)
    max_bars = int(audit_df["bars"].max()) if len(audit_df) else 0
    audit_df["coverage"] = audit_df["bars"] / max_bars if max_bars else 0.0
    audit_df["ok"] = audit_df["ok"] & (audit_df["coverage"] >= MIN_COVERAGE)
    audit_df.to_csv(ROOT / "data" / "universe_downloaded.csv", index=False)

    good = int(audit_df["ok"].sum())
    print(f"\nDownloaded {len(audit_df)} symbols in {time.time() - t0:.0f}s")
    print(f"  usable (bars >= {MIN_COVERAGE:.0%} of {max_bars}): {good}")
    print(f"  dropped: {len(audit_df) - good}  -> data/universe_downloaded.csv")
    bad = audit_df[~audit_df["ok"]].sort_values("bars")
    if len(bad):
        print("  dropped symbols:", ", ".join(bad["symbol"].head(30).tolist()),
              "..." if len(bad) > 30 else "")
    if good < 100:
        print("\nWARNING: fewer than 100 usable symbols — check connectivity/universe source.")
        sys.exit(1)


if __name__ == "__main__":
    main()
