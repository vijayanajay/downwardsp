"""Gate mining: what observable AT ENTRY TIME separates winners from losers?

Joins the persisted baseline blotter (reports/backtest/backtest_trades.parquet,
arm A of the A/B — verified reproduction of the engine) with point-in-time
features at the SIGNAL day. Signal date is embedded in trade_id
("{symbol}-{YYYY-MM-DD}" set by the engine), so every feature below is known
before the T+1 entry: no lookahead.

Outputs: per-gate in/out expectancy, tercile monotonicity sweeps per feature,
and an honest small-sample flag. Nothing is "promoted" at n=216 — candidates
here are hypotheses for a larger-sample validation.

Run:  .venv/Scripts/python.exe experiments/cr002/mine_trade_gates.py
Writes experiments/cr002/gate_mining.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import duckdb

DB = ROOT / "data" / "db" / "nse_market.duckdb"
OUT = Path(__file__).with_name("gate_mining.json")

con = duckdb.connect(str(DB), read_only=True)
con.execute("SET memory_limit='4GB'; SET threads=4;")


def load_trades() -> pd.DataFrame:
    tr = pd.read_parquet(ROOT / "reports" / "backtest" / "backtest_trades.parquet")
    tr = tr[tr["entry_price"].notna()].copy()
    # trade_id = "{symbol}-{YYYY}-{MM}-{DD}"; NSE symbols may contain '-'
    # (BAJAJ-AUTO), so split from the RIGHT and keep the full date parts.
    sig = tr["trade_id"].str.rsplit("-", n=3, expand=True)
    tr["signal_date"] = pd.to_datetime(sig[1] + "-" + sig[2] + "-" + sig[3]).dt.date
    tr["ret_pct"] = tr["realized_pnl"] / tr["entry_cost"] * 100.0
    tr["win"] = tr["realized_pnl"] > 0
    tr["entry_dow"] = pd.to_datetime(tr["entry_date"]).dt.dayofweek
    return tr


def load_features() -> pd.DataFrame:
    f = con.execute("""
        SELECT f.symbol, f.date, f.delivery_z, f.imom_percentile, f.pv_percentile,
               f.rsi2, f.rs_percentile, f.breakout_age, f.shock_a_5d, f.z15_count_3d,
               b.close_adj, b.high_adj, b.low_adj, b.open_adj
        FROM features f
        JOIN daily_bars b ON b.symbol=f.symbol AND b.date=f.date
    """).df()
    f["date"] = pd.to_datetime(f["date"]).dt.date
    f["range_t"] = (f["high_adj"] - f["low_adj"]) / f["close_adj"]
    _rng = f["high_adj"] - f["low_adj"]
    f["ext_from_low_t"] = np.where(_rng > 0, (f["close_adj"] - f["low_adj"]) / _rng, np.nan)
    f["sma200_rel"] = None
    # ATR14% as of signal day (true range incl. gaps)
    atr = con.execute("""
        WITH prev AS (
          SELECT symbol, date, high_adj, low_adj, close_adj,
                 lag(close_adj) OVER (PARTITION BY symbol ORDER BY date) AS pc
          FROM daily_bars
        ),
        tr AS (
          SELECT symbol, date, close_adj,
                 greatest(high_adj - low_adj,
                          abs(high_adj - pc),
                          abs(low_adj  - pc)) AS tr
          FROM prev
        )
        SELECT symbol, date,
               avg(tr) OVER (PARTITION BY symbol ORDER BY date
                             ROWS BETWEEN 13 PRECEDING AND CURRENT ROW)
               / close_adj AS atr14_pct
        FROM tr
    """).df()
    atr["date"] = pd.to_datetime(atr["date"]).dt.date
    f = f.merge(atr, on=["symbol", "date"], how="left")
    # distance below 52w high
    hw = con.execute("""
        SELECT symbol, date,
               close_adj / max(high_adj) OVER (
                   PARTITION BY symbol ORDER BY date
                   ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS prox_52w
        FROM daily_bars
    """).df()
    hw["date"] = pd.to_datetime(hw["date"]).dt.date
    f = f.merge(hw, on=["symbol", "date"], how="left")
    return f


def load_regime() -> pd.DataFrame:
    from nse_cash.data.storage import MarketStore
    from nse_cash.funnel.market_regime import evaluate_market_regime_range
    store = MarketStore(DB, read_only=True)
    try:
        reg = evaluate_market_regime_range(store, start_date=pd.Timestamp("2023-01-01").date(),
                                           end_date=pd.Timestamp("2026-09-17").date())
    finally:
        store.close()
    reg["date"] = pd.to_datetime(reg["date"]).dt.date
    return reg[["date", "nifty50_above_ema", "breadth_pct"]]


def gate_report(tr: pd.DataFrame, mask: pd.Series, label: str, min_n: int = 25) -> dict:
    inside, outside = tr[mask], tr[~mask]
    row = {
        "gate": label,
        "n_in": int(len(inside)), "n_out": int(len(outside)),
        "win_in": round(float(inside["win"].mean()), 3) if len(inside) else None,
        "win_out": round(float(outside["win"].mean()), 3) if len(outside) else None,
        "ret_in": round(float(inside["ret_pct"].mean()), 3) if len(inside) else None,
        "ret_out": round(float(outside["ret_pct"].mean()), 3) if len(outside) else None,
        "spread": (round(float(inside["ret_pct"].mean() - outside["ret_pct"].mean()), 3)
                   if len(inside) and len(outside) else None),
        "small_n": bool(len(inside) < min_n or len(outside) < min_n),
    }
    return row


def terciles(tr: pd.DataFrame, col: str) -> list[dict]:
    sub = tr[tr[col].notna()]
    if len(sub) < 60:
        return []
    q = pd.qcut(sub[col], 3, duplicates="drop")
    out = []
    for lvl, g in sub.groupby(q, observed=True):
        out.append({"feature": col, "bucket": str(lvl), "n": int(len(g)),
                    "win": round(float(g["win"].mean()), 3),
                    "ret": round(float(g["ret_pct"].mean()), 3)})
    return out


def main() -> None:
    tr = load_trades()
    print(f"filled baseline trades: {len(tr)} "
          f"(win {tr['win'].mean():.1%}, mean ret {tr['ret_pct'].mean():+.3f}%)")
    f = load_features()
    reg = load_regime()
    j = tr.merge(f, left_on=["symbol", "signal_date"], right_on=["symbol", "date"],
                 how="left", suffixes=("", "_f")).merge(reg, left_on="signal_date",
                                                        right_on="date", how="left")
    print(f"feature coverage: {j['delivery_z'].notna().mean():.0%} of trades matched")

    j["near_52w"] = j["prox_52w"] >= 0.98
    gates = [
        j["delivery_z"] >= 1.0, j["delivery_z"] >= 0, j["delivery_z"] < 0,
        j["imom_percentile"] >= 0.9, j["imom_percentile"] >= 0.5,
        j["imom_percentile"] < 0.5,
        j["pv_percentile"] <= 0.3, j["pv_percentile"] >= 0.7,
        j["rsi2"] <= 10, (j["rsi2"] > 10) & (j["rsi2"] <= 30), j["rsi2"] >= 70,
        j["range_t"] <= 0.02, j["range_t"] >= 0.04,
        j["atr14_pct"] >= 0.04, j["atr14_pct"] <= 0.025,
        j["near_52w"], ~j["near_52w"],
        j["rs_percentile"] >= 0.95,
        j["nifty50_above_ema"], ~j["nifty50_above_ema"],
        j["breadth_pct"] >= 55, j["breadth_pct"] <= 45,
        j["setup"] == "SETUP_2_RUBBERBAND", j["setup"] == "SETUP_4_ANCHOR_RETEST",
        j["entry_dow"] <= 1, j["entry_dow"] == 4,
        j["shock_a_5d"].fillna(0) >= 1,
        j["ext_from_low_t"] <= 0.4, j["ext_from_low_t"] >= 0.7,
    ]
    labels = [
        "delivery_z>=1", "delivery_z>=0", "delivery_z<0 (dry)",
        "imom>=p90", "imom>=p50", "imom<p50",
        "pv<=p30 (squeeze)", "pv>=p70 (expansion)",
        "rsi2<=10", "rsi2 10-30", "rsi2>=70",
        "range_T<=2%", "range_T>=4%",
        "atr14%>=4%", "atr14%<=2.5%",
        "near 52w high (>=98%)", "NOT near 52w high",
        "rs_pctile>=95",
        "nifty>EMA20 (risk-on)", "nifty<=EMA20 (risk-off)",
        "breadth>=55%", "breadth<=45%",
        "setup==2", "setup==4 (sanity)",
        "entry Mon/Tue", "entry Fri",
        "shock within 5d",
        "close in lower 40% of day range", "close in upper 30% of day range",
    ]
    rows = [gate_report(j, m, l) for m, l in zip(gates, labels)]
    rep = pd.DataFrame(rows).sort_values("spread", ascending=False)
    print("\n== gates by expectancy spread (in minus out), whole book mean "
          f"{tr['ret_pct'].mean():+.3f}% ==")
    print(rep.to_string(index=False))

    print("\n== tercile sweeps (monotonicity check) ==")
    sweep = []
    for col in ("delivery_z", "imom_percentile", "pv_percentile", "rsi2",
                "range_t", "atr14_pct", "prox_52w", "breadth_pct"):
        rows_t = terciles(j, col)
        sweep.extend(rows_t)
        if rows_t:
            line = " | ".join(f"{r['ret']:+.2f}% (n={r['n']}, w={r['win']:.0%})"
                              for r in rows_t)
            print(f"  {col:16s}: {line}")

    bq = j["breadth_pct"].quantile([1 / 3, 2 / 3]).round(1)
    print(f"\n  breadth_pct tercile boundaries: Q1={bq.iloc[0]}%, Q2={bq.iloc[1]}%")
    top = j[j["breadth_pct"] >= bq.iloc[1]]
    rest = j[j["breadth_pct"] < bq.iloc[1]]
    print(f"  gate breadth>=Q2: n={len(top)} ret={top['ret_pct'].mean():+.3f}% "
          f"win={top['win'].mean():.1%} | below: n={len(rest)} "
          f"ret={rest['ret_pct'].mean():+.3f}% win={rest['win'].mean():.1%}")
    combo = j[(j["breadth_pct"] >= bq.iloc[1]) & (j["atr14_pct"] <= 0.025)]
    combo_out = j[~((j["breadth_pct"] >= bq.iloc[1]) & (j["atr14_pct"] <= 0.025))]
    print(f"  combo breadth>=Q2 AND atr<=2.5%: n={len(combo)} "
          f"ret={combo['ret_pct'].mean():+.3f}% win={combo['win'].mean():.1%} "
          f"| rest n={len(combo_out)} ret={combo_out['ret_pct'].mean():+.3f}%")

    OUT.write_text(json.dumps({"book": {"n": len(tr), "win": float(tr["win"].mean()),
                                        "mean_ret": float(tr["ret_pct"].mean())},
                               "gates": rows, "terciles": sweep},
                              indent=2), encoding="utf-8")
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
