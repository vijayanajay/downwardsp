"""Signal-cohort forward-return curves beyond Day 5 (CR-2026-003 exit geometry).

Question the realized book cannot answer (its exits truncate everything at
~5 sessions): what do the setups' signal cohorts actually DO over the next
2/5/10/20/40/60 sessions? This study replays the funnel's signal days exactly
like the engine (same regime filter, same features, same predicate + gate
config), fills at T+1 open like the engine, then simply... holds, recording
per horizon:

  - close_H:  forward return of the T+H close vs the T+1 open (post-friction,
    post-STCG arithmetic included: net = gross - 0.35%, taxed 20% when positive)
  - mfe_H / mae_H: best/worst excursion within H sessions (mae positive = adverse)
  - the T1(+2%) vs structural-stop race within H, and which touched first
  - P(stop touched at all), P(mae > 2.2%) — the -2.66% median-MAE wall question

Scan-day thinning: the engine scans every session; sampling every 5th session
(offset 0) yields an unbiased cohort at ~1/5 the cost. --offsets selects more.

Read-only vs market data except engine.warmup's own healing (PIT/features).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from nse_cash.backtest.engine import BarCache, warmup  # noqa: E402
from nse_cash.core.config import SystemConfig  # noqa: E402
from nse_cash.core.governance import excluded_symbols  # noqa: E402
from nse_cash.data.storage import MarketStore  # noqa: E402
from nse_cash.setups.features import load_features, refresh_features  # noqa: E402
from nse_cash.setups.ranking import evaluate_and_rank  # noqa: E402

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger("cohort")

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "reports" / "cr003" / "exit_geometry"

HORIZONS = (2, 5, 10, 20, 40, 60)
ROUND_TRIP_FRICTION = 0.0035   # engine's buy+sell friction ballpark (0.35%)
STCG = 0.20


def net_after_tax(gross: float) -> float:
    net = gross - ROUND_TRIP_FRICTION
    return net * (1.0 - STCG) if net > 0 else net


def collect_signals(store: MarketStore, cfg: SystemConfig,
                    start: date, end: date, offsets: list[int]):
    """Replay the funnel on sampled sessions; one row per candidate signal."""
    warm = warmup(store, start, end)
    dates = store.trading_dates(start=start, end=end)
    pos_of = {d: i for i, d in enumerate(dates)}
    rdf = warm.regime_df
    offensive = set(pd.to_datetime(
        rdf.loc[rdf["state"].astype(str).str.contains("OFFENSIVE"), "date"]).dt.date)
    breadth = {pd.Timestamp(r["date"]).date(): r.get("breadth_pct")
               for _, r in rdf.iterrows()}

    cache = BarCache(store, start, end)
    rows = []
    for offset in offsets:
        for i, d in enumerate(dates):
            if i % 5 != offset or d not in offensive:
                continue
            excl = excluded_symbols(store.con, d)
            feat = load_features(store, d)
            if feat.empty:
                refresh_features(store, d)
                feat = load_features(store, d)
                if feat.empty:
                    continue
            if "close_raw" in feat.columns:
                feat["close_raw"] = feat["close_raw"].fillna(feat["close_adj"])
            cands = evaluate_and_rank(
                feat[~feat["symbol"].isin(excl)],
                nifty50_above_ema=True, config=cfg)
            for c in cands:
                e_idx = pos_of[d] + 1                 # entry = T+1 (engine parity)
                if e_idx >= len(dates):
                    continue
                rows.append({
                    "signal_date": d, "symbol": c.symbol, "setup": c.setup.value,
                    "entry_ref": c.entry_ref, "stop": c.structural_stop,
                    "stop_pct": c.structural_stop_pct, "s_runner": c.s_runner,
                    "entry_idx": e_idx, "breadth_pct": breadth.get(d),
                })
    return pd.DataFrame(rows), dates, cache


def forward_rows(signals: pd.DataFrame, dates: list, cache: BarCache) -> pd.DataFrame:
    out = []
    for sig in signals.itertuples():
        entry_day = dates[sig.entry_idx]
        bar0 = cache.bar(sig.symbol, entry_day)
        if bar0 is None:
            continue
        o = float(bar0["open"])
        rec = {"signal_date": sig.signal_date, "symbol": sig.symbol,
               "setup": sig.setup, "entry_ref": sig.entry_ref,
               "stop_pct": sig.stop_pct, "s_runner": sig.s_runner,
               "breadth_pct": sig.breadth_pct, "entry_open": o}
        touched_t1_day = touched_stop_day = None
        for H in HORIZONS:
            last = min(sig.entry_idx + H - 1, len(dates) - 1)
            highs, lows = [], []
            complete = True
            for k in range(sig.entry_idx, last + 1):
                b = cache.bar(sig.symbol, dates[k])
                if b is None:
                    complete = False
                    break
                highs.append(float(b["high"]))
                lows.append(float(b["low"]))
                day_no = k - sig.entry_idx + 1
                if touched_t1_day is None and highs[-1] >= o * 1.02:
                    touched_t1_day = day_no
                if touched_stop_day is None and lows[-1] <= sig.stop:
                    touched_stop_day = day_no
            if not complete or len(highs) < H:
                rec[f"close_{H}"] = np.nan
                rec[f"mfe_{H}"] = np.nan
                rec[f"mae_{H}"] = np.nan
                continue
            rec[f"close_{H}"] = float(cache.bar(sig.symbol, dates[last])["close"]) / o - 1.0
            rec[f"mfe_{H}"] = max(highs) / o - 1.0
            rec[f"mae_{H}"] = 1.0 - min(lows) / o
        rec["t1_touch_day"] = touched_t1_day
        rec["stop_touch_day"] = touched_stop_day
        out.append(rec)
    return pd.DataFrame(out)


def summarize(fw: pd.DataFrame) -> dict:
    summary = {}
    for setup, g in fw.groupby("setup"):
        s = {"n_signals": int(len(g))}
        for H in HORIZONS:
            col = f"close_{H}"
            v = g[col].dropna()
            if v.empty:
                continue
            net = v.map(net_after_tax)
            s[f"H{H}"] = {
                "n": int(len(v)),
                "gross_med": round(float(v.median()), 5),
                "gross_mean": round(float(v.mean()), 5),
                "win_rate": round(float((v > 0).mean()), 4),
                "net_mean_per_trade_rs": round(float(net.mean() * 62_500), 0),
                "mfe_med": round(float(g[f"mfe_{H}"].dropna().median()), 5),
                "mae_med": round(float(g[f"mae_{H}"].dropna().median()), 5),
                "p_mae_gt_2p2": round(float((g[f"mae_{H}"].dropna() > 0.022).mean()), 4),
                "p_t1_first": round(float((g["t1_touch_day"].notna()
                                           & (g["t1_touch_day"] <= H)
                                           & (g["stop_touch_day"].isna()
                                              | (g["stop_touch_day"] > g["t1_touch_day"]))).mean()), 4),
            }
        summary[setup] = s
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2011-09-19")
    ap.add_argument("--end", default="2026-09-17")
    ap.add_argument("--offsets", default="0")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    offsets = [int(x) for x in args.offsets.split(",")]
    start = pd.Timestamp(args.start).date()
    end = pd.Timestamp(args.end).date()

    store = MarketStore(ROOT / "data" / "db" / "nse_market.duckdb", read_only=False)
    try:
        cfg = SystemConfig()   # arm C semantics: S2 pruned, S_runner as sort key
        signals, dates, cache = collect_signals(store, cfg, start, end, offsets)
        print(f"signals: {len(signals)} over {len(offsets)} offset(s) "
              f"({signals['setup'].value_counts().to_dict()})", flush=True)
        fw = forward_rows(signals, dates, cache)
        fw.to_parquet(out / "cohort_signals_forward_returns.parquet", index=False)
        summary = summarize(fw)
        (out / "cohort_summary.json").write_text(json.dumps(summary, indent=2))
        for setup, s in summary.items():
            print(f"\n== {setup} (n={s['n_signals']})")
            for H in HORIZONS:
                if f"H{H}" in s:
                    h = s[f"H{H}"]
                    print(f"  H={H:>2}: med {h['gross_med']*100:+.2f}%  "
                          f"win {h['win_rate']*100:4.1f}%  "
                          f"net/trade Rs{h['net_mean_per_trade_rs']:+8.0f}  "
                          f"mae_med {h['mae_med']*100:.2f}%  "
                          f"P(mae>2.2%) {h['p_mae_gt_2p2']*100:4.1f}%  "
                          f"P(T1 first) {h['p_t1_first']*100:4.1f}%")
    finally:
        store.close()


if __name__ == "__main__":
    main()
