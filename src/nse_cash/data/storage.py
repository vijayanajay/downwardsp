"""Unified local storage layer (Phase 2.6).

DuckDB database at data/db/nse_market.duckdb with indexed tables:
daily_bars, corporate_actions, market_indices, pit_universe, governance.
Plus Parquet export partitioned by year under data/processed/.
"""

from __future__ import annotations

import logging
from pathlib import Path

import duckdb
import pandas as pd

from nse_cash.core.constants import (DUCKDB_CORPORATE_ACTIONS, DUCKDB_DAILY_BARS,
                                     DUCKDB_FEATURES, DUCKDB_GOVERNANCE,
                                     DUCKDB_MARKET_INDICES, DUCKDB_PIT_UNIVERSE,
                                     FEATURE_COLS)

log = logging.getLogger("nse_cash.storage")

_SCHEMA = {
    DUCKDB_DAILY_BARS: """
        CREATE TABLE IF NOT EXISTS daily_bars (
            symbol VARCHAR NOT NULL,
            date DATE NOT NULL,
            series VARCHAR DEFAULT 'EQ',
            open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, last DOUBLE,
            volume BIGINT, turnover DOUBLE,
            deliverable_qty BIGINT, delivery_pct DOUBLE,
            open_adj DOUBLE, high_adj DOUBLE, low_adj DOUBLE, close_adj DOUBLE,
            volume_adj DOUBLE, delivery_adj DOUBLE,
            PRIMARY KEY (symbol, date)
        )""",
    DUCKDB_CORPORATE_ACTIONS: """
        CREATE TABLE IF NOT EXISTS corporate_actions (
            symbol VARCHAR NOT NULL,
            ex_date DATE NOT NULL,
            purpose VARCHAR,
            action_type VARCHAR,
            ratio_a DOUBLE DEFAULT 1.0,
            ratio_b DOUBLE DEFAULT 1.0,
            adjustment_factor DOUBLE,
            PRIMARY KEY (symbol, ex_date, purpose)
        )""",
    DUCKDB_MARKET_INDICES: """
        CREATE TABLE IF NOT EXISTS market_indices (
            index_name VARCHAR NOT NULL,
            date DATE NOT NULL,
            open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE,
            PRIMARY KEY (index_name, date)
        )""",
    DUCKDB_PIT_UNIVERSE: """
        CREATE TABLE IF NOT EXISTS pit_universe (
            date DATE NOT NULL,
            symbol VARCHAR NOT NULL,
            adtv_90 DOUBLE,
            rank INTEGER,
            PRIMARY KEY (date, symbol)
        )""",
    DUCKDB_GOVERNANCE: """
        CREATE TABLE IF NOT EXISTS governance (
            date DATE NOT NULL,
            list_type VARCHAR NOT NULL,
            symbol VARCHAR,
            detail VARCHAR,
            PRIMARY KEY (date, list_type, symbol, detail)
        )""",
    DUCKDB_FEATURES: (
        """
        CREATE TABLE IF NOT EXISTS features (
            symbol VARCHAR NOT NULL,
            date DATE NOT NULL,
            """
        + ",\n            ".join(f"{c} DOUBLE" for c in FEATURE_COLS)
        + """,
            PRIMARY KEY (symbol, date)
        )"""
    ),
}

_BAR_COLS = ["symbol", "date", "series", "open", "high", "low", "close", "last",
             "volume", "turnover", "deliverable_qty", "delivery_pct",
             "open_adj", "high_adj", "low_adj", "close_adj",
             "volume_adj", "delivery_adj"]


class MarketStore:
    """DuckDB-backed market data store."""

    def __init__(self, db_path: Path, read_only: bool = False) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(str(self.db_path), read_only=read_only)
        if not read_only:
            self._ensure_schema()
            self._heal_features_schema()

    def _ensure_schema(self) -> None:
        for ddl in _SCHEMA.values():
            self.con.execute(ddl)
        try:
            self.con.execute("CREATE INDEX IF NOT EXISTS idx_bars_date ON daily_bars(date)")
            self.con.execute("CREATE INDEX IF NOT EXISTS idx_bars_symbol ON daily_bars(symbol)")
        except duckdb.Error:
            pass  # index already exists under a different definition

    # -- generic upsert helper ------------------------------------------------
    def _upsert(self, table: str, key_cols: list[str], df: pd.DataFrame,
                cols: list[str]) -> int:
        if df is None or df.empty:
            return 0
        frame = df.reindex(columns=cols)
        keys_on = " AND ".join(f"i.{k} = t.{k}" for k in key_cols)
        column_list = ", ".join(cols)
        self.con.execute("BEGIN")
        try:
            self.con.register("_upsert_in", frame)
            self.con.execute(
                f"DELETE FROM {table} t WHERE EXISTS "
                f"(SELECT 1 FROM _upsert_in i WHERE {keys_on})")
            self.con.execute(
                f"INSERT INTO {table} ({column_list}) SELECT {column_list} FROM _upsert_in")
            self.con.execute("COMMIT")
        except Exception:
            self.con.execute("ROLLBACK")
            raise
        finally:
            try:
                self.con.unregister("_upsert_in")
            except duckdb.Error:
                pass
        return len(frame)

    # -- table-specific upserts ----------------------------------------------
    def upsert_daily_bars(self, df: pd.DataFrame) -> int:
        """Insert-or-replace daily bars keyed on (symbol, date)."""
        return self._upsert(DUCKDB_DAILY_BARS, ["symbol", "date"], df, _BAR_COLS)

    def upsert_corporate_actions(self, df: pd.DataFrame) -> int:
        """Insert-or-replace corporate actions keyed on (symbol, ex_date, purpose)."""
        cols = ["symbol", "ex_date", "purpose", "action_type",
                "ratio_a", "ratio_b", "adjustment_factor"]
        return self._upsert(DUCKDB_CORPORATE_ACTIONS, ["symbol", "ex_date", "purpose"],
                            df, cols)

    def upsert_market_indices(self, df: pd.DataFrame) -> int:
        """Insert-or-replace index bars keyed on (index_name, date)."""
        cols = ["index_name", "date", "open", "high", "low", "close", "volume"]
        return self._upsert(DUCKDB_MARKET_INDICES, ["index_name", "date"], df, cols)

    def upsert_pit_universe(self, df: pd.DataFrame) -> int:
        """Insert-or-replace PIT universe membership keyed on (date, symbol)."""
        cols = ["date", "symbol", "adtv_90", "rank"]
        return self._upsert(DUCKDB_PIT_UNIVERSE, ["date", "symbol"], df, cols)

    def upsert_governance(self, df: pd.DataFrame) -> int:
        """Insert-or-replace governance flags keyed on (date, list_type, symbol, detail)."""
        cols = ["date", "list_type", "symbol", "detail"]
        return self._upsert(DUCKDB_GOVERNANCE, ["date", "list_type", "symbol", "detail"],
                            df, cols)

    def _heal_features_schema(self) -> None:
        """Recreate the derived features table if its columns are stale.

        Features are 100% recomputable from daily_bars, so a schema drift
        (e.g. a new FEATURE_COLS entry) is healed by drop + rebuild instead
        of failing every upsert.
        """
        try:
            cols = {r[0] for r in self.con.execute(
                f"SELECT column_name FROM information_schema.columns "
                f"WHERE table_name = '{DUCKDB_FEATURES}'").fetchall()}
        except duckdb.Error:
            return
        expected = {"symbol", "date", *FEATURE_COLS}
        if cols and cols != expected:
            log.warning("features schema drift (%s); recreating table",
                        sorted(expected ^ cols))
            self.con.execute(f"DROP TABLE IF EXISTS {DUCKDB_FEATURES}")
            self.con.execute(_SCHEMA[DUCKDB_FEATURES])

    def upsert_features(self, df: pd.DataFrame) -> int:
        """Insert-or-replace precomputed setup features keyed on (symbol, date)."""
        cols = ["symbol", "date", *FEATURE_COLS]
        return self._upsert(DUCKDB_FEATURES, ["symbol", "date"], df, cols)

    def latest_feature_date(self):
        row = self.con.execute("SELECT max(date) FROM features").fetchone()
        return row[0] if row and row[0] is not None else None

    # -- queries ---------------------------------------------------------------
    def trading_dates(self, start=None, end=None) -> list:
        """Distinct bhavcopy dates in [start, end], ascending."""
        q = "SELECT DISTINCT date FROM daily_bars"
        conditions, params = [], []
        if start is not None:
            conditions.append("date >= ?")
            params.append(pd.Timestamp(start).date())
        if end is not None:
            conditions.append("date <= ?")
            params.append(pd.Timestamp(end).date())
        if conditions:
            q += " WHERE " + " AND ".join(conditions)
        q += " ORDER BY date"
        rows = self.con.execute(q, params).fetchall()
        return [pd.Timestamp(r[0]).date() for r in rows]

    def latest_date(self):
        row = self.con.execute("SELECT max(date) FROM daily_bars").fetchone()
        return row[0] if row and row[0] is not None else None

    def date_row_counts(self) -> pd.DataFrame:
        return self.con.execute(
            "SELECT date, count(*) AS rows FROM daily_bars GROUP BY date ORDER BY date").df()

    def symbol_count(self) -> int:
        return int(self.con.execute(
            "SELECT count(DISTINCT symbol) FROM daily_bars").fetchone()[0])

    def corporate_actions_applied_count(self) -> int:
        row = self.con.execute(
            "SELECT count(*) FROM corporate_actions WHERE adjustment_factor IS NOT NULL"
        ).fetchone()
        return int(row[0])

    # -- export ------------------------------------------------------------------
    def parquet_export(self, parquet_dir: Path, year: Optional[int] = None) -> list[Path]:
        """Export daily_bars partitioned by year: daily_bars_{YYYY}.parquet.

        If year is specified, only that year is exported (optimized for daily sync).
        """
        parquet_dir = Path(parquet_dir)
        parquet_dir.mkdir(parents=True, exist_ok=True)
        if year is not None:
            years = [year]
        else:
            years = [r[0] for r in self.con.execute(
                "SELECT DISTINCT year(date) AS y FROM daily_bars ORDER BY y").fetchall()]
        written: list[Path] = []
        for y in years:
            dest = parquet_dir / f"daily_bars_{int(y)}.parquet"
            self.con.execute(
                f"COPY (SELECT * FROM daily_bars WHERE year(date) = {int(y)}) "
                f"TO '{dest.as_posix()}' (FORMAT PARQUET)")
            written.append(dest)
        return written

    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> "MarketStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
