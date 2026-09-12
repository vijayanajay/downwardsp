"""Follow-up study — ATR-normalized targets.

Re-expresses every target as a multiple K of each stock's 14-day ATR%
(known at entry time). Stop stays fixed at -2.2% (the BRD structural stop).

Questions answered:
  1. Does the sweet-spot curve (P(win), net expectancy) look different in
     ATR units vs fixed percent?
  2. Is the curve MORE STABLE in ATR units? Stability is measured two ways:
     - cross-stock dispersion of per-stock P(win) at an ATR target
       (compare with the fixed-% target of similar size)
     - year-to-year range of P(win) (compare fixed 2% vs 0.5 ATR)
     - consistency of the argmax-K (best target) across years
  3. Screen: stocks where 2% is a half-ATR move (median ATR% >= 4.0%).

Outputs: data/results/atr_targets.csv, atr_by_year.csv, atr_by_regime.csv,
         atr_stock_rates.csv, screen_half_atr.csv

Usage:  python scripts/analyze_atr.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from analyze import (CFG, FRICTION, HORIZON, OHLCV_DIR, OUT_DIR, ROOT, STOP,
                     STCG, regime_flags)

K_LIST = [0.25, 0.50, 0.75, 1.00, 1.50, 2.00, 3.00]
HALF_ATR_THRESHOLD = 0.04  # 2% = 0.5 * ATR  =>  ATR% >= 4.0%


# ---------------------------------------------------------------------------

def atr_events_for_symbol(sym: str, path: Path) -> pd.DataFrame | None:
    df = pd.read_parquet(path)
    if len(df) < 120:
        return None

    close = df["close"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    n = len(df)
    m = n - HORIZON

    entry = close[:m]
    fut_high = np.stack([high[j + 1 : j + 1 + m] for j in range(HORIZON)], axis=1)
    fut_low = np.stack([low[j + 1 : j + 1 + m] for j in range(HORIZON)], axis=1)

    prev_close = np.concatenate([[close[0]], close[:-1]])
    tr = np.maximum.reduce([high - low, np.abs(high - prev_close), np.abs(low - prev_close)])
    atr14 = pd.Series(tr).rolling(14).mean().to_numpy()
    atr = (atr14 / close)[:m]

    valid = np.isfinite(atr) & (atr > 0.002)  # need a usable ATR
    idx = df.index[:m]

    out = pd.DataFrame({
        "date": idx, "symbol": sym, "entry": entry,
        "atr_pct": atr, "mfe": fut_high.max(axis=1) / entry - 1.0,
        "ret_5d": close[HORIZON:] / entry - 1.0,
        "year": idx.year, "dow": idx.dayofweek,
    })

    stop_hit = fut_low / entry[:, None] - 1.0 <= -STOP
    first_dn = np.where(stop_hit.any(axis=1), stop_hit.argmax(axis=1), HORIZON)

    for k in K_LIST:
        t_e = (k * atr)[:, None]
        up_hit = fut_high / entry[:, None] - 1.0 >= t_e
        first_up = np.where(up_hit.any(axis=1), up_hit.argmax(axis=1), HORIZON)
        out[f"win_{k:g}"] = (first_up < first_dn).astype(int)
        out[f"sf_{k:g}"] = (first_dn < first_up).astype(int)

    out = out[valid]
    return out.iloc[:-HORIZON] if len(out) > HORIZON else None


def build_events() -> pd.DataFrame:
    ok_syms: set[str] | None = None
    audit_path = ROOT / "data" / "universe_downloaded.csv"
    if audit_path.exists():
        audit = pd.read_csv(audit_path)
        ok_syms = set(audit.loc[audit["ok"].astype(bool), "symbol"])
        print(f"audit: {len(ok_syms)} usable symbols")

    frames = []
    paths = sorted(p for p in OHLCV_DIR.glob("*.parquet") if p.name != "_nifty.parquet")
    print(f"processing {len(paths)} symbols ...")
    for p in paths:
        sym = p.stem.replace("_", ".")
        if ok_syms is not None and sym not in ok_syms:
            continue
        try:
            ev = atr_events_for_symbol(sym, p)
            if ev is not None and len(ev):
                frames.append(ev)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {p.stem}: {exc}")
    if not frames:
        raise SystemExit("No events — run scripts/download_data.py first.")
    ev = pd.concat(frames, ignore_index=True)
    return ev.dropna(subset=["date", "atr_pct", "mfe", "ret_5d"])


# ---------------------------------------------------------------------------

def exp_net(ev: pd.DataFrame, k: float) -> float:
    win = ev[f"win_{k:g}"].to_numpy(dtype=bool)
    sf = ev[f"sf_{k:g}"].to_numpy(dtype=bool)
    t_e = k * ev["atr_pct"].to_numpy(dtype=float)
    pnl = np.where(win, t_e, np.where(sf, -STOP, ev["ret_5d"].to_numpy(dtype=float)))
    net = pnl - FRICTION
    net = np.where(net > 0, net * (1.0 - STCG), net)
    return float(net.mean())


def breakeven_mean(ev: pd.DataFrame, k: float) -> float:
    t_e = k * ev["atr_pct"].to_numpy(dtype=float)
    p_e = ((STOP + FRICTION) / (t_e + FRICTION)) / (1.0 - STCG)
    return float(np.clip(p_e, 0.0, 1.0).mean())


def agg_table(ev: pd.DataFrame) -> pd.DataFrame:
    n = len(ev)
    rows = []
    for k in K_LIST:
        p_win = float(ev[f"win_{k:g}"].mean())
        rows.append({
            "k_atr": k,
            "median_target_pct": round(float((k * ev["atr_pct"]).median()) * 100, 2),
            "events": n,
            "p_mfe_ge_k_atr": round(float((ev["mfe"] / ev["atr_pct"] >= k).mean()), 4),
            "p_fp_win": round(p_win, 4),
            "breakeven_p_mean": round(breakeven_mean(ev, k), 4),
            "exp_net_pct": round(exp_net(ev, k) * 100, 3),
            "shots_per_year": round(n * p_win / 5, 1),
        })
    return pd.DataFrame(rows)


def agg_by(ev: pd.DataFrame, key: str, colname: str) -> pd.DataFrame:
    frames = []
    for name, g in ev.groupby(key, sort=True):
        t = agg_table(g)
        t.insert(0, colname, name)
        frames.append(t)
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------

def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ev = build_events()
    print(f"event rows: {len(ev):,} | median ATR% {ev['atr_pct'].median()*100:.2f} | "
          f"span {ev['date'].min().date()} .. {ev['date'].max().date()}")

    tgt = agg_table(ev)
    tgt.to_csv(OUT_DIR / "atr_targets.csv", index=False)
    print("\n== pooled ATR-target curve (stop fixed -2.2%) ==")
    print(tgt.to_string(index=False))

    # --- stability slices ----------------------------------------------------
    by_year = agg_by(ev, "year", "year")
    by_year.to_csv(OUT_DIR / "atr_by_year.csv", index=False)

    nifty_path = OHLCV_DIR / "_nifty.parquet"
    if nifty_path.exists():
        reg = regime_flags(pd.read_parquet(nifty_path))
        if len(reg):
            ev2 = ev.merge(reg, on="date", how="inner")
            agg_by(ev2, "regime", "regime").to_csv(OUT_DIR / "atr_by_regime.csv", index=False)

    argmax_year = by_year.loc[by_year.groupby("year")["exp_net_pct"].idxmax(), ["year", "k_atr", "exp_net_pct"]]
    print("\nargmax-K (best net expectancy) by year:")
    print(argmax_year.to_string(index=False))

    # --- cross-stock dispersion: ATR units vs fixed percent ------------------
    per_stock = ev.groupby("symbol").agg(
        events=("mfe", "size"),
        atr_pct=("atr_pct", "median"),
        **{f"win_{k:g}": (f"win_{k:g}", "mean") for k in K_LIST},
    )
    # fixed-% rates from the base study for comparison
    fixed = pd.read_csv(OUT_DIR / "stock_summary.csv").set_index("symbol")
    cmp_rows = []
    for label, col in [
        ("fixed 1.5%", "hits_1.5"), ("fixed 2%", "hits_2"), ("fixed 2.5%", "hits_2.5"),
        ("0.50 ATR", "win_0.5"), ("0.75 ATR", "win_0.75"), ("1.00 ATR", "win_1"),
    ]:
        if col in fixed.columns:
            rate = (fixed[col] / fixed["events"]).dropna()
        else:
            rate = per_stock[col].dropna()
        q1, q3 = rate.quantile([0.25, 0.75])
        cmp_rows.append({"target": label, "pooled": round(rate.mean(), 4),
                         "iqr_lo": round(q1, 4), "iqr_hi": round(q3, 4),
                         "iqr_pp": round((q3 - q1) * 100, 2), "std_pp": round(rate.std() * 100, 2)})
    cmp_df = pd.DataFrame(cmp_rows)
    cmp_df.to_csv(OUT_DIR / "atr_stock_rates.csv", index=False)
    print("\n== cross-stock dispersion: fixed % vs ATR units ==")
    print(cmp_df.to_string(index=False))

    yr_fixed = pd.read_csv(OUT_DIR / "by_year.csv")
    yr_atr = by_year
    f2 = yr_fixed[yr_fixed["target_pct"] == 2.0]["p_fp_win"]
    a5 = yr_atr[yr_atr["k_atr"] == 0.50]["p_fp_win"]
    print(f"\nyear range of P(win): fixed 2% = {(f2.max()-f2.min())*100:.1f}pp | "
          f"0.5 ATR = {(a5.max()-a5.min())*100:.1f}pp")

    # --- half-ATR screen -----------------------------------------------------
    per_stock = per_stock.join(fixed[["hits_2", "adtv_cr", "durable_years"]])
    per_stock["rate_2pct"] = per_stock["hits_2"] / fixed["events"]
    screen = per_stock[per_stock["atr_pct"] >= HALF_ATR_THRESHOLD].copy()
    screen["target_2pct_is_half_atr"] = True
    screen = screen.rename(columns={"win_0.5": "p_win_0.5atr", "win_0.75": "p_win_0.75atr",
                                    "win_1": "p_win_1atr"})
    screen = screen.sort_values("adtv_cr", ascending=False)
    screen.reset_index().to_csv(OUT_DIR / "screen_half_atr.csv", index=False)
    tradable = int((screen["adtv_cr"] >= 50).sum())
    print(f"\n== half-ATR screen (median ATR% >= {HALF_ATR_THRESHOLD:.0%}) ==")
    print(f"qualifiers: {len(screen)} of {len(per_stock)} stocks | ADTV >= 50cr: {tradable}")
    print(screen[["atr_pct", "p_win_0.5atr", "rate_2pct", "adtv_cr"]].head(15).to_string())

    ev.to_parquet(OUT_DIR / "events_atr.parquet", index=False)
    print(f"\nwrote {OUT_DIR}/atr_*.csv, screen_half_atr.csv, events_atr.parquet")


if __name__ == "__main__":
    main()
