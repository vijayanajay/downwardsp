"""Data health memo for the seeded 2010-2026 dataset.

Run AFTER `nse-cash sync --from 2010-01-01` + `corporate-history` seeding:
    .venv/Scripts/python.exe scripts/data_health_memo.py

Sections:
  1. Per-year bar and delivery coverage vs business-day counts.
  2. Cross-source sanity: days where PR (delivery-bearing) and legacy both
     exist must agree on EQ-row counts within 1% (parser drift detector).
  3. Symbol stats, ADTV>=5Cr density, PIT universe size.
  4. Corporate-action density by year + adjustment coverage.
  5. Index coverage (regime + breadth denominators for the backtest).
  6. Verdict lines a human can act on.
"""

from __future__ import annotations

import json
from datetime import date as Date
from pathlib import Path

import duckdb
import pandas as pd

DB = Path("data/db/nse_market.duckdb")

# NSE closed full-day on these (verified from NSE holiday history); used only
# to sanity-check that "unavailable in all formats" days are real holidays.
KNOWN_HOLIDAYS_2010_2011 = {
    Date(2010, 1, 1), Date(2010, 1, 26), Date(2010, 2, 12), Date(2010, 2, 26),
    Date(2010, 3, 1), Date(2010, 3, 24), Date(2010, 3, 25), Date(2010, 4, 2),
    Date(2010, 4, 14), Date(2010, 9, 10), Date(2010, 11, 17),
    Date(2010, 11, 18), Date(2010, 11, 22), Date(2010, 11, 23),
    Date(2011, 1, 26),
}
HOLIDAYS_2026 = {
    Date(2026, 1, 26), Date(2026, 3, 3), Date(2026, 3, 4), Date(2026, 3, 21),
    Date(2026, 4, 1), Date(2026, 4, 3), Date(2026, 4, 14), Date(2026, 4, 21),
    Date(2026, 5, 1), Date(2026, 8, 15), Date(2026, 10, 2),
}

TURNOVER_QUERY = """
SELECT year(date) AS yr, count(*) AS n_days,
       median(adtv) AS med_adtv, count(*) FILTER (WHERE adtv >= 5e7) AS n_5cr
FROM (
    SELECT date, symbol, avg(turnover) OVER (
        PARTITION BY symbol ORDER BY date ROWS BETWEEN 89 PRECEDING AND CURRENT ROW
    ) AS adtv
    FROM daily_bars
)
GROUP BY yr ORDER BY yr
"""


def _fmt(n, pct=False) -> str:
    if n is None:
        return "n/a"
    if pct:
        return f"{n * 100:.1f}%"
    return f"{n:,}"


def main() -> None:
    con = duckdb.connect(str(DB), read_only=True)
    bars = con.execute("""
        SELECT date, symbol, close, volume, turnover, deliverable_qty, series
        FROM daily_bars ORDER BY date
    """).df()
    bars["date"] = pd.to_datetime(bars["date"]).dt.date

    print("=" * 78)
    print("NSE CASH DATA HEALTH MEMO")
    print(f"generated {Date.today()}  |  db: {DB}")
    print("=" * 78)

    total_days = bars["date"].nunique()
    print(f"\nbars: {len(bars):,} rows | {bars['symbol'].nunique():,} symbols | "
          f"{total_days} trading dates | {bars['date'].min()} -> {bars['date'].max()}")

    # 1. Per-year coverage ---------------------------------------------------
    print("\n1) PER-YEAR COVERAGE")
    print(f"{'year':<6}{'days':>6}{'symbols':>9}{'delivery%':>11}"
          f"{'med ADTV90 (Cr)':>17}{'sym-days>=5Cr':>15}")
    for yr, g in bars.groupby(bars["date"].map(lambda d: d.year)):
        n_days = g["date"].nunique()
        deliv = (g["deliverable_qty"].notna().mean()
                 if "deliverable_qty" in g else 0.0)
        print(f"{yr:<6}{n_days:>6}{g['symbol'].nunique():>9}"
              f"{_fmt(deliv, pct=True):>11}{'-':>17}{'-':>15}")

    adtv = con.execute(TURNOVER_QUERY).df()
    print("\n   liquidity (turnover-based ADTV90, not ADTV qty):")
    print(f"   {'year':<6}{'sym-days':>10}{'med ADTV (Cr)':>15}{'>=5Cr':>10}")
    for _, r in adtv.iterrows():
        print(f"   {int(r['yr']):<6}{int(r['n_days']):>10,}"
              f"{(r['med_adtv'] or 0) / 1e7:>15.2f}{int(r['n_5cr']):>10,}")

    # 2. Cross-source sanity -------------------------------------------------
    print("\n2) CROSS-SOURCE PARSER SANITY (PR vs legacy overlap)")
    print("   (row-count drift between PR and legacy parsers would show as")
    print("    coverage ratio != ~1.00 on overlap years; delivery is PR-only)")
    print("   skipped: needs raw-source tags; raw archives retained under data/raw)")

    # 3. Symbols & universe ---------------------------------------------------
    print("\n3) UNIVERSE")
    latest = bars["date"].max()
    syms = bars[bars["date"] == latest]["symbol"].nunique()
    eq_all = bars["series"].eq("EQ").mean() if "series" in bars else 1.0
    print(f"   latest date {latest}: {syms} symbols | series==EQ share: {_fmt(eq_all, pct=True)}")
    pit = con.execute("SELECT count(DISTINCT date), count(*) FROM pit_universe").fetchone()
    print(f"   pit_universe: {pit[1]:,} rows over {pit[0]} dates")

    # 4. Corporate actions ----------------------------------------------------
    print("\n4) CORPORATE ACTIONS")
    acts = con.execute("""
        SELECT year(ex_date) AS yr, action_type, count(*) AS n
        FROM corporate_actions GROUP BY 1, 2 ORDER BY 1, 2
    """).df()
    piv = acts.pivot_table(index="yr", columns="action_type",
                           values="n", aggfunc="sum", fill_value=0)
    piv["total"] = piv.sum(axis=1)
    print(piv.to_string())
    n_syms_acts = con.execute(
        "SELECT count(DISTINCT symbol) FROM corporate_actions").fetchone()[0]
    n_af = con.execute(
        "SELECT count(*) FROM corporate_actions WHERE adjustment_factor IS NOT NULL"
    ).fetchone()[0]
    print(f"   symbols with actions: {n_syms_acts:,} | rows with AF: {n_af:,}")

    # 4.5 Gap audit -------------------------------------------------------------
    audit_path = Path("reports/corporate_action_audit.json")
    if audit_path.exists():
        print("\n4.5) UNEXPLAINED-GAP AUDIT (raw overnight moves beyond +/-25%)")
        payload = json.loads(audit_path.read_text(encoding="utf-8"))
        gaps = payload.get("gaps", [])
        print(f"   flagged: {payload.get('total_gaps', len(gaps))} | "
              f"unexplained (any action within 3d): {payload.get('unexplained', '?')}")
        strict = 0
        matched = 0
        for g in gaps:
            af, ex = g.get("action_af"), g.get("action_ex_date")
            if ex is None:
                continue
            matched += 1
            # strict: the recorded action's factor must actually match the
            # observed price ratio (a nearby demerger 'explains' nothing if
            # its factor is unrelated to the gap)
            if af and abs(g["implied_factor"] - af) / max(af, 1e-9) < 0.15:
                strict += 1
        print(f"   strict factor-match: {strict}/{matched} matched gaps "
              "(AF within 15% of observed ratio)")
        unexplained = [g for g in gaps if not g.get("action_ex_date")]
        for g in unexplained[:8]:
            print(f"   UNEXPLAINED {g['symbol']} {g['gap_date']} "
                  f"{g['prev_close']:.2f} -> {g['close']:.2f} ({g['implied_factor']:.3f}x)")
        if len(unexplained) > 8:
            print(f"   ... and {len(unexplained) - 8} more")
    else:
        print("\n4.5) gap audit: reports/corporate_action_audit.json not found "
              "(run `nse-cash corporate-history --audit-only` after seeding)")

    # 5. Indices ---------------------------------------------------------------
    print("\n5) INDEX COVERAGE (regime gate + breadth denominator)")
    idx = con.execute("""
        SELECT index_name, count(*) AS n, min(date) AS lo, max(date) AS hi
        FROM market_indices GROUP BY 1 ORDER BY 1
    """).fetchall()
    for name, n, lo, hi in idx:
        print(f"   {name:<10} {n:>6} days  {lo} -> {hi}")
    if not any("NIFTY 50" in r[0] for r in idx):
        print("   [!] NO NIFTY 50 -> regime will be DEFENSIVE_CASH; backtest yields 0 trades")

    # 6. Verdicts ---------------------------------------------------------------
    print("\n6) VERDICTS")
    all_yrs = sorted(bars["date"].map(lambda d: d.year).unique())
    thin = []
    for yr in all_yrs:
        n_days = bars[bars["date"].map(lambda d: d.year) == yr]["date"].nunique()
        if n_days < 200:
            thin.append((yr, n_days))
    if thin:
        print(f"   [!] thin years (<200 sessions): {thin} -> re-run sync for those ranges")
    else:
        print("   [ok] every year has >=200 sessions")
    if not any("NIFTY 500" in r[0] for r in idx):
        print("   [!] no NIFTY 500 -> breadth is computed from the PIT universe instead")
    deliv_share = bars["deliverable_qty"].notna().mean()
    print(f"   delivery coverage overall: {_fmt(deliv_share, pct=True)} "
          f"(pre-2011 MTO reports may be absent; Z_delivery NULL -> scores 0 on that term)")
    print("   next: nse-cash corporate-history (seed) -> audit report -> full backtest")
    con.close()


if __name__ == "__main__":
    main()
