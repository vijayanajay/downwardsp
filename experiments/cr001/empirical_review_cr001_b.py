"""Probe round 2: E3 fixed, E4, E5, and E6 = fire-rate of FIXED predicates."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import duckdb

DB = Path("data/db/nse_market.duckdb")
con = duckdb.connect(str(DB), read_only=True)
con.execute("SET memory_limit='3GB'; SET threads=4;")
D0 = "2022-01-01"


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
          SELECT symbol, date, close, ph,
                 lag(close) OVER (PARTITION BY symbol ORDER BY date) AS pc
          FROM prior WHERE ph IS NOT NULL
        )
        SELECT count(*) FROM crossed
        WHERE close > 1.02*ph AND (pc IS NULL OR pc <= 1.02*ph)
    """).fetchone()[0]
    print(f"  breakout crossing events: {n_bo}")
    if not n_bo:
        print("  -> inconclusive")
        return
    df = con.execute(f"""
        WITH prior AS (
          SELECT symbol, date, close, high,
                 max(high) OVER (PARTITION BY symbol ORDER BY date
                     ROWS BETWEEN 90 PRECEDING AND 1 PRECEDING) AS ph
          FROM daily_bars WHERE date >= '2021-06-01'
        ),
        crossed AS (
          SELECT symbol, date, close, high, ph,
                 lag(close) OVER (PARTITION BY symbol ORDER BY date) AS pc
          FROM prior WHERE ph IS NOT NULL
        ),
        bo AS (
          SELECT symbol, min(date) AS bo_date FROM crossed
          WHERE close > 1.02*ph AND (pc IS NULL OR pc <= 1.02*ph)
          GROUP BY symbol
        ),
        after AS (
          SELECT b.symbol, b.date,
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


# ---------------------------------------------------------------- E6
def e6_fire_rates():
    print(f"\nE6: empirical fire-rates of CURRENT vs FIXED predicates (from {D0})")

    # --- Fixed Setup 1: shock in last 5 sessions + dry day T (+ squeeze proxy later)
    n1 = con.execute(f"""
        WITH w AS (
          SELECT symbol, date, deliverable_qty AS dlv, volume AS vol,
                 avg(deliverable_qty) OVER (PARTITION BY symbol ORDER BY date
                     ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS s20d,
                 avg(volume) OVER (PARTITION BY symbol ORDER BY date
                     ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS s20v
          FROM daily_bars
          WHERE date >= '2021-11-01' AND deliverable_qty IS NOT NULL
            AND volume IS NOT NULL AND volume > 0
        ),
        shocks AS (
          SELECT *, CASE WHEN dlv >= 2.2*s20d THEN 1 ELSE 0 END AS shock
          FROM w WHERE s20d > 0 AND s20v > 0
        ),
        rolled AS (
          SELECT *, max(shock) OVER (PARTITION BY symbol ORDER BY date
              ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS shock_5d
          FROM shocks
        )
        SELECT sum(CASE WHEN shock_5d = 1 AND vol <= 0.65*s20v THEN 1 ELSE 0 END),
               sum(CASE WHEN vol <= 0.65*s20v THEN 1 ELSE 0 END),
               count(*)
        FROM rolled
    """).fetchone()
    fixed1, dry_any, tot1 = n1
    print(f"  Setup 1 fixed (shock<=5d + dry T): {fixed1} fires / {tot1} windows "
          f"({fixed1/max(1,tot1):.5%}); dry-T alone: {dry_any} ({dry_any/max(1,tot1):.2%})")

    # --- Setup 2 predicate on real data (needs features: rsi2, sma20_delivery)
    n2 = con.execute(f"""
        SELECT sum(CASE WHEN f.rsi2 <= 10.0
                          AND coalesce(b.delivery_adj, b.deliverable_qty)
                              <= 1.15 * f.sma20_delivery THEN 1 ELSE 0 END),
               sum(CASE WHEN f.rsi2 <= 10.0 THEN 1 ELSE 0 END),
               count(*)
        FROM features f JOIN daily_bars b ON b.symbol=f.symbol AND b.date=f.date
        WHERE f.rsi2 IS NOT NULL AND f.sma20_delivery IS NOT NULL
          AND coalesce(b.delivery_adj, b.deliverable_qty) IS NOT NULL
    """).fetchone()
    s2_full, s2_rsi, tot2 = n2
    print(f"  Setup 2 predicate (rsi2<=10 + subdued dlv): {s2_full} fires / {tot2} feature rows "
          f"({s2_full/max(1,tot2):.4%}); rsi2<=10 alone: {s2_rsi} ({s2_rsi/max(1,tot2):.3%})")

    # Of those, how many clear S>=0.70 today (lockout check)?
    if s2_full:
        n2b = con.execute("""
            SELECT sum(CASE WHEN 0.35*greatest(coalesce(f.delivery_z,0),0)
                              + 0.35*coalesce(f.imom_percentile,0)
                              + 0.30*(1.0-coalesce(f.pv_percentile,1.0)) >= 0.70
                       THEN 1 ELSE 0 END),
                   median(0.35*greatest(coalesce(f.delivery_z,0),0)
                          + 0.35*coalesce(f.imom_percentile,0)
                          + 0.30*(1.0-coalesce(f.pv_percentile,1.0)))
            FROM features f JOIN daily_bars b ON b.symbol=f.symbol AND b.date=f.date
            WHERE f.rsi2 <= 10.0 AND f.sma20_delivery IS NOT NULL
              AND coalesce(b.delivery_adj, b.deliverable_qty) <= 1.15 * f.sma20_delivery
        """).fetchone()
        print(f"    of those: clear S>=0.70 today = {n2b[0]}; median S = {n2b[1]:.3f}")

    # --- Setup 3 predicate on real data (rs_percentile, 5d range, 52w high)
    n3 = con.execute(f"""
        SELECT sum(CASE WHEN f.rs_percentile >= 0.95
                          AND b.high_adj - b.low_adj <= 0.03 * b.low_adj
                          AND b.close_adj >= 0.985 * f.high_52w THEN 1 ELSE 0 END),
               count(*)
        FROM features f JOIN daily_bars b ON b.symbol=f.symbol AND b.date=f.date
        WHERE f.rs_percentile IS NOT NULL AND f.high_52w IS NOT NULL
    """).fetchone()
    s3, tot3 = n3
    print(f"  Setup 3 predicate (RS>=95pct + tight base + near 52w high): "
          f"{s3} fires / {tot3} feature rows ({s3/max(1,tot3):.4%})")
    if s3:
        n3b = con.execute("""
            SELECT sum(CASE WHEN 0.35*greatest(coalesce(f.delivery_z,0),0)
                              + 0.35*coalesce(f.imom_percentile,0)
                              + 0.30*(1.0-coalesce(f.pv_percentile,1.0)) >= 0.70
                       THEN 1 ELSE 0 END),
                   median(0.35*greatest(coalesce(f.delivery_z,0),0)
                          + 0.35*coalesce(f.imom_percentile,0)
                          + 0.30*(1.0-coalesce(f.pv_percentile,1.0)))
            FROM features f JOIN daily_bars b ON b.symbol=f.symbol AND b.date=f.date
            WHERE f.rs_percentile >= 0.95
              AND b.high_adj - b.low_adj <= 0.03 * b.low_adj
              AND b.close_adj >= 0.985 * f.high_52w
        """).fetchone()
        print(f"    of those: clear S>=0.70 today = {n3b[0]}; median S = {n3b[1]:.3f}")

    # --- Fixed Setup 4: breakout 3-7 sessions ago (first cross of 1.02x prior-90d
    # high), retest today: low within +/-0.8% of pre-breakout ceiling, hold above,
    # dry volume. (Approximation of the full candle-tail predicate.)
    n4 = con.execute(f"""
        WITH prior AS (
          SELECT symbol, date, open, high, low, close, volume,
                 max(high) OVER (PARTITION BY symbol ORDER BY date
                     ROWS BETWEEN 90 PRECEDING AND 1 PRECEDING) AS ph,
                 avg(volume) OVER (PARTITION BY symbol ORDER BY date
                     ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS s20v
          FROM daily_bars WHERE date >= '2021-06-01'
        ),
        crossed AS (
          SELECT *, lag(close) OVER (PARTITION BY symbol ORDER BY date) AS pc
          FROM prior WHERE ph IS NOT NULL AND s20v > 0
        ),
        events AS (
          SELECT *,
                 CASE WHEN close > 1.02*ph AND (pc IS NULL OR pc <= 1.02*ph)
                      THEN date ELSE NULL END AS bo_d
          FROM crossed
        ),
        last_bo AS (
          SELECT symbol, date,
                 max(bo_d) OVER (PARTITION BY symbol ORDER BY date
                     ROWS BETWEEN 7 PRECEDING AND CURRENT ROW) AS bo_date
          FROM events
        ),
        retest AS (
          SELECT e.*, lb.bo_date,
                 date_diff('day', lb.bo_date, e.date) AS age,
                 first_value(ph) OVER (PARTITION BY e.symbol ORDER BY e.date) AS ph0
          FROM events e JOIN last_bo lb ON lb.symbol=e.symbol AND lb.date=e.date
          WHERE lb.bo_date IS NOT NULL
        )
        SELECT sum(CASE WHEN age BETWEEN 3 AND 7
                          AND abs(low - ph0)/ph0 <= 0.008
                          AND close >= ph0
                          AND volume <= 0.55*s20v THEN 1 ELSE 0 END),
               sum(CASE WHEN age BETWEEN 3 AND 7
                          AND abs(low - ph0)/ph0 <= 0.008
                          AND close >= ph0 THEN 1 ELSE 0 END),
               count(*)
        FROM retest
    """).fetchone()
    s4_full, s4_touch, tot4 = n4
    print(f"  Setup 4 fixed (breakout 3-7d ago + retest of PRE-breakout ceiling "
          f"+ hold + dry): {s4_full} fires / {tot4} rows ({s4_full/max(1,tot4):.5%}); "
          f"retest+hold w/o dry: {s4_touch} ({s4_touch/max(1,tot4):.4%})")


if __name__ == "__main__":
    e3_setup4()
    e4_gaps()
    e5_invariant()
    e6_fire_rates()
    print("\nDONE")
