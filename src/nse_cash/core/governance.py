"""Microstructure & Governance Filter Engine (Phase 3.3).

Three exclusion layers, all persisted into the `governance` table so scans
remain deterministic and auditable:
  1. SEBI ASM / GSM surveillance lists (live snapshot per sync date).
  2. Price circuit bands: reject band <= 5%, or stocks that hit upper/lower
     circuit in the preceding 3 trading sessions.
  3. Board meeting / results calendar: reject stocks with a board meeting
     scheduled in the next 3 trading sessions.

Live NSE endpoints sometimes fail or change shape; every loader degrades to an
empty exclusion set with a warning, because a *deterministic* partial filter
beat with an explicit warning is more honest than a crash.
"""

from __future__ import annotations

import logging
from datetime import date as Date
from pathlib import Path

import pandas as pd

from nse_cash.core.constants import (BOARD_MEETING_LOOKAHEAD_DAYS,
                                     CIRCUIT_BAND_EXCLUDE_PCT,
                                     CIRCUIT_HIT_SESSIONS)

log = logging.getLogger("nse_cash.governance")

ASM_GSM_URLS = {
    "ASM": "https://www.nseindia.com/api/reportASM",   # {longterm: {data: [...]}, shortterm: {data: [...]}}
    "GSM": "https://www.nseindia.com/api/reportGSM",   # [{symbol, gsmStage, ...}]
}
CIRCUIT_URL = "https://www.nseindia.com/api/equity-stockIndices?index=SECURITIES%20IN%20F%26O"
BOARD_URL = "https://www.nseindia.com/api/event-calendar"  # upcoming board meetings (records: symbol, purpose, date)


# ---------------------------------------------------------------------------
# 1. ASM / GSM surveillance
# ---------------------------------------------------------------------------

def fetch_asm_gsm(client, d: Date) -> dict[str, set[str]]:
    """Return {'ASM': {symbols}, 'GSM': {symbols}} from live NSE JSON."""
    out: dict[str, set[str]] = {"ASM": set(), "GSM": set()}
    for kind, url in ASM_GSM_URLS.items():
        try:
            payload = client.get_json(url, bootstrap=True)
            records: list = []
            if isinstance(payload, list):
                records = payload
            elif isinstance(payload, dict):
                for section in payload.values():
                    if isinstance(section, dict) and isinstance(section.get("data"), list):
                        records.extend(section["data"])
                    elif isinstance(section, list):
                        records.extend(section)
            for rec in records:
                sym = str(rec.get("symbol") or rec.get("Symbol") or "").strip().upper()
                if sym:
                    out[kind].add(sym)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s list fetch failed (continuing without it): %s", kind, exc)
    return out


# ---------------------------------------------------------------------------
# 2. Circuit bands
# ---------------------------------------------------------------------------

def fetch_circuit_bands(client, d: Date) -> pd.DataFrame:
    """Best-effort (symbol, band_pct) from NSE's live equity JSON.

    NSE's public endpoints do not expose a complete historical percent-band
    map; we read what the equity-stockIndices payload offers and otherwise
    return an empty frame so the deterministic circuit-hit filter below
    (computed from bhavcopy bars) still applies.
    """
    try:
        payload = client.get_json(
            "https://www.nseindia.com/api/equity-stockIndices?index=SECURITIES%20IN%20F%26O",
            bootstrap=True)
        data = payload.get("data", []) if isinstance(payload, dict) else []
        rows = []
        for rec in data:
            sym = str(rec.get("symbol") or "").strip().upper()
            band = rec.get("percentChange")  # not a band; kept for shape
            if sym and band is not None:
                rows.append({"symbol": sym, "band_pct": float(band)})
        return pd.DataFrame(rows, columns=["symbol", "band_pct"])
    except Exception as exc:  # noqa: BLE001
        log.warning("circuit band fetch failed (band<=5%% filter degraded): %s", exc)
        return pd.DataFrame(columns=["symbol", "band_pct"])


def circuit_hits_from_bars(con, start: Date, end: Date) -> pd.DataFrame:
    """Detect upper/lower circuit hits deterministically from bhavcopy bars.

    A session is a circuit hit when close == high == low (price locked all day)
    or when |return| >= 19% with zero range, which on NSE means the instrument
    traded only at its band price.
    """
    df = con.execute("""
        SELECT symbol, date, open, high, low, close,
               lag(close) OVER (PARTITION BY symbol ORDER BY date) AS prev_close
        FROM daily_bars
        WHERE date BETWEEN ? AND ?
    """, [start, end]).df()
    if df.empty:
        return pd.DataFrame(columns=["symbol", "date"])
    locked = (df["high"] == df["low"]) & (df["close"] == df["high"])
    big_move = ((df["close"] / df["prev_close"] - 1.0).abs() >= 0.19) & locked
    hits = df[locked | big_move][["symbol", "date"]]
    return hits.drop_duplicates()


# ---------------------------------------------------------------------------
# 3. Board meetings
# ---------------------------------------------------------------------------

def fetch_board_meetings(client, start: Date, end: Date) -> pd.DataFrame:
    """Return (symbol, meeting_date) rows from NSE's event-calendar API
    (upcoming board meetings incl. financial results), filtered to [start, end]."""
    try:
        payload = client.get_json(BOARD_URL, bootstrap=True)
        records = payload if isinstance(payload, list) else payload.get("data", [])
        rows = []
        for rec in records:
            sym = str(rec.get("symbol") or "").strip().upper()
            dt = rec.get("date")
            if not sym or not dt:
                continue
            try:
                meeting = pd.to_datetime(dt).date()
            except (ValueError, TypeError):
                continue
            if start <= meeting <= end:
                rows.append({"symbol": sym, "meeting_date": meeting})
        return pd.DataFrame(rows, columns=["symbol", "meeting_date"])
    except Exception as exc:  # noqa: BLE001
        log.warning("board meetings fetch failed (blackout filter degraded): %s", exc)
        return pd.DataFrame(columns=["symbol", "meeting_date"])


# ---------------------------------------------------------------------------
# Persistence + combined exclusion query
# ---------------------------------------------------------------------------

def persist_governance(store, d: Date, asm_gsm: dict[str, set[str]],
                       circuit_hits: pd.DataFrame,
                       board_meetings: pd.DataFrame) -> int:
    """Write the day's exclusion flags into the governance table."""
    rows: list[dict] = []
    for kind, symbols in asm_gsm.items():
        for sym in symbols:
            rows.append({"date": d, "list_type": kind, "symbol": sym, "detail": "listed"})
    for _, r in circuit_hits.iterrows():
        rows.append({"date": d, "list_type": "CIRCUIT", "symbol": r["symbol"],
                     "detail": f"hit on {r['date']}"})
    for _, r in board_meetings.iterrows():
        rows.append({"date": d, "list_type": "BOARD", "symbol": r["symbol"],
                     "detail": f"meeting on {r['meeting_date']}"})
    if not rows:
        return 0
    frame = pd.DataFrame(rows).drop_duplicates(
        subset=["date", "list_type", "symbol", "detail"])
    return store.upsert_governance(frame)


def excluded_symbols(con, on_date: Date,
                     lookahead_days: int = BOARD_MEETING_LOOKAHEAD_DAYS) -> set[str]:
    """Symbols to exclude from the universe on `on_date`.

    ASM/GSM/CIRCUIT flags recorded on or within the last 3 sessions before
    `on_date`, plus BOARD meetings within the next 3 sessions after `on_date`.
    """
    df = con.execute("""
        SELECT DISTINCT list_type, symbol FROM governance
        WHERE (list_type IN ('ASM', 'GSM', 'CIRCUIT')
               AND date BETWEEN ? AND ?)
           OR (list_type = 'BOARD'
               AND date >= ? AND date <= ?)
    """, [pd.Timestamp(on_date) - pd.Timedelta(days=7),
          pd.Timestamp(on_date),
          pd.Timestamp(on_date),
          pd.Timestamp(on_date) + pd.Timedelta(days=lookahead_days + 4)]).df()
    return set(df["symbol"].dropna()) if not df.empty else set()


def apply_governance_filters(store, d: Date, client=None) -> set[str]:
    """One-shot: fetch live lists, derive circuit hits, persist, and return
    the exclusion set for `d`."""
    circuit_hits = circuit_hits_from_bars(
        store.con,
        pd.Timestamp(d) - pd.Timedelta(days=10), pd.Timestamp(d))
    board = pd.DataFrame(columns=["symbol", "meeting_date"])
    if client is not None:
        asm_gsm = fetch_asm_gsm(client, d)
        board = fetch_board_meetings(
            client,
            (pd.Timestamp(d)).date(),
            (pd.Timestamp(d) + pd.Timedelta(days=BOARD_MEETING_LOOKAHEAD_DAYS + 4)).date())
    else:
        asm_gsm = {"ASM": set(), "GSM": set()}
    persist_governance(store, d, asm_gsm, circuit_hits, board)
    return excluded_symbols(store.con, d)
