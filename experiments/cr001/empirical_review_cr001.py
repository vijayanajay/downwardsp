"""Fast empirical probes of CR-2026-001 (read-only, bounded queries).

Bounded to 2022-01-01+ (5 years, statistically ample) with memory pragmas.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import duckdb

DB = Path("data/db/nse_market.duckdb")
con = duckdb.connect(str(DB), read_only=True)
con.execute("SET memory_limit='3GB'; SET threads=4;")
D0 = "2022-01-01"


def extent():
    n_days, dmin, dmax = con.execute(
        "SELECT count(DISTINCT date), min(date), max(date) FROM daily_bars").fetchone()
    n_sym = con.execute("SELECT count(DISTINCT symbol) FROM daily_bars").fetchone()[0]
    n_rows = con.execute("SELECT count(*) FROM daily_bars").fetchone()[0]
    try:
        n_feat = con.execute("SELECT count(*) FROM features").fetchone()[0]
        fmin, fmax = con.execute("SELECT min(date), max(date) FROM features").fetchone()
    except Exception:
        n_feat, fmin, fmax = 0, None, None
    print(f"extent: dates={n_days} [{dmin}..{dmax}] symbols={n_sym} bar_rows={n_rows} "
          f"features={n_feat} [{fmin}..{fmax}]")


# ---------------------------------------------------------------- E2
def e2_setup1():
    print("\nE2: Setup 1 day-T shock+dry-up joint feasibility")
    n2, n_joint, n_shock, n_dry = con.execute(f"""
        WITH w AS (
          SELECT symbol, date, deliverable_qty AS dlv, volume AS vol,
                 avg(deliverable_qty) OVER (PARTITION BY symbol ORDER BY date
                     ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS sma20_dlv,
                 avg(volume) OVER (PARTITION BY symbol ORDER BY date
                     ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS sma20_vol
          FROM daily_bars
          WHERE date >= '{D0}' AND deliverable_qty IS NOT NULL
            AND volume IS NOT NULL AND volume > 0
        )
        SELECT count(*),
               sum(CASE WHEN dlv >= 2.2*sma20_dlv AND vol <= 0.65*sma20_vol THEN 1 ELSE 0 END),
               sum(CASE WHEN dlv >= 2.2*sma20_dlv THEN 1 ELSE 0 END),
               sum(CASE WHEN vol <= 0.65*sma20_vol THEN 1 ELSE 0 END)
        FROM w WHERE sma20_dlv IS NOT NULL AND sma20_vol IS NOT NULL AND sma20_dlv > 0
    """).fetchone()
    tot = max(1, n2)
    print(f"  day-T windows (from {D0})={n2}: shock={n_shock} ({(n_shock or 0)/tot:.3%}), "
          f"dry={n_dry} ({(n_dry or 0)/tot:.2%}), JOINT={n_joint} ({(n_joint or 0)/tot:.5%})")


# ---------------------------------------------------------------- E1
def e1_scores():
    print("\nE1: conviction-score attainability on persisted features")
    try:
        zmax, imp, pv, n = con.execute("""
            SELECT max(delivery_z), max(imom_percentile), min(pv_percentile), count(*)
            FROM features
        """).fetchone()
    except Exception as exc:
        print(f"  features query failed: {exc}")
        return
    if not n or n == 0:
        print("  features table empty")
        return
    print(f"  rows={n}  z max={zmax:.2f}  imom_pct max={imp:.4f}  pv_pct min={pv:.4f}")
    s_rawz = 0.35*zmax + 0.35*imp + 0.30*(1-pv)
    s_normz = 0.35*zmax/3 + 0.35*imp + 0.30*(1-pv)
    s_dry = 0.35*imp + 0.30*(1-pv)   # z<=0 ceiling (dry setups)
    print(f"  max attainable S: raw-z={s_rawz:.3f} | z/3={s_normz:.3f} | dry-day={s_dry:.3f} (thr 0.70)")
    zneg, zmed = con.execute("""
        SELECT avg(CASE WHEN delivery_z <= 0 THEN 1.0 ELSE 0.0 END), median(delivery_z)
        FROM features WHERE delivery_z IS NOT NULL
    """).fetchone()
    print(f"  z<=0 share={zneg:.1%}  median z={zmed:.2f}")
    n_pass, n_tot = con.execute("""
        SELECT sum(CASE WHEN 0.35*greatest(coalesce(delivery_z,0),0)
                          + 0.35*coalesce(imom_percentile,0)
                          + 0.30*(1.0-coalesce(pv_percentile,1.0)) >= 0.70
                   THEN 1 ELSE 0 END), count(*)
        FROM features
    """).fetchone()
    print(f"  rows clearing S>=0.70 (raw-z formula): {n_pass}/{n_tot} ({n_pass/max(1,n_tot):.2%})")


# ---------------------------------------------------------------- E3
def e3_setup4():
    print(f"\nE3: Setup 4 rolling 90d-high self-reference after breakout (from {D0})")
    n_bo = con.execute(f"""
        WITH prior AS (
          SELECT symbol, date, close,
                 max(high) OVER (PARTITION BY symbol ORDER BY date
                     ROWS BETWEEN 90 PRECEDING AND 1 PRECEDING) AS ph
          FROM daily_bars WHERE date >= '2021-06-01'
        ),
        crossed AS (
          SELECT symbol, date, ph,
                 lag(close) OVER (PARTITION BY symbol ORDER BY date) AS pc
          FROM prior WHERE ph IS NOT NULL
        )
        SELECT count(*) FROM crossed
        WHERE close > 1.02*ph AND (pc IS NULL OR pc <= 1.02*ph)
    """).fetchone()[0]
    print(f"  breakout crossing events: {n_bo}")
    if not n_bo:
        print("  -> insufficient history; inconclusive on this DB")
        return
    df = con.execute(f"""
        WITH prior AS (
          SELECT symbol, date, close, high,
                 max(high) OVER (PARTITION BY symbol ORDER BY date
                     ROWS BETWEEN 90 PRECEDING AND 1 PRECEDING) AS ph
          FROM daily_bars WHERE date >= '2021-06-01'
        ),
        crossed AS (
          SELECT symbol, date, ph, close, high,
                 lag(close) OVER (PARTITION BY symbol ORDER BY date) AS pc
          FROM prior WHERE ph IS NOT NULL
        ),
        bo AS (
          SELECT symbol, min(date) AS bo_date FROM crossed
          WHERE close > 1.02*ph AND (pc IS NULL OR pc <= 1.02*ph)
          GROUP BY symbol
        ),
        after AS (
          SELECT b.symbol, b.date, f.bo_date,
                 date_diff('day', f.bo_date, b.date) AS d,
                 first_value(b.high) OVER (PARTITION BY b.symbol ORDER BY b.date) AS ph_est,
                 max(b.high) OVER (PARTITION BY b.symbol ORDER BY b.date
                     ROWS BETWEEN 89 PRECEDING AND CURRENT ROW) AS roll90
          FROM daily_bars b JOIN bo f ON b.symbol=f.symbol AND b.date >= f.bo_date
          WHERE b.date >= '{D0}'
        )
        SELECT d, count(*) AS n,
               avg((roll90 - ph_est)/ph_est)*100 AS avg_drift,
               max((roll90 - ph_est)/ph_est)*100 AS max_drift
        FROM after WHERE d <= 10 GROUP BY d ORDER BY d
    """).df()
    print("  days_since | n | avg% rolling90high above pre-breakout ceiling | worst%")
    for _, r in df.iterrows():
        print(f"    {int(r['d']):2d} | {int(r['n']):5d} | {r['avg_drift']:6.2f}% | {r['max_drift']:7.2f}%")


# ---------------------------------------------------------------- E4
def e4_gaps():
    print(f"\nE4: overnight gap-down distribution (buffer sizing), from {D0}")
    gaps = con.execute(f"""
        WITH prev AS (
          SELECT symbol, date, open, close,
                 lag(close) OVER (PARTITION BY symbol ORDER BY date) AS pclose
          FROM daily_bars WHERE date >= '{D0}'
        )
        SELECT (open - pclose)/pclose AS gap FROM prev
        WHERE pclose IS NOT NULL AND pclose > 0 AND open IS NOT NULL
    """).df()["gap"]
    print(f"  sessions measured: {len(gaps)}")
    downs = gaps[gaps < 0]
    print(f"  gap-downs: {len(downs)} ({len(downs)/max(1,len(gaps)):.1%} of sessions)")
    if len(downs):
        qs = downs.quantile([0.5, 0.9, 0.99, 1.0])
        print("  gap-down quantiles: " + ", ".join(f"p{int(k*100)}={v:.2%}" for k, v in qs.items()))
        for buf in (0.005, 0.01, 0.015, 0.02, 0.03):
            beyond = int((downs < -buf).sum())
            print(f"  buffer {buf:.1%}: gap-downs beyond = {beyond} "
                  f"({beyond/max(1,len(gaps)):.3%} of all sessions)")


# ---------------------------------------------------------------- E5
def e5_invariant():
    print(f"\nE5: delivery <= volume invariant (from {D0})")
    bad_adj, bad_raw, n = con.execute(f"""
        SELECT sum(CASE WHEN coalesce(delivery_adj, deliverable_qty)
                             > coalesce(volume_adj, volume) + 0.5 THEN 1 ELSE 0 END),
               sum(CASE WHEN deliverable_qty > volume THEN 1 ELSE 0 END),
               count(*)
        FROM daily_bars WHERE date >= '{D0}'
          AND deliverable_qty IS NOT NULL AND volume IS NOT NULL
    """).fetchone()
    print(f"  rows={n}: adj violations={bad_adj or 0}, raw violations={bad_raw or 0}")


if __name__ == "__main__":
    extent()
    e2_setup1()
    e1_scores()
    e3_setup4()
    e4_gaps()
    e5_invariant()
    print("\nDONE")
