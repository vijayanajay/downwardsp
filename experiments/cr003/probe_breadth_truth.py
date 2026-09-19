"""CR-003 Probe 1: Is the breadth edge (>=77.3%) real, or index drift?
Read-only. Reuses the CR's own joins (blotter x features x regime).
Tests: (a) reproduce terciles, (b) threshold sweep, (c) per-year stability,
(d) NIFTY500 forward-return control (is it just the index going up?),
(e) trade-level NIFTY500 beta decomposition (one line of regression).
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
import duckdb

DB = ROOT / "data" / "db" / "nse_market.duckdb"
con = duckdb.connect(str(DB), read_only=True)
con.execute("SET memory_limit='4GB'; SET threads=4;")

from nse_cash.data.storage import MarketStore
from nse_cash.funnel.market_regime import evaluate_market_regime_range

store = MarketStore(DB, read_only=True)
try:
    reg = evaluate_market_regime_range(store,
        start_date=pd.Timestamp("2023-01-01").date(),
        end_date=pd.Timestamp("2026-09-17").date())
finally:
    store.close()
reg["date"] = pd.to_datetime(reg["date"]).dt.date
reg = reg[["date", "breadth_pct", "nifty50_above_ema"]]

tr = pd.read_parquet(ROOT / "reports" / "backtest" / "backtest_trades.parquet")
tr = tr[tr["entry_price"].notna()].copy()
sig = tr["trade_id"].str.rsplit("-", n=3, expand=True)
tr["signal_date"] = pd.to_datetime(sig[1] + "-" + sig[2] + "-" + sig[3]).dt.date
tr["ret_pct"] = tr["realized_pnl"] / tr["entry_cost"] * 100.0
tr["win"] = tr["realized_pnl"] > 0
tr["year"] = pd.to_datetime(tr["entry_date"]).dt.year

# forward NIFTY 500 return over the trade's holding window (entry->exit dates),
# as a proxy for market drift during the trade (day-resolution)
n500 = con.execute("""
    SELECT date, close FROM market_indices WHERE index_name='NIFTY 500' ORDER BY date
""").df()
n500["date"] = pd.to_datetime(n500["date"]).dt.date
n500 = n500.set_index("date")["close"]

def fwd_ret(row):
    try:
        c0 = n500.get(row.entry_date); c1 = n500.get(row.exit_date)
        if c0 and c1 and c0 > 0:
            return (c1 - c0) / c0 * 100.0
    except Exception:
        pass
    return np.nan
tr["n500_fwd_pct"] = [fwd_ret(r) for r in tr.itertuples()]

j = tr.merge(reg, left_on="signal_date", right_on="date", how="left")
print(f"trades joined: {len(j)}, missing breadth: {j['breadth_pct'].isna().sum()}")

print("\n== (a) reproduce terciles (CR says: low -0.48, mid -0.51, high -0.03, win 29/25/44) ==")
bq = j["breadth_pct"].quantile([1/3, 2/3]).round(1)
print(f"tercile boundaries: {bq.iloc[0]} / {bq.iloc[1]}")
for lo, hi, lbl in [(-1, bq.iloc[0], "LOW"), (bq.iloc[0], bq.iloc[1], "MID"), (bq.iloc[1], 999, "HIGH")]:
    g = j[(j["breadth_pct"] > lo) & (j["breadth_pct"] <= hi)] if hi < 900 else j[j["breadth_pct"] > lo]
    print(f"  {lbl:4s} [{lo:.0f},{hi:.0f}]: n={len(g):3d} win={g['win'].mean():.1%} ret={g['ret_pct'].mean():+.3f}%")

print("\n== (b) threshold sweep (every 2.5pp from 55 to 87.5; spread = ret(>=thr) - ret(<thr)) ==")
for thr in np.arange(55, 88.5, 2.5):
    inn, out = j[j["breadth_pct"] >= thr], j[j["breadth_pct"] < thr]
    if len(inn) < 15:
        print(f"  >= {thr:5.1f}%: n_in={len(inn):3d}  (too small)")
        continue
    sp = inn["ret_pct"].mean() - out["ret_pct"].mean()
    print(f"  >= {thr:5.1f}%: n_in={len(inn):3d} win_in={inn['win'].mean():.1%} ret_in={inn['ret_pct'].mean():+.3f}% | spread={sp:+.3f}pp")

print("\n== (c) per-year stability of the >=77.3% edge ==")
for y in sorted(j["year"].unique()):
    gy = j[j["year"] == y]
    hi, lo = gy[gy["breadth_pct"] >= 77.333], gy[gy["breadth_pct"] < 77.333]
    print(f"  {y}: high n={len(hi):3d} ret={hi['ret_pct'].mean() if len(hi) else float('nan'):+.3f}% | "
          f"low n={len(lo):3d} ret={lo['ret_pct'].mean() if len(lo) else float('nan'):+.3f}%")

print("\n== (d) index-drift control: breadth tercile vs forward NIFTY500 return over the trade window ==")
for lo, hi, lbl in [(-1, bq.iloc[0], "LOW"), (bq.iloc[0], bq.iloc[1], "MID"), (bq.iloc[1], 999, "HIGH")]:
    g = j[(j["breadth_pct"] > lo) & (j["breadth_pct"] <= hi)] if hi < 900 else j[j["breadth_pct"] > lo]
    print(f"  {lbl:4s}: mean N500 fwd ret over trade window = {g['n500_fwd_pct'].mean():+.3f}%")

print("\n== (e) beta decomposition: ret_pct ~ a + b * N500_fwd ==")
jj = j.dropna(subset=["n500_fwd_pct"])
hi = jj[jj["breadth_pct"] >= 77.333]
lo = jj[jj["breadth_pct"] < 77.333]
for lbl, g in [("HIGH", hi), ("BELOW", lo), ("ALL", jj)]:
    x, y = g["n500_fwd_pct"].values, g["ret_pct"].values
    b, a = np.polyfit(x, y, 1)
    resid = y - (a + b * x)
    print(f"  {lbl:5s}: alpha={a:+.3f}%/trade  beta={b:+.2f}  resid_mean={resid.mean():+.3f}%  n={len(g)}")

print("\n== (f) unconditional session distribution: how much of the time is breadth >= 75/77.3? ==")
rr = reg[reg["date"] >= pd.Timestamp("2023-01-02").date()]
for thr in (75.0, 77.333):
    share = (rr["breadth_pct"] >= thr).mean()
    print(f"  sessions 2023-2026 with breadth >= {thr}: {share:.1%} ({int((rr['breadth_pct'] >= thr).sum())}/{len(rr)})")

# persistence: how many consecutive-session runs at >=77.3 (turnover/whipsaw estimate)
rr2 = rr.sort_values("date").reset_index(drop=True)
rr2["hi"] = (rr2["breadth_pct"] >= 77.333).astype(int)
runs, cur = [], 0
for v in rr2["hi"]:
    if v: cur += 1
    elif cur: runs.append(cur); cur = 0
if cur: runs.append(cur)
runs = np.array(runs) if runs else np.array([0])
print(f"  high-breadth runs: n={len(runs)}, median len={np.median(runs):.0f}, mean={runs.mean():.1f}, p90={np.percentile(runs,90):.0f}")
con.close()
