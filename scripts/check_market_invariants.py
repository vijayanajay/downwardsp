"""CR-2026-001 data-hygiene audit (read-only): run after any sync.

  1. delivery <= volume invariant on the adjusted columns (the identity the
     Setup 1/2/5 predicates and the delivery-Z feature silently rely on).
  2. Circuit-band gap audit: overnight closes beyond +/-20% are impossible
     under NSE band rules; they are corporate-action artifacts (unit splits,
     demergers) that polluted the measured gap distribution (1,360 rows
     found on 2026-09-18) until the adjuster covers them.

Usage: .venv/Scripts/python.exe scripts/check_market_invariants.py [--fix-report]
Exit code 0 = clean, 1 = violations found (does not mutate the DB).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

DEFAULT_DB = Path("data/db/nse_market.duckdb")
MAX_ABS_GAP = 0.20  # NSE max band; anything beyond is a data artifact


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--limit", type=int, default=25,
                    help="rows to print per violation table")
    args = ap.parse_args()
    if not args.db.exists():
        print(f"no database at {args.db}")
        return 1

    con = duckdb.connect(str(args.db), read_only=True)
    rc = 0

    # --- 1. delivery <= volume ------------------------------------------
    n, bad = con.execute("""
        SELECT count(*),
               sum(CASE WHEN delivery_adj > volume_adj + 1e-6 THEN 1 ELSE 0 END)
        FROM daily_bars
        WHERE delivery_adj IS NOT NULL AND volume_adj IS NOT NULL
          AND volume_adj > 0
    """).fetchone()
    print(f"[1] delivery<=volume: {n:,} checked rows, {bad or 0} violations")
    if bad:
        rc = 1
        for row in con.execute("""
            SELECT symbol, date, delivery_adj, volume_adj
            FROM daily_bars
            WHERE delivery_adj > volume_adj + 1e-6 AND volume_adj > 0
            ORDER BY date LIMIT ?
        """, [args.limit]).fetchall():
            print(f"    {row[0]} {row[1]}: delivery {row[2]:,.0f} > volume {row[3]:,.0f}")

    # --- 2. impossible overnight gaps ------------------------------------
    n2, gbad = con.execute(f"""
        WITH g AS (
          SELECT symbol, date,
                 close / lag(close) OVER (PARTITION BY symbol ORDER BY date) - 1.0
                 AS gap
          FROM daily_bars
        )
        SELECT count(*), sum(CASE WHEN abs(gap) > {MAX_ABS_GAP} THEN 1 ELSE 0 END)
        FROM g WHERE gap IS NOT NULL
    """).fetchone()
    print(f"[2] overnight gaps beyond +-{MAX_ABS_GAP:.0%}: "
          f"{n2:,} measured, {gbad or 0} impossible (corporate-action artifacts)")
    if gbad:
        rc = 1
        for row in con.execute(f"""
            WITH g AS (
              SELECT symbol, date,
                     close / lag(close) OVER (PARTITION BY symbol ORDER BY date) - 1.0
                     AS gap
              FROM daily_bars
            )
            SELECT symbol, date, gap FROM g
            WHERE abs(gap) > {MAX_ABS_GAP} ORDER BY date LIMIT ?
        """, [args.limit]).fetchall():
            print(f"    {row[0]} {row[1]}: {row[2]:+.2%}")

    # --- 3. zero/negative prices -----------------------------------------
    n3, pbad = con.execute("""
        SELECT count(*), sum(CASE WHEN close <= 0 OR high < low THEN 1 ELSE 0 END)
        FROM daily_bars
    """).fetchone()
    print(f"[3] price sanity (close>0, high>=low): {n3:,} rows, {pbad or 0} bad")
    if pbad:
        rc = 1

    con.close()
    print("RESULT:", "CLEAN" if rc == 0 else "VIOLATIONS FOUND (see above)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
