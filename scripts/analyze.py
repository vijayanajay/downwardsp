"""Step 2/3 — Event-study engine: MFE / MAE / first-passage over a 5-day horizon.

For every (stock, day) entry at close T, we look forward over the next
HORIZON trading sessions and compute:
  - MFE  max favorable excursion  = max( high / entry - 1 )   -> upside reached
  - MAE  max adverse excursion    = min( low  / entry - 1 )   -> downside suffered
  - first-passage per target: did price touch +TARGET% before touching -STOP%?
    Day-resolution: first touch wins; same-day tie counts as STOP (conservative).
  - outcome accounting per target:
      win        -> +target gross
      stop_first -> -stop gross
      timeout    -> close(T+H)/entry - 1 (marked to market at horizon end)
    then minus round-trip friction, and 20% STCG on positive nets.

Aggregate tables written to data/results/:
  targets.csv      pooled P(MFE>=X), P(fp win), breakeven p, edge, exp net, shots/yr
  by_year.csv      same by entry year          by_regime.csv  by NIFTY 20EMA state
  by_dow.csv       same by weekday
  stock_summary.csv  per-stock hits per target, MFE stats, ATR%, ADTV
  repeats.csv      durable repeaters at the pooled sweet-spot target

Usage:  python scripts/analyze.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
OHLCV_DIR = ROOT / "data" / "ohlcv"
OUT_DIR = ROOT / "data" / "results"
CFG = yaml.safe_load((ROOT / "scripts" / "study_config.yaml").read_text())

HORIZON = int(CFG["study"]["horizon_days"])
STOP = float(CFG["study"]["stop_pct"]) / 100.0
TARGETS = [t / 100.0 for t in CFG["study"]["targets_pct"]]
FRICTION = float(CFG["economics"]["friction_pct"]) / 100.0
STCG = float(CFG["economics"]["stcg_pct"]) / 100.0
DURABILITY_YEARS = int(CFG["quality"]["durability_min_years"])
DURABILITY_MIN_HITS = int(CFG["quality"]["durability_min_hits_per_year"])
DOW_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri"]


def breakeven_hit_rate(target: float, stop: float, friction: float, stcg: float) -> float:
    """Hit probability needed for zero net expectancy with 1:0 fixed exits."""
    net_win = target - friction
    net_loss = stop + friction
    p_brk = net_loss / (net_win + net_loss)          # gross breakeven
    return p_brk / (1.0 - stcg)                      # lift for 20% STCG on wins


BE_RATES = {t: breakeven_hit_rate(t, STOP, FRICTION, STCG) for t in TARGETS}

# A repeater is a *candidate pool*, not a buy list: the table must carry the
# breakeven bar and the per-stock edge so nobody reads high P(win) as edge.
# See docs/FINDINGS_Weekly_Sweet_Spot_Study.md §8.
TRADEABLE_ADTV_CR = float(CFG["quality"].get("tradeable_adtv_cr", 50.0))


# ---------------------------------------------------------------------------
# Per-stock event table (numpy, day-resolution first-passage)
# ---------------------------------------------------------------------------

def events_for_symbol(sym: str, path: Path) -> pd.DataFrame | None:
    df = pd.read_parquet(path)
    if len(df) < 120:
        return None

    close = df["close"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    n = len(df)
    m = n - HORIZON  # rows with a complete forward window

    entry = close[:m]
    # forward windows, shape (m, H): fut_high[k, j] = high[k + j + 1]
    fut_high = np.stack([high[j + 1 : j + 1 + m] for j in range(HORIZON)], axis=1)
    fut_low = np.stack([low[j + 1 : j + 1 + m] for j in range(HORIZON)], axis=1)

    mfe = fut_high.max(axis=1) / entry - 1.0
    mae = fut_low.min(axis=1) / entry - 1.0
    ret_h = close[HORIZON:] / entry - 1.0  # close at T+H vs entry

    # daily true range -> ATR% known at entry time
    prev_close = np.concatenate([[close[0]], close[:-1]])
    tr = np.maximum.reduce([high - low, np.abs(high - prev_close), np.abs(low - prev_close)])
    atr14 = pd.Series(tr).rolling(14).mean().to_numpy()
    atr_pct = (atr14 / close)[:m]

    idx = df.index[:m]
    out = pd.DataFrame({
        "date": idx,
        "symbol": sym,
        "entry": entry,
        "mfe": mfe,
        "mae": mae,
        "ret_5d": ret_h,
        "atr_pct": atr_pct,
        "dow": idx.dayofweek,
        "year": idx.year,
    })

    # first-passage with day resolution; same-day tie -> stop wins (conservative)
    stop_hit = fut_low / entry[:, None] - 1.0 <= -STOP  # (m, H) bool
    first_dn = np.where(stop_hit.any(axis=1), stop_hit.argmax(axis=1), HORIZON)

    for t in TARGETS:
        up_hit = fut_high / entry[:, None] - 1.0 >= t
        first_up = np.where(up_hit.any(axis=1), up_hit.argmax(axis=1), HORIZON)
        win = (first_up < first_dn).astype(int)
        stop_first = (first_dn < first_up).astype(int)
        out[f"fp_{t:.3f}"] = win
        out[f"sf_{t:.3f}"] = stop_first
    return out


def build_events() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    paths = sorted(p for p in OHLCV_DIR.glob("*.parquet") if p.name != "_nifty.parquet")

    # honor the download audit: skip symbols with <80% bar coverage / failed downloads
    ok_syms: set[str] | None = None
    audit_path = ROOT / "data" / "universe_downloaded.csv"
    if audit_path.exists():
        audit = pd.read_csv(audit_path)
        ok_syms = set(audit.loc[audit["ok"].astype(bool), "symbol"])
        print(f"audit: {len(ok_syms)} usable of {len(audit)} downloaded symbols")

    print(f"processing {len(paths)} symbols ...")
    for p in paths:
        sym = p.stem.replace("_", ".")
        if ok_syms is not None and sym not in ok_syms:
            continue
        try:
            ev = events_for_symbol(sym, p)
            if ev is not None and len(ev):
                frames.append(ev)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {p.stem}: {exc}")
    if not frames:
        raise SystemExit("No event data — run scripts/download_data.py first.")
    ev = pd.concat(frames, ignore_index=True)
    return ev.dropna(subset=["date", "dow", "entry", "mfe", "ret_5d"])


# ---------------------------------------------------------------------------
# Aggregations
# ---------------------------------------------------------------------------

def outcome_pnl(ev: pd.DataFrame, t: float) -> np.ndarray:
    """Gross per-event P&L fraction for a fixed target/stop exit."""
    win = ev[f"fp_{t:.3f}"].to_numpy(dtype=bool)
    sf = ev[f"sf_{t:.3f}"].to_numpy(dtype=bool)
    return np.where(win, t, np.where(sf, -STOP, ev["ret_5d"].to_numpy(dtype=float)))


def agg_table(ev: pd.DataFrame) -> pd.DataFrame:
    rows = []
    n = len(ev)
    for t in TARGETS:
        p_fp = float(ev[f"fp_{t:.3f}"].mean())
        pnl = outcome_pnl(ev, t)
        net = pnl - FRICTION
        net_after_tax = np.where(net > 0, net * (1.0 - STCG), net)
        p_be = BE_RATES[t]
        rows.append({
            "target_pct": round(t * 100, 2),
            "events": n,
            "p_mfe_ge": round(float((ev["mfe"] >= t).mean()), 4),
            "p_fp_win": round(p_fp, 4),
            "breakeven_p": round(p_be, 4),
            "edge": round(p_fp - p_be, 4),
            "exp_net_pct": round(float(net_after_tax.mean()) * 100, 3),
            "shots_per_year": round(n * p_fp / 5, 1),  # events / ~5y window
        })
    return pd.DataFrame(rows)


def agg_by(ev: pd.DataFrame, key: str, colname: str) -> pd.DataFrame:
    frames = []
    for name, g in ev.groupby(key, sort=True):
        t = agg_table(g)
        t.insert(0, colname, name)
        frames.append(t)
    return pd.concat(frames, ignore_index=True)


def regime_flags(nifty: pd.DataFrame | None) -> pd.DataFrame:
    if nifty is None or len(nifty) < 30:
        return pd.DataFrame(columns=["date", "regime"])
    ema20 = nifty["close"].ewm(span=20, adjust=False).mean()
    return pd.DataFrame({
        "date": nifty.index,
        "regime": np.where(nifty["close"].to_numpy() > ema20.to_numpy(), "risk_on", "risk_off"),
    })


def stock_summary(ev: pd.DataFrame) -> pd.DataFrame:
    best = max(TARGETS, key=lambda t: (ev[f"fp_{t:.3f}"].mean() - BE_RATES[t]))
    best_col = f"fp_{best:.3f}"
    grp = ev.groupby("symbol")
    out = grp.agg(
        events=("mfe", "size"),
        median_mfe=("mfe", "median"),
        p90_mfe=("mfe", lambda s: s.quantile(0.90)),
        median_atr_pct=("atr_pct", "median"),
        median_mae=("mae", "median"),
    )
    for t in TARGETS:
        out[f"hits_{t*100:g}"] = grp[f"fp_{t:.3f}"].sum()
    out["best_target"] = round(best * 100, 2)
    out["best_fp_rate"] = grp[best_col].mean().round(4)
    out["best_edge"] = (out["best_fp_rate"] - BE_RATES[best]).round(4)
    # durability: #years with >= DURABILITY_MIN_HITS fp-wins at the pooled best target
    yrs = (ev.assign(hit=ev[best_col])
             .groupby(["symbol", "year"])["hit"].sum()
             .reset_index())
    yrs["durable"] = yrs["hit"] >= DURABILITY_MIN_HITS
    out["durable_years"] = yrs.groupby("symbol")["durable"].sum()
    return out


def repeats_table(stock: pd.DataFrame, ev: pd.DataFrame, target: float) -> pd.DataFrame:
    """Durable repeaters at `target`: stocks hitting it in >= DURABILITY_YEARS years.

    The table is a candidate pool for conditional studies, not a buy list —
    every row carries its own breakeven bar and edge so that is visible.
    """
    col = f"fp_{target:.3f}"
    per_year = (ev.assign(hit=ev[col])
                  .groupby(["symbol", "year"])["hit"].sum().reset_index())
    dur = (per_year.assign(d=per_year["hit"] >= DURABILITY_MIN_HITS)
                   .groupby("symbol")["d"].sum())
    rep = stock.copy()
    rep["durable_years_at_target"] = dur
    rep["fp_rate_at_target"] = ev.groupby("symbol")[col].mean().round(4)
    rep["breakeven_at_target"] = round(BE_RATES[target], 4)
    rep["edge_at_target"] = (rep["fp_rate_at_target"] - rep["breakeven_at_target"]).round(4)
    rep["tradeable"] = rep["adtv_cr"] >= TRADEABLE_ADTV_CR
    rep = rep[rep["durable_years_at_target"] >= DURABILITY_YEARS]
    # Most-consistent first (6/6 years), then least-negative edge within each
    # consistency tier. Sorting by win rate alone put a volatility list first.
    return rep.sort_values(
        ["durable_years_at_target", "edge_at_target", "fp_rate_at_target"],
        ascending=[False, False, False],
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ev = build_events()
    print(f"event rows: {len(ev):,} | span {ev['date'].min().date()} .. {ev['date'].max().date()}")

    tgt = agg_table(ev)
    tgt.to_csv(OUT_DIR / "targets.csv", index=False)
    print("\n== pooled targets table ==")
    print(tgt.to_string(index=False))

    by_year = agg_by(ev, "year", "year")
    by_year.to_csv(OUT_DIR / "by_year.csv", index=False)

    nifty_path = OHLCV_DIR / "_nifty.parquet"
    nifty = pd.read_parquet(nifty_path) if nifty_path.exists() else None
    reg = regime_flags(nifty)
    if len(reg):
        ev2 = ev.merge(reg, on="date", how="inner")
        by_regime = agg_by(ev2, "regime", "regime")
        by_regime.to_csv(OUT_DIR / "by_regime.csv", index=False)

    dow = agg_by(ev, "dow", "dow")
    dow["dow"] = dow["dow"].map(dict(enumerate(DOW_NAMES)))
    dow.to_csv(OUT_DIR / "by_dow.csv", index=False)

    stock = stock_summary(ev)
    adtv = {}
    for p in OHLCV_DIR.glob("*.parquet"):
        if p.name == "_nifty.parquet":
            continue
        sym = p.stem.replace("_", ".")
        df = pd.read_parquet(p, columns=["close", "volume"])
        adtv[sym] = float((df["close"] * df["volume"]).rolling(90).mean().median())
    stock["adtv_cr"] = stock.index.map(lambda s: round(adtv.get(s, np.nan) / 1e7, 2))
    stock = stock.reset_index()
    stock.to_csv(OUT_DIR / "stock_summary.csv", index=False)

    sweet_target = float(tgt.loc[tgt["exp_net_pct"].idxmax(), "target_pct"]) / 100.0
    rep = repeats_table(stock.set_index("symbol"), ev, sweet_target)
    n_tradeable = int(rep["tradeable"].sum())
    print(f"\nstock_summary: {len(stock)} stocks | durable repeaters @{sweet_target:.1%}: {len(rep)} "
          f"(tradeable >=Rs{TRADEABLE_ADTV_CR:g}cr ADTV: {n_tradeable}; "
          f"none beat breakeven: {bool((rep['edge_at_target'] < 0).all())})")
    rep.reset_index().to_csv(OUT_DIR / "repeats.csv", index=False)

    best_row = tgt.loc[tgt["exp_net_pct"].idxmax()]
    edge_row = tgt.loc[tgt["edge"].idxmax()]
    print("\n== SWEET SPOT (pooled, by net expectancy) ==")
    print(f"   target {best_row['target_pct']}%  p_win={best_row['p_fp_win']:.3f}  "
          f"breakeven={best_row['breakeven_p']:.3f}  edge={best_row['edge']:.3f}  "
          f"exp_net={best_row['exp_net_pct']}%  shots/yr={best_row['shots_per_year']}")
    print(f"   (best win-rate edge is at {edge_row['target_pct']}%: "
          f"edge={edge_row['edge']:.3f})")

    ev.to_parquet(OUT_DIR / "events.parquet", index=False)
    print(f"\nwrote {OUT_DIR}/*.csv + events.parquet")


if __name__ == "__main__":
    main()
