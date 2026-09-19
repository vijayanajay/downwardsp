"""CR-2026-003 lever-by-lever truth probe (read-only).

Reuses the CR-002 probe machinery (experiments/cr002/empirical_review_cr002.py:
build_features, fp_outcome, net_after_costs, s_runner_vec) so every number is
methodology-identical to the artifacts the CR cites. Adds:

  L2  Realized P&L x breadth (retrospective counterfactual for Lever 2)
  L3  Setup 5 risk parity: engine-mirror sim (stall/breakeven/time exits),
      per-notional AND per-unit-risk (risk-parity) expectancy
  L4  Setup 4 expansion: current gate vs CR-003 geometry, T1=+2% vs +3.5%
  L5  S_runner gate: within-setup S>=0.45 vs S<0.45 edge; in-book margins

Engine-mirror sim: entry=close_T (probe convention; validated vs engine:
narrow-cohort P(win) 33% mirror / 28.9% engine realized), day-resolution,
pessimism (stop wins ties), T1 fills -> breakeven stop arms EOD (T2 protected
from next session), Day-2 stall exit (close < entry*1.008 -> exit all at
close), Day-5 time exit, T2 target +6%. Gap-through-stop and kill switch are
out of scope (rare; noted as approximation).

Run:  .venv/Scripts/python.exe experiments/cr003/probe_levers_truth.py
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "experiments" / "cr002"))

import empirical_review_cr002 as cr2  # noqa: E402  (module-level duckdb con is read-only)

OUT = Path(__file__).with_name("probe_output_levers.txt")
_buf = io.StringIO()


def emit(line: str = "") -> None:
    print(line)
    _buf.write(line + "\n")


FRICTION, STCG = 0.0035, 0.20
SLOT = 125_000.0
RISK_BUDGET = 0.022


def net(pnl: np.ndarray) -> np.ndarray:
    return cr2.net_after_costs(pnl)


def engine_mirror(entry: np.ndarray, stop_frac: np.ndarray,
                  h: np.ndarray, l: np.ndarray, c2: np.ndarray, c5: np.ndarray,
                  t1: float = 0.02, t2: float = 0.06,
                  stall_thr: float = 0.008, horizon: int = 5):
    """Day-resolution mirror of fill_model.simulate_*_day in adjusted space.

    entry: close_T (probe convention). stop_frac: positive fraction below entry.
    Returns pnl (per notional, gross), win, reasons.
    """
    n = len(entry)
    pnl = np.zeros(n)
    win = np.zeros(n, bool)
    reasons = np.empty(n, dtype=object)
    for i in range(n):
        e = entry[i]
        stop = stop_frac[i]
        t1f = False
        done = False
        leg1 = leg2 = 0.0
        reason = "TIME"
        for d in range(1, horizon + 1):
            hi = h[i, d - 1] / e - 1.0
            lo = l[i, d - 1] / e - 1.0
            cur_stop = 0.0 if t1f else stop        # breakeven arms EOD of T1-fill day
            if lo <= -cur_stop - 1e-12:            # pessimism: stop wins ties
                if not t1f:
                    leg1 = leg2 = -stop
                else:
                    leg2 = 0.0                      # T2 exits at breakeven
                reason = "STOP" if not t1f else "BE_STOP"
                done = True
                break
            if not t1f and hi >= t1:
                t1f = True
                leg1 = t1
                if hi >= t2:                        # same-day T2 (engine semantics)
                    leg2 = t2
                    reason = "T2"
                    done = True
                    break
            elif t1f and hi >= t2:
                leg2 = t2
                reason = "T2"
                done = True
                break
            if d == 2 and (c2[i] / e - 1.0) < stall_thr - 1e-12:
                r2d = c2[i] / e - 1.0
                if not t1f:
                    leg1 = leg2 = r2d
                else:
                    leg2 = r2d
                reason = "STALL"
                done = True
                break
        if not done:
            r5 = c5[i] / e - 1.0
            if not t1f:
                leg1 = leg2 = r5
            else:
                leg2 = r5
        pnl[i] = 0.5 * leg1 + 0.5 * leg2
        win[i] = pnl[i] > 0
        reasons[i] = reason
    return pnl, win, reasons


def reason_hist(reasons) -> str:
    u, c = np.unique(reasons, return_counts=True)
    return ", ".join(f"{k}:{v}" for k, v in zip(u, c))


def report(name: str, pnl: np.ndarray, win: np.ndarray, reasons,
           risk: np.ndarray, t1_target: float = 0.02,
           risk_parity: bool = True) -> None:
    n_ = net(pnl)
    be = (risk + FRICTION) / ((t1_target - FRICTION) + (risk + FRICTION)) / (1 - STCG)
    line = (f"    {name:<44s} n={len(pnl):5d} | P(win)={win.mean():5.1%} | "
            f"E[net]={n_.mean()*100:+8.4f}%/trade | med risk={np.median(risk):6.2%} "
            f"| BE p~{np.median(be):4.0%} | exits: {reason_hist(reasons)}")
    if risk_parity:
        fac = np.minimum(1.0, RISK_BUDGET / np.maximum(risk, 1e-9))
        rp = n_ * fac
        line += (f"\n        rupee view (Rs {SLOT:,.0f} slot): E[net]={n_.mean()*SLOT:+8,.0f} | "
                 f"risk-parity E={rp.mean()*SLOT:+8,.0f} "
                 f"(deploy median {np.median(fac):.0%}; rupee stop-out {np.median(risk)*np.median(fac)*SLOT:,.0f})")
    else:
        line += f"\n        rupee view (Rs {SLOT:,.0f} slot, full deployment): E[net]={n_.mean()*SLOT:+8,.0f}"
    emit(line)


def main() -> None:
    emit(f"CR-2026-003 lever truth probe | friction {FRICTION:.2%}, STCG {STCG:.0%}, "
         f"slot Rs {SLOT:,.0f}, risk budget {RISK_BUDGET:.2%}")

    # ------------------------------------------------------------------ L2
    emit("=" * 100)
    emit("L2. Lever 2 retrospective: realized 216-trade book x signal-day breadth")
    from nse_cash.data.storage import MarketStore
    from nse_cash.funnel.market_regime import evaluate_market_regime_range
    store = MarketStore(cr2.DB, read_only=True)
    try:
        reg = evaluate_market_regime_range(store,
                                           start_date=pd.Timestamp("2023-01-01").date(),
                                           end_date=pd.Timestamp("2026-09-17").date())
    finally:
        store.close()
    reg["date"] = pd.to_datetime(reg["date"]).dt.date
    reg = reg[["date", "breadth_pct"]]

    tr = pd.read_parquet(ROOT / "reports" / "backtest" / "backtest_trades.parquet")
    tr = tr[tr["entry_price"].notna()].copy()
    sig = tr["trade_id"].str.rsplit("-", n=3, expand=True)
    tr["signal_date"] = pd.to_datetime(sig[1] + "-" + sig[2] + "-" + sig[3]).dt.date
    tr["year"] = pd.to_datetime(tr["entry_date"]).dt.year
    j = tr.merge(reg, left_on="signal_date", right_on="date", how="left")
    emit(f"  filled book trades: {len(j)}  | total realized trading P&L: Rs {j['realized_pnl'].sum():,.0f}")

    for thr in (75.0, 77.333):
        hi = j[j["breadth_pct"] >= thr]
        lo = j[j["breadth_pct"] < thr]
        emit(f"  breadth >= {thr}: n={len(hi):3d} P&L=Rs {hi['realized_pnl'].sum():+9,.0f} "
             f"(win {hi['realized_pnl'].gt(0).mean():.1%}, avg Rs {hi['realized_pnl'].mean():+7,.0f}) | "
             f"below: n={len(lo):3d} P&L=Rs {lo['realized_pnl'].sum():+9,.0f}")

    ex2 = j[j["setup"] != "SETUP_2_RUBBERBAND"]
    emit(f"  --- ex-Setup-2 book (Lever-1 world, n={len(ex2)}, P&L Rs {ex2['realized_pnl'].sum():,.0f}) ---")
    for thr in (75.0, 77.333):
        hi = ex2[ex2["breadth_pct"] >= thr]
        lo = ex2[ex2["breadth_pct"] < thr]
        emit(f"  breadth >= {thr}: n={len(hi):3d} P&L=Rs {hi['realized_pnl'].sum():+9,.0f} "
             f"(win {hi['realized_pnl'].gt(0).mean():.1%}, avg Rs {hi['realized_pnl'].mean():+7,.0f}) | "
             f"below: n={len(lo):3d} P&L=Rs {lo['realized_pnl'].sum():+9,.0f}")

    hi77 = j[j["breadth_pct"] >= 77.333]
    emit("  >=77.3% cohort by year (full book):")
    for y in sorted(j["year"].unique()):
        g = hi77[hi77["year"] == y]
        emit(f"    {y}: n={len(g):3d} P&L=Rs {g['realized_pnl'].sum():+8,.0f} "
             f"(win {g['realized_pnl'].gt(0).mean() if len(g) else float('nan'):.1%})")

    s5_book = j[j["setup"] == "SETUP_5_RESIDUAL_MOM"]
    emit(f"  realized Setup-5 trades (n={len(s5_book)}): P&L Rs {s5_book['realized_pnl'].sum():,.0f} | "
         f"exit mix: {s5_book['exit_reason'].value_counts().to_dict()}")
    emit(f"  Setup-5 trades by breadth (>=77.3): n={int((s5_book['breadth_pct'] >= 77.333).sum())} "
         f"P&L Rs {s5_book[s5_book['breadth_pct'] >= 77.333]['realized_pnl'].sum():,.0f}")

    # ------------------------------------------------------------------ features
    emit("=" * 100)
    emit("Building engine-exact feature frame (reuses CR-002 machinery)...")
    f = cr2.build_features()
    gk = f.groupby("symbol")
    for k in (2, 3, 4):
        f[f"c{k}"] = gk["close_adj"].shift(-k)
    f = f.dropna(subset=["c5"]).reset_index(drop=True)
    emit(f"  frame: {len(f):,} rows, {f['symbol'].nunique()} symbols, {f['date'].min()}..{f['date'].max()}")

    bmap = dict(zip(reg["date"], reg["breadth_pct"]))
    f["breadth"] = f["date"].map(bmap)

    hmat = np.stack([f[f"h{k}"].to_numpy(float) for k in range(1, 6)], axis=1)
    lmat = np.stack([f[f"l{k}"].to_numpy(float) for k in range(1, 6)], axis=1)
    c2v = f["c2"].to_numpy(float)
    c5v = f["c5"].to_numpy(float)
    ent = f["close_adj"].to_numpy(float)

    def sub_arrays(idx: np.ndarray):
        return ent[idx], hmat[idx], lmat[idx], c2v[idx], c5v[idx]

    # ------------------------------------------------------------------ L3
    emit("=" * 100)
    emit("L3. Lever 3: Setup 5 risk parity — does a wide stop + smaller size beat today's narrow gate?")
    s5 = f[(f["imom_percentile"] >= 0.95)
           & (f["delivery_adj"] >= 2.0 * f["sma20_delivery"])
           & (f["close_adj"] > f["open_adj"]) & f["prev_low"].notna()].copy()
    pos5 = s5.index.to_numpy()
    risk_prev = ((s5["close_adj"] - s5["prev_low"]).clip(lower=0) / s5["close_adj"]).to_numpy()
    ent5, h5, l5, c25, c55 = sub_arrays(pos5)
    emit(f"  fires: {len(s5)} | risk<=2.2% today: {int((risk_prev <= 0.022).sum())} "
         f"({(risk_prev <= 0.022).mean():.1%}) | median risk {np.median(risk_prev):.2%} | p90 {np.quantile(risk_prev, .9):.2%}")

    sub = np.where(risk_prev <= 0.022)[0]
    pnl, win, rs = engine_mirror(ent5[sub], risk_prev[sub], h5[sub], l5[sub], c25[sub], c55[sub])
    report("admitted TODAY (risk<=2.2%, full slot)", pnl, win, rs, risk_prev[sub], risk_parity=False)
    pnl, win, rs = engine_mirror(ent5, risk_prev, h5, l5, c25, c55)
    report("CR-003 wide-stop cohort (all fires, risk-parity sized)", pnl, win, rs, risk_prev)

    hi_b = (s5["breadth"].to_numpy() >= 77.333)
    for lbl, mask in (("breadth>=77.3", hi_b), ("breadth<77.3", ~hi_b)):
        m = np.where(mask)[0]
        if len(m) < 30:
            emit(f"    CR cohort x {lbl}: n={len(m)} (too few)")
            continue
        pnl, win, rs = engine_mirror(ent5[m], risk_prev[m], h5[m], l5[m], c25[m], c55[m])
        report(f"CR-003 cohort x {lbl}", pnl, win, rs, risk_prev[m])

    yr = pd.to_datetime(pd.Series(s5["date"].to_numpy())).dt.year.to_numpy()
    emit("  wide cohort by year:")
    for y in sorted(set(yr)):
        m = np.where(yr == y)[0]
        if len(m) < 30:
            emit(f"    {y}: n={len(m)} (too few)")
            continue
        pnl, win, rs = engine_mirror(ent5[m], risk_prev[m], h5[m], l5[m], c25[m], c55[m])
        n_ = net(pnl)
        fac = np.minimum(1.0, RISK_BUDGET / risk_prev[m])
        emit(f"    {y}: n={len(m):5d} P(win)={win.mean():5.1%} E[net]={n_.mean()*100:+7.3f}% "
             f"E[risk-parity]={ (n_*fac).mean()*SLOT:+8,.0f} Rs/slot")

    # ------------------------------------------------------------------ L4
    emit("=" * 100)
    emit("L4. Lever 4: Setup 4 expansion — current gate vs CR-003 geometry (undercut -2.5%, close>=99.5%, T1 3.5%)")
    a = f[f["breakout_age"].between(3, 7) & f["breakout_anchor_90"].notna()].copy()
    posa = a.index.to_numpy()
    enta, ha, la, c2a, c5a = sub_arrays(posa)
    anchor = a["breakout_anchor_90"].to_numpy(float)
    low_a = a["low_adj"].to_numpy(float)
    close_a = a["close_adj"].to_numpy(float)
    green = (a["close_adj"] > a["open_adj"]).to_numpy()
    rng = (a["high_adj"] - a["low_adj"]).to_numpy(float)
    shadow = np.where(rng > 0, (np.minimum(a["open_adj"], a["close_adj"]).to_numpy(float) - low_a) / np.where(rng > 0, rng, 1), 0.0)
    dry = (a["volume_adj"] <= 0.55 * a["sma20_vol"]).to_numpy()
    pen = low_a / anchor - 1.0                                   # negative = undercut
    cur = ((np.abs(pen) <= 0.008) & (close_a >= anchor) & green & (shadow >= 0.40) & dry)
    risk_cur = (close_a - np.minimum(anchor, low_a) * 0.998) / close_a
    cur_gated = cur & (risk_cur <= 0.020)                        # engine's own Setup-4 stop gate
    cr = ((pen >= -0.025) & (pen <= 0.008) & (close_a >= 0.995 * anchor)
          & green & (shadow >= 0.40) & dry)
    risk_cr = (close_a - np.minimum(anchor, low_a) * 0.998) / close_a
    cr_gated = cr & (risk_cr <= 0.035)                           # CR's raised max_stop_pct=0.035
    emit(f"  retest windows: {len(a)}")
    emit(f"  current predicate: {int(cur.sum())} fires -> with 2.0% stop gate: {int(cur_gated.sum())}")
    emit(f"  CR-003 predicate:  {int(cr.sum())} fires -> with 3.5% stop gate: {int(cr_gated.sum())} "
         f"(x{cr_gated.sum() / max(1, cur_gated.sum()):.1f})")
    if (pen < 0).any():
        emit(f"  undercut distribution where pen<0: median {np.median(pen[pen < 0]):.3%}; "
             f"pen < -0.8%: {(pen < -0.008).mean():.1%} of windows; pen < -2.5%: {(pen < -0.025).mean():.1%}")
    if cr_gated.any():
        emit(f"  CR-003 gated cohort risk: median {np.median(risk_cr[cr_gated]):.2%}, "
             f"p90 {np.quantile(risk_cr[cr_gated], .9):.2%} | "
             f"fires needing >2.2% legacy stop: {(risk_cr[cr_gated] > 0.022).mean():.1%} "
             f"(NOT risk-parity sized by the CR: rupee stop-out up to Rs {np.quantile(risk_cr[cr_gated], .9) * SLOT:,.0f})")

    for lbl, mask, t1t in (("current gate, T1=+2%", cur_gated, 0.02),
                           ("CR-003 gate, T1=+2%", cr_gated, 0.02),
                           ("CR-003 gate, T1=+3.5% (CR proposal)", cr_gated, 0.035)):
        sub = np.where(mask)[0]
        if len(sub) == 0:
            emit(f"    {lbl}: n=0")
            continue
        sf = risk_cr[sub]
        pnl, win, rs = engine_mirror(enta[sub], sf, ha[sub], la[sub], c2a[sub], c5a[sub],
                                     t1=t1t, t2=0.06)
        report(lbl, pnl, win, rs, sf, t1_target=t1t, risk_parity=False)

    hi_ba = (a["breadth"].to_numpy() >= 77.333)
    m4 = cr_gated & hi_ba
    emit(f"  CR-003 gate x breadth>=77.3: {int(m4.sum())} fires "
         f"(2023-2026 -> {m4.sum() / 3.7:.0f}/yr before slots/regime/dedupe)")

    # ------------------------------------------------------------------ L5
    emit("=" * 100)
    emit("L5. Lever 5: S_runner >= 0.45 gate — who does it keep, and does the kept cohort lose less?")
    cohorts = {
        "Setup 1": f[(f["close_adj"] > f["sma200"]) & (f["sma200_slope5"] > 0)
                     & ((f["shock_a_5d"].fillna(0) >= 1) | (f["z15_count_3d"].fillna(0) >= 2))
                     & (f["volume_adj"] <= 0.65 * f["sma20_vol"])
                     & (((f["high_adj"] - f["low_adj"]) / f["close_adj"] <= 0.015)
                        | (f["pv_percentile"] <= 0.15))],
        "Setup 3": f[(f["rs_percentile"] >= 0.95)
                     & ((f["high_adj"] - f["low_adj"]) / f["low_adj"] <= 0.03)
                     & (f["close_adj"] >= 0.985 * f["high_52w"])],
        "Setup 4 windows": f[f["breakout_age"].between(3, 7) & f["breakout_anchor_90"].notna()],
        "Setup 5": s5,
    }
    for name, coh in cohorts.items():
        coh = coh.dropna(subset=["c5"])
        posc = coh.index.to_numpy()
        entc, hc, lc, c2c, c5c = sub_arrays(posc)
        if name == "Setup 1":
            sf = ((coh["close_adj"] - np.minimum(coh["low_adj"], coh["prev_low"]).clip(lower=0))
                  .clip(lower=0) / coh["close_adj"]).to_numpy()
        elif name == "Setup 3":
            sf = ((coh["close_adj"] - coh["low_adj"]).clip(lower=0) / coh["close_adj"]).to_numpy()
        elif name == "Setup 4 windows":
            sf = ((coh["close_adj"] - np.minimum(coh["breakout_anchor_90"], coh["low_adj"]) * 0.998)
                  .clip(lower=0) / coh["close_adj"]).to_numpy()
        else:
            sf = ((coh["close_adj"] - coh["prev_low"]).clip(lower=0) / coh["close_adj"]).to_numpy()
        s_sc = cr2.s_runner_vec(coh["delivery_z"], coh["imom_percentile"], coh["pv_percentile"]).to_numpy()
        keep = np.where(sf <= 0.05)[0]      # exclude absurd stop distances for comparability
        if len(keep) < 40:
            emit(f"  {name}: n={len(keep)} (too few)")
            continue
        for lbl, m2 in (("S>=0.45", s_sc[keep] >= 0.45), ("S< 0.45", s_sc[keep] < 0.45)):
            idx = keep[m2]
            if len(idx) < 25:
                emit(f"    {name} {lbl}: n={len(idx)} (too few)")
                continue
            pnl, win, rs = engine_mirror(entc[idx], sf[idx], hc[idx], lc[idx], c2c[idx], c5c[idx])
            n_ = net(pnl)
            emit(f"    {name:<16s} {lbl}: n={len(idx):5d} P(win)={win.mean():5.1%} "
                 f"E[net]={n_.mean()*100:+7.3f}%/trade (own stop, T1=+2%)")

    # in-book S margins
    from nse_cash.setups.ranking import compute_s_runner
    fmap = f.set_index(["symbol", "date"])
    s_book = []
    for r in j.itertuples():
        key = (r.symbol, r.signal_date)
        if key in fmap.index:
            row = fmap.loc[key]
            if hasattr(row, "ndim") and row.ndim > 1:
                row = row.iloc[0]
            s_book.append(compute_s_runner(row.get("delivery_z"), row.get("imom_percentile"),
                                           row.get("pv_percentile")))
        else:
            s_book.append(np.nan)
    s_book = np.array(s_book, dtype=float)
    ok = ~np.isnan(s_book)
    emit(f"  in-book S_runner (n={int(ok.sum())} of {len(j)} matched): "
         f"min={np.nanmin(s_book):.3f} p10={np.nanquantile(s_book, .1):.3f} "
         f"median={np.nanmedian(s_book):.3f} | share in the 0.45-0.50 band: "
         f"{((s_book >= 0.45) & (s_book < 0.50)).mean():.1%}")

    emit("\nDONE")
    OUT.write_text(_buf.getvalue(), encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
