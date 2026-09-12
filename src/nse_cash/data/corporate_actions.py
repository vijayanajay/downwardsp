"""NSE Corporate Actions ingestion (Phase 2.4).

Endpoint: https://www.nseindia.com/api/corporates-corporateActions?index=equities
Parses split / bonus ratios from `sm_category`/`smStt`+`smRej`-style purpose
strings, e.g.:
  SPLIT FROM RS 10 TO RS 2        -> ratio A=4, B=1  (A = old/new - 1)
  BONUS 1:1 / BONUS 3:1           -> ratio A=1, B=1  (A new : B held)
Rights and dividends are ingested for informational logging only (per plan).
"""

from __future__ import annotations

import logging
import re
from datetime import date as Date
from typing import Optional

import pandas as pd

from nse_cash.data.fetcher import NSEHttpClient

log = logging.getLogger("nse_cash.corporate_actions")

CA_URL = "https://www.nseindia.com/api/corporates-corporateActions?index=equities"

# Covers: "SPLIT FROM RS 10 TO RS 2", "SPLIT OF RS.5 TO RS.1",
#         "Face Value Split (Sub-Division) - From Rs10/- Per Share To Re 1/- Per Share",
#         "SUB-DIVISION OF EQUITY SHARES FROM RS. 10/- EACH TO RE. 1/- EACH"
_SPLIT_RE = re.compile(
    r"(?:SPLIT|SUB-DIVISION|SUB DIVISION).*?(?:\bFROM\s+)?(?:RS\.?|RE\.?)\s*([0-9.]+)(?:/-)?"
    r".*?\bTO\s+(?:RS\.?|RE\.?)\s*([0-9.]+)(?:/-)?",
    flags=re.IGNORECASE | re.DOTALL)

_BONUS_RE = re.compile(
    r"BONUS\s*(?:ISSUE\s*)?(?:IN THE RATIO OF\s*)?([0-9.]+)\s*(?::|FOR)\s*([0-9.]+)",
    flags=re.IGNORECASE)

_NON_EQUITY_RE = re.compile(
    r"\b(?:NCRPS|PREFERENCE|DEBENTURE|WARRANT|NOTE)\b",
    flags=re.IGNORECASE)


def _parse_split(purpose: str) -> Optional[tuple[float, float]]:
    """Split purpose -> (A, B) with A = old FV / new FV - 1, B = 1."""
    m = _SPLIT_RE.search(purpose)
    if not m:
        return None
    old_fv, new_fv = float(m.group(1)), float(m.group(2))
    if new_fv <= 0 or old_fv <= new_fv:
        return None
    return (old_fv / new_fv - 1.0, 1.0)


def _parse_bonus(purpose: str) -> Optional[tuple[float, float]]:
    """'BONUS 3:1' -> (3.0, 1.0) meaning A new shares : B held."""
    if _NON_EQUITY_RE.search(purpose):
        return None  # Preference shares / debentures are not equity bonus issues
    m = _BONUS_RE.search(purpose)
    if not m:
        return None
    return (float(m.group(1)), float(m.group(2)))


def classify_action(purpose: str) -> tuple[Optional[str], float, float]:
    """Return (action_type, ratio_a, ratio_b) for a purpose string."""
    p = (purpose or "").upper()
    split = _parse_split(p)
    if split:
        return "SPLIT", split[0], split[1]
    bonus = _parse_bonus(p)
    if bonus:
        return "BONUS", bonus[0], bonus[1]
    if "RIGHTS" in p:
        return "RIGHTS", 1.0, 1.0
    if "DIVIDEND" in p or "INTERIM" in p:
        return "DIVIDEND", 1.0, 1.0
    return None, 1.0, 1.0


def parse_corporate_actions(payload: dict | list,
                            from_date: Optional[Date] = None,
                            to_date: Optional[Date] = None) -> pd.DataFrame:
    """Normalize the NSE corporate-actions JSON into a DataFrame."""
    records = payload if isinstance(payload, list) else payload.get("data", [])
    rows = []
    for rec in records:
        symbol = str(rec.get("symbol") or rec.get("Symbol") or "").strip().upper()
        if not symbol:
            continue
        ex_raw = rec.get("exDate") or rec.get("ex_date")
        if not ex_raw:
            continue
        try:
            ex_date = pd.to_datetime(ex_raw).date()
        except (ValueError, TypeError):
            continue
        if from_date and ex_date < from_date:
            continue
        if to_date and ex_date > to_date:
            continue
        purpose = str(rec.get("subject") or rec.get("purpose") or "").strip()
        series = rec.get("series") or rec.get("Series")
        action_type, ratio_a, ratio_b = classify_action(purpose)
        rows.append({
            "symbol": symbol,
            "series": series,
            "ex_date": ex_date,
            "purpose": purpose,
            "action_type": action_type,
            "ratio_a": ratio_a,
            "ratio_b": ratio_b,
        })
    df = pd.DataFrame(rows, columns=["symbol", "series", "ex_date", "purpose",
                                     "action_type", "ratio_a", "ratio_b"])
    if not df.empty:
        df = df.drop_duplicates(subset=["symbol", "ex_date", "purpose"])
    return df


def fetch_corporate_actions(client: NSEHttpClient,
                            from_date: Optional[Date] = None,
                            to_date: Optional[Date] = None) -> pd.DataFrame | None:
    """Fetch the corporate-actions JSON (cookie bootstrapped) and normalize it.

    With from_date + to_date the API returns historical actions in that
    window (used for multi-year backfills); without them it returns the
    ~20 upcoming actions.
    """
    url = CA_URL
    if from_date and to_date:
        url += f"&from_date={from_date:%d-%m-%Y}&to_date={to_date:%d-%m-%Y}"
    try:
        payload = client.get_json(url, bootstrap=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("corporate actions fetch failed: %s", exc)
        return None
    df = parse_corporate_actions(payload, from_date, to_date)
    log.info("corporate actions: %d records (%d split/bonus)",
             len(df), int(df["action_type"].isin(["SPLIT", "BONUS"]).sum()) if not df.empty else 0)
    return df
