"""Probe round 3: settle remaining unknowns.

  P1  Setup 1 condition-B (z>=1.5 today) viability with day-T dry-up
  P2  deep gap buffer tails (3.5/4/5/7.5%) + worst gap-down
  P3  Option-A (z/3) score distribution on rows where S2 / S3 fire,
      to pick a defensible default threshold (not a guess)
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


def p1_condition_b():
    print("\nP1: Setup 1 condition-B (z>=1.5 today) + day-T dry-up")
    n_b, n_z, tot = con.execute(f"""
        WITH w AS (
          SELECT symbol, date, deliverable_qty AS dlv, volume AS vol,
                 avg(deliverable_qty) OVER (PARTITION BY symbol ORDER BY date
                     ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS s20d,
                 avg(volume) OVER (PARTITION BY symbol ORDER BY date
                     ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS s20v,
                 stddev_pop(deliverable_qty) OVER (PARTITION BY symbol ORDER BY date
                     ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS sd20
          FROM daily_bars
          WHERE date >= '{D0}' AND deliverable_qty IS NOT NULL
            AND volume IS NOT NULL AND volume > 0
        )
        SELECT sum(CASE WHEN sd20 > 0 AND (dlv - s20d)/sd20 >= 1.5
                          AND vol <= 0.65*s20v THEN 1 ELSE 0 END),
               sum(CASE WHEN sd20 > 0 AND (dlv - s20d)/sd20 >= 1.5 THEN 1 ELSE 0 END),
               count(*)
        FROM w WHERE s20d IS NOT NULL AND s20v IS NOT NULL AND s20d > 0
    """).fetchone()
    print(f"  windows={tot}: z>=1.5 days={n_z} ({(n_z or 0)/max(1,tot):.3%}), "
          f"z>=1.5 AND dry-T JOINT={n_b} ({(n_b or 0)/max(1,tot):.5%})")


def p2_deep_buffers():
    print(f"\nP2: deep gap buffer tails (from {D0})")
    gaps = con.execute(f"""
        WITH prev AS (
          SELECT symbol, date, open,
                 lag(close) OVER (PARTITION BY symbol ORDER BY date) AS pclose
          FROM daily_bars WHERE date >= '{D0}'
        )
        SELECT (open - pclose)/pclose AS gap FROM prev
        WHERE pclose IS NOT NULL AND pclose > 0 AND open IS NOT NULL
    """).df()["gap"]
    downs = gaps[gaps < 0]
    print(f"  worst gap-down: {gaps.min():.2%}")
    for buf in (0.02, 0.03, 0.035, 0.04, 0.05, 0.075, 0.10):
        beyond = int((downs < -buf).sum())
        print(f"  buffer {buf:.1%}: gap-downs beyond = {beyond} "
              f"({beyond/max(1,len(gaps)):.3%} of all sessions)")


def p3_option_a_thresholds():
    print("\nP3: Option-A (z/3) score distribution where setups fire")
    score_expr = """
      0.35 * greatest(coalesce(f.delivery_z, 0), 0) / 3.0
      + 0.35 * coalesce(f.imom_percentile, 0)
      + 0.30 * (1.0 - coalesce(f.pv_percentile, 1.0))
    """
    # S2 firing rows
    q2 = f"""
        SELECT quantile_cont({score_expr}, 0.5) AS p50,
               quantile_cont({score_expr}, 0.75) AS p75,
               quantile_cont({score_expr}, 0.95) AS p95,
               max({score_expr}) AS mx,
               avg(CASE WHEN {score_expr} >= 0.45 THEN 1.0 ELSE 0.0 END) AS share45,
               avg(CASE WHEN {score_expr} >= 0.50 THEN 1.0 ELSE 0.0 END) AS share50,
               count(*) AS n
        FROM features f JOIN daily_bars b ON b.symbol=f.symbol AND b.date=f.date
        WHERE f.rsi2 <= 10.0 AND f.sma20_delivery IS NOT NULL
          AND coalesce(b.delivery_adj, b.deliverable_qty) <= 1.15 * f.sma20_delivery
    """
    r2 = con.execute(q2).fetchone()
    print(f"  S2 rows (n={r2[6]}): p50={r2[0]:.3f} p75={r2[1]:.3f} p95={r2[2]:.3f} "
          f"max={r2[3]:.3f} | share>=0.45={r2[4]:.1%} share>=0.50={r2[5]:.1%}")
    # S3 firing rows
    q3 = f"""
        SELECT quantile_cont({score_expr}, 0.5) AS p50,
               quantile_cont({score_expr}, 0.95) AS p95,
               avg(CASE WHEN {score_expr} >= 0.45 THEN 1.0 ELSE 0.0 END) AS share45,
               count(*) AS n
        FROM features f JOIN daily_bars b ON b.symbol=f.symbol AND b.date=f.date
        WHERE f.rs_percentile >= 0.95 AND f.high_52w IS NOT NULL
          AND b.high_adj - b.low_adj <= 0.03 * b.low_adj
          AND b.close_adj >= 0.985 * f.high_52w
    """
    r3 = con.execute(q3).fetchone()
    print(f"  S3 rows (n={r3[3]}): p50={r3[0]:.3f} p95={r3[1]:.3f} "
          f"share>=0.45={r3[2]:.1%}")
    # S1-fixed rows (shock<=5d + dry T) with features: trend/squeeze gates excluded
    q1 = f"""
        WITH w AS (
          SELECT f.symbol, f.date, {score_expr} AS s
          FROM features f JOIN daily_bars b ON b.symbol=f.symbol AND b.date=f.date
          WHERE b.volume_adj <= 0.65 * f.sma20_vol
            AND f.sma20_vol IS NOT NULL
            AND (f.delivery_z >= 1.5
                 OR coalesce(b.delivery_adj, b.deliverable_qty) >= 2.2 * f.sma20_delivery)
        )
        SELECT quantile_cont(s, 0.5), quantile_cont(s, 0.95), max(s), count(*) FROM w
    """
    r1 = con.execute(q1).fetchone()
    print(f"  S1-ish rows (dry-T + any shock, no trend gate; n={r1[3]}): "
          f"p50={r1[0]:.3f} p95={r1[1]:.3f} max={r1[2]:.3f}")


if __name__ == "__main__":
    p1_condition_b()
    p2_deep_buffers()
    p3_option_a_thresholds()
    print("\nDONE")
