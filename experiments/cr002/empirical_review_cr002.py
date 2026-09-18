"""Empirical probes for CR-2026-002 (read-only, walk-forward window 2023+).

CR-002 §4 mandates an "Empirical Validation Funnel" BEFORE implementation.
This script is funnel step 1: measure every Issue's claim on the production
store (data/db/nse_market.duckdb, 6.5M bar rows) using the engine's OWN code
(`_add_symbol_features`, `compute_s_runner`, `evaluate_market_regime_range`)
so probe numbers are implementation-exact, not re-derivations.

Entry model for all forward-looking sims (matches scripts/analyze.py, the
study the CR cites): buy at Close_T, first-passage over the next 5 sessions
at day resolution, same-day tie counts as a STOP (conservative), timeout
marked at T+5 close. Friction 0.35% round trip, 20% STCG on net wins.

Run:  .venv/Scripts/python.exe experiments/cr002/empirical_review_cr002.py
Writes experiments/cr002/probe_output.txt.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import duckdb

DB = ROOT / "data" / "db" / "nse_market.duckdb"
OUT = Path(__file__).with_name("probe_output.txt")
D0 = "2023-01-01"            # BRD §10.1 walk-forward start
WARM = "2021-01-01"          # SMA200/52w-high warm-up so features are live on day 1
END = "2026-09-17"
FRICTION, STCG = 0.0035, 0.20
H = 5                        # forward horizon, sessions

_buf = io.StringIO()


def emit(line: str = "") -> None:
    print(line)
    _buf.write(line + "\n")


con = duckdb.connect(str(DB), read_only=True)
con.execute("SET memory_limit='4GB'; SET threads=4;")


def net_after_costs(pnl: np.ndarray) -> np.ndarray:
    net = pnl - FRICTION
    return np.where(net > 0, net * (1.0 - STCG), net)


def fp_outcome(df: pd.DataFrame, target: float = 0.02,
               stop_frac: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Day-resolution first passage. stop_frac: per-row stop distance below
    entry as a positive fraction (default 0.022). Returns (win, stop_first, pnl)."""
    entry = df["close_adj"].to_numpy(dtype=float)
    ups = np.stack([df[f"h{k}"].to_numpy(dtype=float) / entry - 1.0 for k in range(1, H + 1)], axis=1)
    dns = np.stack([df[f"l{k}"].to_numpy(dtype=float) / entry - 1.0 for k in range(1, H + 1)], axis=1)
    sf_level = np.full(len(df), 0.022) if stop_frac is None else np.asarray(stop_frac, dtype=float)
    up_hit = ups >= target
    dn_hit = dns <= -sf_level[:, None]
    first_up = np.where(up_hit.any(1), up_hit.argmax(1), H)
    first_dn = np.where(dn_hit.any(1), dn_hit.argmax(1), H)
    win = first_up < first_dn
    sf = first_dn < first_up
    timeout_ret = df["c5"].to_numpy(dtype=float) / entry - 1.0
    pnl = np.where(win, target, np.where(sf, -sf_level, timeout_ret))
    return win, sf, pnl


def report_cohort(df: pd.DataFrame, label: str, stop_frac=None) -> None:
    if df.empty:
        emit(f"    {label:<38s} n=0")
        return
    win, sf, pnl = fp_outcome(df, stop_frac=stop_frac)
    net = net_after_costs(pnl)
    s = (np.full(len(df), 0.022) if stop_frac is None
         else np.asarray(stop_frac, dtype=float))
    be = (s + 0.0035) / ((0.02 - 0.0035) + (s + 0.0035)) / 0.8
    emit(f"    {label:<38s} n={len(df):5d} | P(win)={win.mean():5.1%} | P(stop)={sf.mean():5.1%} "
         f"| E[net]={net.mean()*100:+8.4f}%/trade | BE p~{be.mean():4.0%} | med MFE={df['mfe5'].median():+6.2%}")


def s_runner_vec(z: pd.Series, m: pd.Series, pv: pd.Series) -> pd.Series:
    """Vectorized mirror of ranking.compute_s_runner (scalar fn): NaN z->0,
    NaN m->0, NaN pv->1.0, z winsorized [0,3] normalized by 3."""
    z_norm = z.clip(lower=0, upper=3.0).fillna(0.0) / 3.0
    m_ = m.fillna(0.0)
    pv_ = pv.fillna(1.0)
    return 0.35 * z_norm + 0.35 * m_ + 0.30 * (1.0 - pv_)


# =====================================================================
# P0. Regime distribution: binary engine vs CR's proposed tri-state
# =====================================================================
def p0_regime() -> pd.DataFrame:
    emit("=" * 78)
    emit("P0. Regime state distribution — Issue 1's 'binary choke' claim")
    from nse_cash.data.storage import MarketStore
    from nse_cash.funnel.market_regime import evaluate_market_regime_range

    store = MarketStore(DB, read_only=True)
    reg = evaluate_market_regime_range(store, start_date=pd.Timestamp(D0).date(),
                                       end_date=pd.Timestamp(END).date())
    reg["date"] = pd.to_datetime(reg["date"]).dt.date
    n = len(reg)
    off = int((reg["state"] == "OFFENSIVE_LONG").sum())
    emit(f"  sessions [{reg['date'].min()} .. {reg['date'].max()}]: {n}")
    emit(f"  engine OFFENSIVE_LONG : {off:4d} ({off/n:.1%})  <- all setups evaluated")
    emit(f"  engine DEFENSIVE_CASH : {n-off:4d} ({(n-off)/n:.1%})  <- zero setups evaluated")
    ne = int((~reg["nifty50_above_ema"]).sum())
    nb = int((~reg["breadth_above_50"]).sum())
    emit(f"  failure causes: nifty<=EMA20 {ne} ({ne/n:.1%}) | breadth<=50 {nb} ({nb/n:.1%})")

    nifty = con.execute("""
        SELECT date, close FROM market_indices WHERE index_name='NIFTY 50' ORDER BY date
    """).df()
    nifty["date"] = pd.to_datetime(nifty["date"]).dt.date
    nifty["sma200"] = nifty["close"].rolling(200, min_periods=200).mean()
    sma = dict(zip(nifty["date"], nifty["sma200"]))
    above200 = reg["date"].map(lambda d: sma.get(d) is not None and pd.notna(sma.get(d))
                               and reg.set_index("date")["nifty50_close"].get(d, np.nan) > sma[d]
                               if d in reg.set_index("date").index else False)
    reg["nifty_above_sma200"] = above200.to_numpy() if hasattr(above200, "to_numpy") else above200

    both = reg["nifty50_above_ema"] & reg["breadth_above_50"]
    tri = np.where(both, "BULL_TREND",
          np.where(reg["nifty_above_sma200"], "PULLBACK_CORRECTION",
          np.where(reg["breadth_pct"] < 30.0, "SECULAR_BEAR", "UNDEFINED_gap")))
    reg["_tri"] = tri
    emit("\n  CR Issue-1 tri-state (same inputs):")
    for s, c in reg["_tri"].value_counts().items():
        emit(f"    {s:<20s}: {c:4d} ({c/n:.1%})")
    reg.to_pickle(Path(__file__).with_name("_regime_df.pkl"))
    return reg


# =====================================================================
# Feature frame (engine-exact): one build, reused by P1-P4, P7
# =====================================================================
def build_features() -> pd.DataFrame:
    from nse_cash.setups.features import _add_symbol_features
    from nse_cash.core.constants import FEATURE_COLS

    syms = [r[0] for r in con.execute(f"""
        SELECT DISTINCT symbol FROM pit_universe WHERE date >= '{D0}'
    """).fetchall()]
    bars = con.execute(f"""
        SELECT b.symbol, b.date, b.open, b.high, b.low, b.close, b.volume,
               b.deliverable_qty,
               coalesce(b.open_adj, b.open) AS open_adj,
               coalesce(b.high_adj, b.high) AS high_adj,
               coalesce(b.low_adj, b.low) AS low_adj,
               coalesce(b.close_adj, b.close) AS close_adj,
               coalesce(b.volume_adj, b.volume) AS volume_adj,
               coalesce(b.delivery_adj, b.deliverable_qty) AS delivery_adj
        FROM daily_bars b
        WHERE b.date >= '{WARM}' AND b.symbol IN ({','.join("?" * min(len(syms), 1)) + ')' if False else "SELECT DISTINCT symbol FROM pit_universe WHERE date >= '" + D0 + "'"})
        ORDER BY b.symbol, b.date
    """).df()
    bars["date"] = pd.to_datetime(bars["date"]).dt.date

    idx = con.execute("""
        SELECT index_name, date, close FROM market_indices
        WHERE index_name IN ('NIFTY 50','NIFTY 500') ORDER BY date
    """).df()
    idx["date"] = pd.to_datetime(idx["date"]).dt.date
    n50 = idx[idx.index_name == "NIFTY 50"][["date", "close"]].drop_duplicates("date")
    n500 = idx[idx.index_name == "NIFTY 500"][["date", "close"]].drop_duplicates("date")
    if n500.empty:
        n500 = n50.rename(columns={"close": "nifty500_close"})
    n50["nifty50_ret"] = n50["close"].pct_change()
    bars = bars.merge(n50[["date", "nifty50_ret"]], on="date", how="left")
    bars = bars.merge(n500.rename(columns={"close": "nifty500_close"})[["date", "nifty500_close"]],
                      on="date", how="left")

    pieces = []
    for _, g in bars.groupby("symbol", sort=False):
        if len(g) >= 210:
            g = g.sort_values("date").reset_index(drop=True)
            pieces.append(_add_symbol_features(g))
    f = pd.concat(pieces, ignore_index=True)
    # engine-exact cross-sectional percentiles (compute_features lines)
    f["rs_percentile"] = f.groupby("date")["rs_mansfield"].rank(pct=True)
    f["imom_percentile"] = f.groupby("date")["imom"].rank(pct=True)

    # forward windows for first-passage sims (shift(-k) within symbol)
    f = f.sort_values(["symbol", "date"]).reset_index(drop=True)
    gk = f.groupby("symbol")
    for k in range(1, H + 1):
        f[f"h{k}"] = gk["high_adj"].shift(-k)
        f[f"l{k}"] = gk["low_adj"].shift(-k)
    f["c5"] = gk["close_adj"].shift(-H)
    f["mfe5"] = f[[f"h{k}" for k in range(1, H + 1)]].max(axis=1) / f["close_adj"] - 1.0
    f["mae5"] = f[[f"l{k}" for k in range(1, H + 1)]].min(axis=1) / f["close_adj"] - 1.0

    keep = (["symbol", "date", "close_adj", "open_adj", "high_adj", "low_adj",
             "volume_adj", "delivery_adj", *FEATURE_COLS,
             "c5", "mfe5", "mae5", *[f"h{k}" for k in range(1, H + 1)],
             *[f"l{k}" for k in range(1, H + 1)]])
    f = f[[c for c in keep if c in f.columns]]
    f = f[f["date"] >= pd.Timestamp(D0).date()]
    return f


# =====================================================================
# P1. Setup 2 fires by regime (Issue 1's starvation claim)
# =====================================================================
def p1_setup2_by_regime(f: pd.DataFrame, reg: pd.DataFrame) -> None:
    emit("=" * 78)
    emit("P1. Setup 2 (Rubber-Band) full-predicate fires by regime — Issue 1")
    tri_map = dict(zip(reg["date"], reg["_tri"]))
    st_map = dict(zip(reg["date"], reg["state"]))
    s2 = f[(f["rsi2"] <= 10.0)
           & (f["delivery_adj"] <= 1.15 * f["sma20_delivery"])
           & (f["close_adj"] > f["sma200"])
           & (f["sma200_slope5"] > 0)].copy()
    s2["_tri"] = s2["date"].map(tri_map).fillna("n/a")
    s2["_st"] = s2["date"].map(st_map).fillna("n/a")
    total = len(s2)
    emit(f"  Setup 2 full-predicate fires {D0}+: {total} on {s2['date'].nunique()} sessions")
    for s, c in s2["_tri"].value_counts().items():
        emit(f"    {s:<20s}: {c:5d} ({c/max(1,total):.1%})")
    off = int((s2["_st"] == "OFFENSIVE_LONG").sum())
    emit(f"  fires under CURRENT binary engine: {off} evaluated / {total} suppressed")
    pull = int((s2["_tri"] == "PULLBACK_CORRECTION").sum())
    emit(f"  tri-state would additionally evaluate {pull} PULLBACK_CORRECTION fires")

    # The Issue-1 BET: same predicate, does it work better in pullback regimes?
    emit("\n  Forward edge of Setup 2 fires BY REGIME (entry=close_T, T1=+2%, stop=low*0.998):")
    for name, sub in (("BULL_TREND", s2[s2["_tri"] == "BULL_TREND"]),
                      ("PULLBACK_CORRECTION", s2[s2["_tri"] == "PULLBACK_CORRECTION"])):
        sub = sub.dropna(subset=["c5"])
        stop_frac = (sub["close_adj"] - sub["low_adj"] * 0.998).clip(lower=0) / sub["close_adj"]
        report_cohort(sub, name, stop_frac.to_numpy())
    emit("\n  Same predicate, stop at fixed -2.2% (study framing):")
    for name, sub in (("BULL_TREND", s2[s2["_tri"] == "BULL_TREND"]),
                      ("PULLBACK_CORRECTION", s2[s2["_tri"] == "PULLBACK_CORRECTION"])):
        report_cohort(sub.dropna(subset=["c5"]), name, None)


# =====================================================================
# P2. S_runner attainability + co-fire slot dynamics (Issue 2)
# =====================================================================
def p2_s_runner(f: pd.DataFrame) -> None:
    emit("=" * 78)
    emit("P2. S_runner on non-momentum archetypes — Issue 2's starvation claim")
    from nse_cash.setups.ranking import compute_s_runner

    def score(sub: pd.DataFrame, label: str) -> None:
        s = s_runner_vec(sub["delivery_z"], sub["imom_percentile"], sub["pv_percentile"])
        emit(f"  {label}: n={len(s)} | median={s.median():.3f} | p90={s.quantile(.9):.3f} "
             f"| max={s.max():.3f} | share>=0.45: {(s >= 0.45).mean():.1%}")

    s2 = f[(f["rsi2"] <= 10.0)
           & (f["delivery_adj"] <= 1.15 * f["sma20_delivery"])
           & (f["close_adj"] > f["sma200"]) & (f["sma200_slope5"] > 0)]
    score(s2, "Setup 2 fires scored with S_runner")

    s4w = f[f["breakout_age"].between(3, 7) & f["breakout_anchor_90"].notna()]
    score(s4w, "Setup 4 retest windows scored with S_runner")

    s5 = f[(f["imom_percentile"] >= 0.95)
           & (f["delivery_adj"] >= 2.0 * f["sma20_delivery"])
           & (f["close_adj"] > f["open_adj"])]
    score(s5, "Setup 5 fires scored with S_runner")

    # co-fire dynamics: same symbol-day firing both Setup 2 and Setup 5
    k2 = set(zip(s2["symbol"], s2["date"]))
    k5 = set(zip(s5["symbol"], s5["date"]))
    co = k2 & k5
    emit(f"\n  Setup2 x Setup5 co-fires (same symbol-day): {len(co)} "
         f"(of {len(k2)} S2 fires, {len(k5)} S5 fires)")
    if co:
        z2 = f.set_index(["symbol", "date"])
        both = z2.loc[sorted(co)]
        s_s2 = s_runner_vec(both["delivery_z"], both["imom_percentile"], both["pv_percentile"])
        # co-fires are dry-volume days (S2 requires dlv<=1.15*sma20), so z<=0
        # and the Z-term is 0: S_runner for the SAME row equals S2's score. The
        # slot fight is therefore decided by which setup the dedupe keeps.
        emit(f"    co-fire rows' S_runner: median={s_s2.median():.3f} "
             f"share>=0.45: {(s_s2 >= 0.45).mean():.1%} (dry days: Z-term=0)")

    # P2b: is the EXISTING S_runner threshold a good selector within the
    # starved archetypes? Phase D's new scores must BEAT this, not just exist.
    emit("\n  P2b. Does S_runner>=0.45 select edge WITHIN Setup 2 / Setup 4 fires?")
    emit("  (entry=close_T, own structural stop, T1=+2%; E[net] after costs)")
    for name, coh in (("Setup 2", s2), ("Setup 4 windows", s4w)):
        coh = coh.dropna(subset=["c5"])
        if name == "Setup 2":
            stop_px = coh["low_adj"] * 0.998
        else:
            stop_px = np.minimum(coh["breakout_anchor_90"], coh["low_adj"]) * 0.998
        sf_frac = ((coh["close_adj"] - stop_px).clip(lower=0) / coh["close_adj"]).to_numpy()
        s_sc = s_runner_vec(coh["delivery_z"], coh["imom_percentile"], coh["pv_percentile"])
        for label, mask in (("S>=0.45", (s_sc >= 0.45).to_numpy()),
                            ("S< 0.45", (s_sc < 0.45).to_numpy())):
            sub = coh[mask]
            report_cohort(sub, f"{name} {label}", sf_frac[mask])


# =====================================================================
# P3. Setup 5 stop gate (Issue 3)
# =====================================================================
def p3_setup5(f: pd.DataFrame) -> None:
    emit("=" * 78)
    emit("P3. Setup 5: prev_low stop gate vs proposed max(low_T, close*0.979) — Issue 3")
    s5 = f[(f["imom_percentile"] >= 0.95)
           & (f["delivery_adj"] >= 2.0 * f["sma20_delivery"])
           & (f["close_adj"] > f["open_adj"])].copy().dropna(subset=["c5", "prev_low"])
    n = len(s5)
    emit(f"  Setup 5 full-predicate fires {D0}+: {n}")
    if n == 0:
        emit("  no fires — cannot verify; STOP (probe shows the premise unmeasured)")
        return
    entry = s5["close_adj"]
    risk_prev = (entry - s5["prev_low"]) / entry
    range_t = (entry - s5["low_adj"]) / entry
    risk_prop = np.minimum(range_t, 0.021)  # max(low, close*0.979) => risk = min(range, 2.1%)

    emit(f"  current gate (stop=prev_low): pass 2.2% {int((risk_prev <= 0.022).sum())}/{n} "
         f"({(risk_prev <= 0.022).mean():.1%}) | median risk {risk_prev.median():.2%} | "
         f"p90 {risk_prev.quantile(.9):.2%}")
    emit(f"  proposed stop risk distribution: median {risk_prop.median():.2%}, "
         f"max {risk_prop.max():.4%}  -> note: max is 2.10% BY CONSTRUCTION")
    emit("  >>> the CR's own formula makes the 2.2% stop gate NON-BINDING for Setup 5:")
    emit("      risk = min(range_T, 2.1%) can never exceed the gate. 'Relief' is total,")
    emit("      not 'invalidation relief for prime bars'.")

    wide = range_t > 0.021
    emit(f"\n  fires with Day-T range > 2.1% (the 'prime expansion bars'): {int(wide.sum())} "
         f"({wide.mean():.1%})")
    # CR's premise: wide bars are the HIGH-conviction winners being rejected.
    emit("  Is Day-T width actually associated with forward edge? (entry=close_T, stop -2.2%)")
    for label, sub in (("narrow (range<=2.1%)", s5[~wide]),
                       ("wide (range>2.1%)", s5[wide])):
        report_cohort(sub, label, None)
    emit("\n  Simulated cohorts with their own stops (5-day FP, tie=stop):")
    report_cohort(s5[risk_prev <= 0.022], "admitted TODAY (stop=prev_low)",
                  risk_prev[risk_prev <= 0.022].to_numpy())
    report_cohort(s5, "admitted under CR-B.1 (all fires)",
                  np.minimum(range_t, 0.021).to_numpy())


# =====================================================================
# P4. Setup 3 & Setup 4 relaxations (Issue 5)
# =====================================================================
def p4_setup3_setup4(f: pd.DataFrame) -> None:
    emit("=" * 78)
    emit("P4. Setup 3 fire-rate claim & Setup 4 undershoot — Issue 5")
    base = f.dropna(subset=["c5", "rs_percentile", "high_52w"])
    rs = base["rs_percentile"] >= 0.95
    rng = (base["high_adj"] - base["low_adj"]) / base["low_adj"]
    prox = base["close_adj"] / base["high_52w"]
    n_rows = len(base)
    cur3 = rs & (rng <= 0.03) & (prox >= 0.985)
    rel3 = rs & (rng <= 0.042) & (prox >= 0.975)
    dry3 = rel3 & (base["volume_adj"] <= 0.85 * base["sma20_vol"])
    emit(f"  feature rows {D0}+: {n_rows}")
    emit(f"  Setup 3 fires CURRENT (range<=3%, prox>=98.5%): {int(cur3.sum())} "
         f"({cur3.mean():.4%} of rows)")
    emit(f"  Setup 3 fires RELAXED (range<=4.2%, prox>=97.5%): {int(rel3.sum())} "
         f"({rel3.mean():.4%}) -> x{rel3.sum()/max(1,cur3.sum()):.1f}")
    emit(f"  ... with CR's added dry-volume check (vol<=0.85*sma20): {int(dry3.sum())} "
         f"({dry3.mean():.4%}) -> x{dry3.sum()/max(1,cur3.sum()):.1f} net")
    emit("  Forward edge of each admitted cohort (entry=close_T, stop=low_T, T1=+2%):")
    for label, mask in (("current", cur3), ("relaxed", rel3), ("relaxed+dry", dry3)):
        sub = base[mask]
        if sub.empty:
            continue
        stop_frac = (sub["close_adj"] - sub["low_adj"]).clip(lower=0) / sub["close_adj"]
        report_cohort(sub, label, stop_frac.to_numpy())

    emit("\n  --- Setup 4: retest windows (age 3-7) ---")
    a = f[f["breakout_age"].between(3, 7) & f["breakout_anchor_90"].notna()
          & f["c5"].notna()].copy()
    anchor = a["breakout_anchor_90"]
    pen = (a["low_adj"] - anchor) / anchor
    green = a["close_adj"] > a["open_adj"]
    shadow_ok = ((np.minimum(a["open_adj"], a["close_adj"]) - a["low_adj"])
                 / (a["high_adj"] - a["low_adj"]).replace(0, np.nan)) >= 0.40
    dry = a["volume_adj"] <= 0.55 * a["sma20_vol"]
    cur4 = (pen.abs() <= 0.008) & (a["close_adj"] >= anchor) & green & shadow_ok.fillna(False) & dry \
        & (((a["close_adj"] - np.minimum(anchor, a["low_adj"]) * 0.998) / a["close_adj"]) <= 0.020)
    prop4 = (pen >= -0.012) & (a["close_adj"] >= 0.998 * anchor) & green & shadow_ok.fillna(False) & dry \
        & (((a["close_adj"] - np.minimum(anchor, a["low_adj"]) * 0.998) / a["close_adj"]) <= 0.022)
    emit(f"  retest windows: {len(a)} | current full predicate: {int(cur4.sum())} fires | "
         f"proposed: {int(prop4.sum())} fires (x{prop4.sum()/max(1,cur4.sum()):.1f})")
    emit(f"  low undercuts anchor in {(pen < 0).mean():.1%} of windows; "
         f"median undercut {(pen[pen < 0]).median() if (pen < 0).any() else 0:.3%}; "
         f"undercut beyond -1.2%: {(pen < -0.012).mean():.1%}")
    emit("  Forward edge (stop = min(anchor, low)*0.998, T1=+2%):")
    for label, mask in (("current gate", cur4), ("proposed gate", prop4)):
        sub = a[mask]
        if sub.empty:
            emit(f"    {label}: n=0")
            continue
        stop_px = np.minimum(sub["breakout_anchor_90"], sub["low_adj"]) * 0.998
        sf = (sub["close_adj"] - stop_px).clip(lower=0) / sub["close_adj"]
        report_cohort(sub, label, sf.to_numpy())


# =====================================================================
# P5. Fixed +2% vs ATR targets (Issue 4) — repo's own Addendum B
# =====================================================================
def p5_atr() -> None:
    emit("=" * 78)
    emit("P5. Issue 4 (ATR-scaled targets) vs the repo's own Addendum B study")
    atr = pd.read_csv(ROOT / "data" / "results" / "atr_targets.csv")
    fx = pd.read_csv(ROOT / "data" / "results" / "targets.csv")
    r2 = fx[fx["target_pct"] == 2.0].iloc[0]
    emit(f"  fixed +2.0% target, unconditional: P(win)={r2['p_fp_win']:.1%}, "
         f"exp_net={r2['exp_net_pct']}% per attempt")
    for k in (0.5, 0.75, 1.0):
        r = atr[atr["k_atr"] == k].iloc[0]
        emit(f"  K={k:.2f} ATR target (median {r['median_target_pct']}%): "
             f"P(win)={r['p_fp_win']:.1%}, exp_net={r['exp_net_pct']}% per attempt")
    emit("  CR proposes T1 = clamp(0.75*ATR, 2.0%, 3.5%). For the median-ATR stock")
    emit("  (3.18%) that is a 2.4-3.2% target: K=0.75-1.0 rows. Win rate FALLS from")
    emit("  50.4% to 47.5%/39.3% and exp_net stays negative. Addendum B's verdict in")
    emit("  the repo's own docs: 'the stability hypothesis is rejected on all three")
    emit("  tests... ATR normalization is a screening tool, not a targeting tool.'")
    emit("  CR-002 Issue 4 cites the sweet-spot study but not its Addendum B.")


# =====================================================================
# P6. CR §2.2 arithmetic audit
# =====================================================================
def p6_arithmetic() -> None:
    emit("=" * 78)
    emit("P6. CR §2.2 expectancy arithmetic")
    e1 = 0.52 * 2.10 - 0.48 * 1.85 - 0.35
    e2 = 0.53 * 3.15 - 0.47 * 1.65 - 0.35
    emit(f"  baseline inputs  -> E = {e1:+.3f}%/trade  (CR states '-0.146% to +0.10%')")
    emit(f"  proposed inputs  -> E = {e2:+.3f}%/trade  (CR states '+0.544%')")
    emit("  Arithmetic: checks out. But:")
    emit("  a) §2.1 claims baseline expectancy '+0.18%'; §2.2 computes -0.146%..+0.10%.")
    emit("     The CR contradicts ITSELF on its own baseline.")
    emit("  b) The avg-win input (+2.10%) is asserted, not measured. No walk-forward of")
    emit("     the CURRENT engine produced it (no tear sheet exists in data/results).")
    emit("  c) The +3.15% avg win assumes the dynamic-target change works — i.e. the")
    emit("     conclusion is an input to the proof.")
    emit("  d) BRD §10.1 already targets +0.40-0.75% expectancy; the CR baseline (+0.1%)")
    emit("     implies the current system MISSES the BRD — never demonstrated.")


# =====================================================================
# P7. THE decisive table: per-setup conditional edge vs unconditional
# =====================================================================
def p7_conditional_edge(f: pd.DataFrame) -> None:
    emit("=" * 78)
    emit("P7. Per-setup conditional first-passage edge (the number the CR omits)")
    emit("    Baseline from the study: unconditional 2%-before--2.2% = 50.4%,")
    emit("    breakeven 75.9% -> every setup must LIFT P(win), not just fire.")
    base = f.dropna(subset=["c5"])
    win_base, sf_base, pnl_base = fp_outcome(base, stop_frac=None)
    emit(f"  probe-window unconditional P(2% before -2.2%, 5d, close-entry): "
         f"{win_base.mean():.1%} (n={len(base)}) [study pooled 2021-26: 50.4%]")
    emit("\n  Cohorts (entry=close_T, each setup's own structural stop, T1=+2%):")

    s1 = base[(base["close_adj"] > base["sma200"]) & (base["sma200_slope5"] > 0)
              & (((base["shock_a_5d"] or 0) >= 1) if False else (base["shock_a_5d"].fillna(0) >= 1)
                 | (base["z15_count_3d"].fillna(0) >= 2))
              & (base["volume_adj"] <= 0.65 * base["sma20_vol"])
              & (((base["high_adj"] - base["low_adj"]) / base["close_adj"] <= 0.015)
                 | (base["pv_percentile"] <= 0.15))]
    if not s1.empty:
        sf1 = ((s1["close_adj"] - np.minimum(s1["low_adj"], s1["prev_low"]).clip(lower=0))
               .clip(lower=0) / s1["close_adj"])
        report_cohort(s1, "Setup 1 VCP squeeze (stop=min(low,prev_low))", sf1.to_numpy())

    s2 = base[(base["rsi2"] <= 10.0) & (base["delivery_adj"] <= 1.15 * base["sma20_delivery"])
              & (base["close_adj"] > base["sma200"]) & (base["sma200_slope5"] > 0)]
    if not s2.empty:
        sf2 = (s2["close_adj"] - s2["low_adj"] * 0.998).clip(lower=0) / s2["close_adj"]
        report_cohort(s2, "Setup 2 rubber-band (stop=low*0.998)", sf2.to_numpy())

    s3 = base[(base["rs_percentile"] >= 0.95)
              & ((base["high_adj"] - base["low_adj"]) / base["low_adj"] <= 0.03)
              & (base["close_adj"] >= 0.985 * base["high_52w"])]
    if not s3.empty:
        sf3 = (s3["close_adj"] - s3["low_adj"]).clip(lower=0) / s3["close_adj"]
        report_cohort(s3, "Setup 3 RS base (stop=low_T)", sf3.to_numpy())

    a = base[base["breakout_age"].between(3, 7) & base["breakout_anchor_90"].notna()
             & (base["volume_adj"] <= 0.55 * base["sma20_vol"])
             & (base["close_adj"] > base["open_adj"])
             & (((np.minimum(base["open_adj"], base["close_adj"]) - base["low_adj"])
                 / (base["high_adj"] - base["low_adj"]).replace(0, np.nan)) >= 0.40).fillna(False)]
    a = a[((a["low_adj"] - a["breakout_anchor_90"]).abs() / a["breakout_anchor_90"] <= 0.008)
          & (a["close_adj"] >= a["breakout_anchor_90"])]
    if not a.empty:
        sfa = ((a["close_adj"] - np.minimum(a["breakout_anchor_90"], a["low_adj"]) * 0.998)
               .clip(lower=0) / a["close_adj"])
        report_cohort(a, "Setup 4 anchor retest (current gates)", sfa.to_numpy())

    s5 = base[(base["imom_percentile"] >= 0.95)
              & (base["delivery_adj"] >= 2.0 * base["sma20_delivery"])
              & (base["close_adj"] > base["open_adj"]) & base["prev_low"].notna()]
    if not s5.empty:
        sf5 = (s5["close_adj"] - s5["prev_low"]).clip(lower=0) / s5["close_adj"]
        report_cohort(s5, "Setup 5 residual mom (stop=prev_low)", sf5.to_numpy())
        ok = s5[sf5 <= 0.022]
        report_cohort(ok, "  ... after 2.2% stop gate (what trades today)",
                      sf5[sf5 <= 0.022].to_numpy())

    emit("\n  Reading: a setup deserves code changes only if its cohort's P(win)")
    emit("  beats the unconditional baseline by enough to clear breakeven vs its")
    emit("  own stop, and E[net] > 0 after friction+tax. Breakeven p for (2% win,")
    emit(f"  s stop, 0.35% friction, 20% STCG) = (s+0.35)/((2.35)) / 0.8 — see CR §2.")


def main() -> None:
    emit(f"CR-2026-002 empirical probe — store: {DB.name} | window {D0}+ (warm-up {WARM})")
    emit(f"entry model: close_T, 5-session first-passage, tie->stop, friction 0.35%, STCG 20%\n")
    reg = p0_regime()
    f = build_features()
    emit(f"\nfeature frame: {len(f):,} rows, {f['symbol'].nunique()} symbols, "
         f"{f['date'].min()}..{f['date'].max()}")
    p1_setup2_by_regime(f, reg)
    p2_s_runner(f)
    p3_setup5(f)
    p4_setup3_setup4(f)
    p5_atr()
    p6_arithmetic()
    p7_conditional_edge(f)
    emit("\nDONE")
    OUT.write_text(_buf.getvalue(), encoding="utf-8")
    for p in (Path(__file__).with_name("_regime_df.pkl"),):
        p.unlink(missing_ok=True)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
