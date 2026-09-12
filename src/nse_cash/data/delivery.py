"""Security-wise Delivery report (MTO) ingestion (Phase 2.3).

URL: https://archives.nseindia.com/archives/equities/mto/MTO_{DDMMYYYY}.DAT
(.csv also accepted). Record Type 20 rows carry security-wise client delivery:
  20,<sr_no>,<SYMBOL>,<SERIES>,<qty_traded>,<deliverable_qty>,<delivery_pct>
Rows are filtered to `EQ` series and joined with daily bhavcopy on
(symbol, date) into the unified daily table.
"""

from __future__ import annotations

import logging
from datetime import date as Date

import pandas as pd
import requests

from nse_cash.data.fetcher import NSEHttpClient

log = logging.getLogger("nse_cash.delivery")

MTO_URL = "https://archives.nseindia.com/archives/equities/mto/MTO_{d:%d%m%Y}.DAT"
MTO_URL_CSV = "https://archives.nseindia.com/archives/equities/mto/MTO_{d:%d%m%Y}.csv"


def mto_cache_path(raw_dir, d: Date):
    return raw_dir / f"{d:%Y}" / f"{d:%m}" / f"MTO_{d:%d%m%Y}.DAT"


def parse_mto(text: str) -> pd.DataFrame:
    """Parse MTO DAT/CSV text into normalized delivery records (EQ only)."""
    rows: list[list[str]] = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 7 and parts[0] == "20":
            rows.append(parts[:7])
    if not rows:
        raise ValueError("MTO: no record-type-20 rows found")
    df = pd.DataFrame(rows, columns=["record", "sr_no", "symbol", "series",
                                     "traded_qty", "deliverable_qty", "delivery_pct"])
    df = df[df["series"] == "EQ"]
    if df.empty:
        raise ValueError("MTO: no EQ-series rows found")
    out = pd.DataFrame({
        "symbol": df["symbol"].str.upper(),
        "series": df["series"],
        "traded_qty": pd.to_numeric(df["traded_qty"], errors="coerce").fillna(0).astype("int64"),
        "deliverable_qty": pd.to_numeric(df["deliverable_qty"], errors="coerce").fillna(0).astype("int64"),
        "delivery_pct": pd.to_numeric(df["delivery_pct"], errors="coerce"),
    })
    return out


def fetch_delivery(client: NSEHttpClient, d: Date, raw_dir) -> pd.DataFrame | None:
    """Download + parse MTO for `d`; None if unavailable (holiday/404)."""
    cache = mto_cache_path(raw_dir, d)
    for url in (MTO_URL.format(d=d), MTO_URL_CSV.format(d=d)):
        try:
            raw = client.get_bytes(url, cache_path=cache)
            text = raw.decode("utf-8", errors="replace")
            df = parse_mto(text)
            df["date"] = d
            log.info("MTO %s: %d EQ rows", d, len(df))
            return df
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                continue
            log.warning("MTO %s failed: %s", d, exc)
        except ValueError as exc:
            log.warning("MTO %s invalid: %s", d, exc)
    log.info("MTO %s: unavailable (holiday?)", d)
    return None
