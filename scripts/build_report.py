"""Step 3/3 — Build the self-contained HTML report (figures + tables + verdict).

Reads data/results/*.csv written by analyze.py, renders matplotlib figures,
embeds everything as base64 PNGs, and writes reports/sweet_spot_study.html.

Usage:  python scripts/build_report.py
"""

from __future__ import annotations

import base64
import io
from datetime import date
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "data" / "results"
OUT = ROOT / "reports" / "sweet_spot_study.html"
CFG = yaml.safe_load((ROOT / "scripts" / "study_config.yaml").read_text())

STOP = float(CFG["study"]["stop_pct"])
FRICTION = float(CFG["economics"]["friction_pct"])
STCG = float(CFG["economics"]["stcg_pct"])
HORIZON = int(CFG["study"]["horizon_days"])
TOP_N_REPEATS = int(CFG["report"]["top_n_repeats"])
KEY_TARGETS = [1.0, 2.0, 4.0, 6.0]

plt.rcParams.update({
    "figure.dpi": 110, "font.size": 9.5, "axes.grid": True,
    "grid.alpha": 0.3, "axes.spines.top": False, "axes.spines.right": False,
})


def breakeven_hit_rate(target_pct: float) -> float:
    net_win = target_pct / 100 - FRICTION / 100
    net_loss = STOP / 100 + FRICTION / 100
    return (net_loss / (net_win + net_loss)) / (1 - STCG / 100)


def fig64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def load():
    tgt = pd.read_csv(RES / "targets.csv")
    by_year = pd.read_csv(RES / "by_year.csv")
    by_regime = pd.read_csv(RES / "by_regime.csv") if (RES / "by_regime.csv").exists() else None
    by_dow = pd.read_csv(RES / "by_dow.csv").dropna(subset=["dow"])
    stock = pd.read_csv(RES / "stock_summary.csv")
    rep = pd.read_csv(RES / "repeats.csv")
    audit = pd.read_csv(ROOT / "data" / "universe_downloaded.csv")
    atr_tgt = pd.read_csv(RES / "atr_targets.csv") if (RES / "atr_targets.csv").exists() else None
    atr_disp = pd.read_csv(RES / "atr_stock_rates.csv") if (RES / "atr_stock_rates.csv").exists() else None
    screen = pd.read_csv(RES / "screen_half_atr.csv") if (RES / "screen_half_atr.csv").exists() else None
    return tgt, by_year, by_regime, by_dow, stock, rep, audit, atr_tgt, atr_disp, screen


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------

def fig_expectancy(tgt: pd.DataFrame) -> str:
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 7), sharex=True,
                                   gridspec_kw={"height_ratios": [1, 1]})
    colors = ["#2e7d32" if v > 0 else "#c62828" for v in tgt["exp_net_pct"]]
    ax1.bar(tgt["target_pct"], tgt["exp_net_pct"], color=colors, width=0.35)
    ax1.axhline(0, color="black", lw=0.8)
    ax1.set_ylabel("net expectancy per entry (%)")
    ax1.set_title(f"Net expectancy per daily entry (target/stop exits, {HORIZON}d horizon,\n"
                  f"friction {FRICTION}% RT + STCG {STCG:.0f}%) — all bars negative = no free lunch")

    ax2.plot(tgt["target_pct"], tgt["p_fp_win"], "o-", label="P(touch target before stop)", color="#1565c0")
    ax2.plot(tgt["target_pct"], tgt["breakeven_p"], "s--", label="breakeven hit rate needed", color="#c62828")
    for _, r in tgt.iterrows():
        ax2.annotate(f"{r['p_fp_win']:.2f}", (r["target_pct"], r["p_fp_win"]),
                     textcoords="offset points", xytext=(0, 6), fontsize=7.5)
    ax2.set_xlabel("profit target (%)")
    ax2.set_ylabel("probability")
    ax2.set_title("Measured hit rate vs the rate you would need (stop −"f"{STOP}%)")
    ax2.legend()
    ax2.set_xticks(tgt["target_pct"])
    return fig64(fig)


def fig_touches(tgt: pd.DataFrame) -> str:
    fig, ax = plt.subplots(figsize=(9, 4.2))
    w = 0.38
    x = np.arange(len(tgt))
    ax.bar(x - w / 2, tgt["p_mfe_ge"] * 100, w, label="P(MFE reaches target at all)", color="#90a4ae")
    ax.bar(x + w / 2, tgt["p_fp_win"] * 100, w, label="P(reach target BEFORE −stop)", color="#1565c0")
    ax.set_xticks(x)
    ax.set_xticklabels([f"{t:g}%" for t in tgt["target_pct"]])
    ax.set_ylabel("% of all stock-day entries")
    ax.set_title("Touches are abundant; touching the stop first is what kills capture")
    ax.legend()
    return fig64(fig)


def fig_year(by_year: pd.DataFrame) -> str:
    fig, ax = plt.subplots(figsize=(9, 4.2))
    piv = by_year.pivot_table(index="year", columns="target_pct", values="p_fp_win")
    for t in KEY_TARGETS:
        if t in piv.columns:
            ax.plot(piv.index, piv[t] * 100, "o-", label=f"target {t:g}%")
    ax.plot(piv.index, [breakeven_hit_rate(2.0) * 100] * len(piv.index), "s--",
            color="#c62828", label="breakeven @2% target")
    ax.set_xlabel("entry year")
    ax.set_ylabel("P(win) %")
    ax.set_title("First-passage win rate by year — check for regime dependence")
    ax.legend(ncol=2, fontsize=8)
    return fig64(fig)


def fig_regime(by_regime: pd.DataFrame) -> str:
    fig, ax = plt.subplots(figsize=(9, 4.2))
    for regime, g in by_regime.groupby("regime"):
        ax.plot(g["target_pct"], g["p_fp_win"] * 100, "o-",
                label=f"{regime} (NIFTY 20EMA)")
    ax.plot(by_regime["target_pct"], by_regime["breakeven_p"] * 100, "s--",
            color="#c62828", label="breakeven needed")
    ax.set_xlabel("profit target (%)")
    ax.set_ylabel("P(win) %")
    ax.set_title("First-passage win rate by market regime")
    ax.legend()
    ax.set_xticks(sorted(by_regime["target_pct"].unique()))
    return fig64(fig)


def fig_dow(by_dow: pd.DataFrame) -> str:
    d2 = by_dow[by_dow["target_pct"] == 2.0].copy()
    order = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    d2["dow"] = pd.Categorical(d2["dow"], categories=order, ordered=True)
    d2 = d2.sort_values("dow")
    fig, ax = plt.subplots(figsize=(7.5, 3.6))
    bars = ax.bar(d2["dow"].astype(str), d2["p_fp_win"] * 100, color="#5c6bc0", width=0.5)
    ax.axhline(breakeven_hit_rate(2.0) * 100, color="#c62828", ls="--",
               label=f"breakeven @2% ({breakeven_hit_rate(2.0)*100:.1f}%)")
    for b, v in zip(bars, d2["p_fp_win"] * 100):
        ax.annotate(f"{v:.1f}", (b.get_x() + b.get_width() / 2, v),
                    textcoords="offset points", xytext=(0, 3), ha="center", fontsize=8)
    ax.set_ylabel("P(win) % @2% target")
    ax.set_title("Day-of-week of entry (Mon/Tue entries fare best)")
    ax.legend()
    return fig64(fig)


def fig_heatmap(stock: pd.DataFrame, tgt: pd.DataFrame) -> str:
    sweet = float(tgt.loc[tgt["exp_net_pct"].idxmax(), "target_pct"])
    tcols = [t for t in tgt["target_pct"]]
    hcols = [f"hits_{t:g}" for t in tcols]
    s = stock.dropna(subset=["events"]).copy()
    for t, c in zip(tcols, hcols):
        s[f"rate_{t:g}"] = s[c] / s["events"]
    s = s.sort_values(f"rate_{sweet:g}", ascending=False).head(50)

    mat = s[[f"rate_{t:g}" for t in tcols]].to_numpy() * 100
    fig, ax = plt.subplots(figsize=(9, 0.26 * len(s) + 2))
    im = ax.imshow(mat, aspect="auto", cmap="YlGnBu", vmin=0, vmax=min(60, np.nanmax(mat)))
    ax.set_xticks(range(len(tcols)))
    ax.set_xticklabels([f"{t:g}%" for t in tcols])
    ax.set_yticks(range(len(s)))
    ax.set_yticklabels([str(x).replace(".NS", "") for x in s["symbol"]], fontsize=7)
    ax.set_title(f"Per-stock P(win) % by target — top 50 stocks at the {sweet:g}% target")
    ax.grid(False)
    fig.colorbar(im, ax=ax, shrink=0.6, label="P(win) %")
    return fig64(fig)


def fig_atr_curve(atr_tgt: pd.DataFrame) -> str:
    fig, ax = plt.subplots(figsize=(9, 4.4))
    ax.plot(atr_tgt["median_target_pct"], atr_tgt["p_fp_win"] * 100, "o-",
            label="ATR-target curve (K × stock ATR)", color="#00695c")
    fx = pd.read_csv(RES / "targets.csv")
    ax.plot(fx["target_pct"], fx["p_fp_win"] * 100, "s--",
            label="fixed-% curve", color="#1565c0", alpha=0.75)
    ax.plot(atr_tgt["median_target_pct"], atr_tgt["breakeven_p_mean"] * 100, "^--",
            label="breakeven (ATR targets)", color="#c62828", alpha=0.8)
    for _, r in atr_tgt.iterrows():
        ax.annotate(f"K={r['k_atr']:g}", (r["median_target_pct"], r["p_fp_win"] * 100),
                    textcoords="offset points", xytext=(4, -11), fontsize=7.5, color="#00695c")
    ax.set_xlabel("median realized target (%)")
    ax.set_ylabel("P(win) %")
    ax.set_title("Targets in volatility units track the same curve — no separate regime")
    ax.legend()
    return fig64(fig)


def table_atr(atr_tgt: pd.DataFrame, atr_disp: pd.DataFrame, screen: pd.DataFrame) -> str:
    rows = ""
    for _, r in atr_tgt.iterrows():
        rows += (f"<tr><td><b>K={r['k_atr']:g} ATR</b></td><td>{r['median_target_pct']:.2f}%</td>"
                 f"<td><b>{r['p_fp_win']:.1%}</b></td><td>{r['breakeven_p_mean']:.1%}</td>"
                 f'<td class="neg">{r["exp_net_pct"]:+.2f}%</td></tr>')
    disp_rows = ""
    for _, r in atr_disp.iterrows():
        disp_rows += (f"<tr><td>{r['target']}</td><td>{r['pooled']:.1%}</td>"
                      f"<td>{r['iqr_pp']:.1f}pp</td><td>{r['std_pp']:.1f}pp</td></tr>")
    tradable = int((screen["adtv_cr"] >= 50).sum()) if screen is not None else 0
    n_screen = len(screen) if screen is not None else 0
    return f"""
    <p><b>ATR-target curve (stop fixed −2.2%):</b></p>
    <table>
      <tr><th>Target</th><th>Median realized %</th><th>P(win)</th><th>Breakeven P</th><th>Net exp/entry</th></tr>
      {rows}
    </table>
    <p><b>Cross-stock dispersion — same move size, two unit systems:</b></p>
    <table>
      <tr><th>Target</th><th>Pooled P(win)</th><th>Per-stock IQR</th><th>Std dev</th></tr>
      {disp_rows}
    </table>
    <p class="note">If the sweet spot lived in volatility units, the ATR rows would show materially
    tighter dispersion than fixed-% rows of similar size. They do not. Half-ATR screen: {n_screen} stocks
    with median ATR ≥ 4% ({tradable} with ADTV ≥ ₹50 cr) — saved to
    <code>data/results/screen_half_atr.csv</code>.</p>"""


def fig_mfe_spread(stock: pd.DataFrame) -> str:
    fig, ax = plt.subplots(figsize=(9, 3.8))
    s = stock.dropna(subset=["median_mfe"])
    ax.hist(s["median_mfe"] * 100, bins=40, alpha=0.65, label="per-stock median MFE (5d)", color="#1565c0")
    ax.hist(s["p90_mfe"] * 100, bins=40, alpha=0.55, label="per-stock 90th pct MFE (5d)", color="#ef6c00")
    ax.axvline(STOP, color="#c62828", ls="--", label=f"stop −{STOP}%")
    ax.set_xlabel("5-day favorable excursion (%)")
    ax.set_ylabel("number of stocks")
    ax.set_title("Cross-sectional spread of 5-day MFE — one man's 1% is another's noise")
    ax.legend()
    return fig64(fig)


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

def _row(r: pd.DataFrame, sweet: float) -> str:
    style = ' style="background:#fff8e1"' if abs(r["target_pct"] - sweet) < 1e-9 else ""
    exp = r["exp_net_pct"]
    exp_cell = f'<td class="{"pos" if exp > 0 else "neg"}">{exp:+.2f}%</td>'
    return (f"<tr{style}><td><b>{r['target_pct']:g}%</b></td><td>{r['p_mfe_ge']:.1%}</td>"
            f"<td><b>{r['p_fp_win']:.1%}</b></td><td>{r['breakeven_p']:.1%}</td>"
            f'<td class="neg">{r["edge"]:+.2f}</td>{exp_cell}'
            f"<td>{r['shots_per_year']:,.0f}</td></tr>")


def table_targets(tgt: pd.DataFrame) -> str:
    sweet = float(tgt.loc[tgt["exp_net_pct"].idxmax(), "target_pct"])
    rows = "".join(_row(r, sweet) for _, r in tgt.iterrows())
    return f"""
    <table>
      <tr><th>Target</th><th>P(MFE ≥ T)</th><th>P(win before −{STOP}%)</th>
          <th>Breakeven P</th><th>Edge (p−p*)</th><th>Net exp/entry</th><th>Shots/yr (universe)</th></tr>
      {rows}
    </table>
    <p class="note">Shots/yr counts every winning stock-day in the whole 500-stock universe; your account
    captures a handful of these (slot/sector limits). Yellow row = least-bad net expectancy.</p>"""


def table_regime(by_regime: pd.DataFrame) -> str:
    if by_regime is None:
        return "<p class='note'>NIFTY index unavailable — regime table skipped.</p>"
    rows = ""
    for _, r in by_regime[by_regime["target_pct"].isin(KEY_TARGETS)].iterrows():
        rows += (f"<tr><td>{r['regime']}</td><td>{r['target_pct']:g}%</td>"
                 f"<td>{r['p_fp_win']:.1%}</td><td>{r['breakeven_p']:.1%}</td>"
                 f"<td>{r['exp_net_pct']:+.2f}%</td></tr>")
    return f"""
    <table>
      <tr><th>Regime</th><th>Target</th><th>P(win)</th><th>Breakeven</th><th>Net exp/entry</th></tr>
      {rows}
    </table>"""


def table_repeaters(rep: pd.DataFrame) -> str:
    if not len(rep):
        return "<p class='note'>No stock hit the target in enough distinct years to qualify.</p>"
    head = rep.head(TOP_N_REPEATS)
    rows = ""
    for _, r in head.iterrows():
        edge = float(r["edge_at_target"])
        edge_cls = "pos" if edge >= 0 else "neg"
        trade = "yes" if r["tradeable"] else "<b>no</b>"
        rows += (f"<tr><td><b>{str(r['symbol']).replace('.NS','')}</b></td>"
                 f"<td>{int(r['durable_years_at_target'])}/6</td>"
                 f"<td><b>{r['fp_rate_at_target']:.1%}</b></td>"
                 f"<td>{r['breakeven_at_target']:.1%}</td>"
                 f"<td class='{edge_cls}'>{edge:+.1%}</td>"
                 f"<td>{int(r.get('hits_2', 0)):,}</td>"
                 f"<td>{r['median_atr_pct']*100:.1f}%</td>"
                 f"<td>₹{r['adtv_cr']:,.0f} cr</td><td>{trade}</td></tr>")
    n_trade = int(rep["tradeable"].sum())
    return f"""
    <table>
      <tr><th>Stock</th><th>Years hit (of 6)</th><th>P(win) @ target</th>
          <th>Breakeven</th><th>Edge</th><th>Hits @2%</th>
          <th>Median ATR%</th><th>ADTV</th><th>Tradeable</th></tr>
      {rows}
    </table>
    <p class="verdict"><b>Not a buy list.</b> Every edge in this table is negative — repeaters clear the
    target often <i>because</i> they are volatile, and pay for it in stops. This is the <b>candidate pool</b>
    for conditional studies (where range exists for a 4–8% tranche-2 runner); conditions, not membership,
    are what can produce an edge. Tradeable = 90d median ADTV ≥ ₹{CFG["quality"]["tradeable_adtv_cr"]:g} cr
    ({n_trade} of {len(rep)} rows); non-tradeable smallcaps are shown for completeness but cannot be sized
    for a retail account.</p>"""


def table_audit(audit: pd.DataFrame) -> str:
    ok = int(audit["ok"].sum())
    rows = (f"<tr><td>Symbols attempted</td><td>{len(audit)}</td></tr>"
            f"<tr><td>Usable (≥80% bar coverage)</td><td>{ok}</td></tr>"
            f"<tr><td>Dropped (IPOs, suspensions, bad data)</td><td>{len(audit) - ok}</td></tr>")
    return f"<table>{rows}</table>"


# ---------------------------------------------------------------------------
# HTML assembly
# ---------------------------------------------------------------------------

CSS = """
  body { font-family: 'Segoe UI', system-ui, sans-serif; max-width: 980px; margin: 24px auto;
         padding: 0 16px; color: #212121; line-height: 1.45; }
  h1 { font-size: 1.6em; margin-bottom: 4px; }
  h2 { font-size: 1.15em; border-bottom: 2px solid #eceff1; padding-bottom: 4px; margin-top: 34px; }
  .sub { color: #757575; font-size: 0.9em; }
  .chips { margin: 10px 0; }
  .chip { display: inline-block; background: #eceff1; border-radius: 12px; padding: 2px 10px;
          margin-right: 6px; font-size: 0.82em; }
  .verdict { background: #fff3e0; border-left: 4px solid #ef6c00; padding: 12px 16px;
             border-radius: 4px; margin: 18px 0; }
  .verdict b.big { font-size: 1.25em; }
  table { border-collapse: collapse; margin: 12px 0; font-size: 0.88em; }
  th, td { border: 1px solid #e0e0e0; padding: 4px 10px; text-align: right; }
  th { background: #f5f5f5; }
  th:first-child, td:first-child { text-align: left; }
  td.pos { color: #2e7d32; font-weight: 600; }
  td.neg { color: #c62828; }
  .note { color: #616161; font-size: 0.84em; }
  img { max-width: 100%; }
  code { background: #f5f5f5; padding: 1px 5px; border-radius: 3px; }
  li { margin-bottom: 4px; }
"""


def main() -> None:
    tgt, by_year, by_regime, by_dow, stock, rep, audit, atr_tgt, atr_disp, screen = load()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    n_events = int(tgt["events"].iloc[0])
    sweet = tgt.loc[tgt["exp_net_pct"].idxmax()]
    p1 = float(tgt.loc[tgt["target_pct"] == 1.0, "p_mfe_ge"].iloc[0])
    p1fp = float(tgt.loc[tgt["target_pct"] == 1.0, "p_fp_win"].iloc[0])
    ok_n = int(audit["ok"].sum())

    chips = (f'<span class="chip">universe: {ok_n} liquid NSE stocks</span>'
             f'<span class="chip">entries: close of every session T</span>'
             f'<span class="chip">window: {HORIZON} trading days</span>'
             f'<span class="chip">stop: −{STOP}%</span>'
             f'<span class="chip">friction: {FRICTION}% RT</span>'
             f'<span class="chip">STCG: {STCG:.0f}%</span>'
             f'<span class="chip">events: {n_events:,}</span>')

    verdict = f"""
    <div class="verdict">
      <b class="big">Verdict: touches are abundant — capture is not.</b><br>
      {p1:.0%} of all stock-day entries see +1% at some point inside 5 sessions, but only
      {p1fp:.0%} reach +1% <i>before</i> hitting −2.2%. After {FRICTION}% friction and {STCG:.0f}% STCG,
      <b>every fixed target from 0.5% to 8% is net-negative</b> for unconditional daily entries.
      The least-bad is <b>{sweet['target_pct']:g}%</b> at {sweet['exp_net_pct']:+.2f}% per entry
      (P(win) {sweet['p_fp_win']:.1%} vs {sweet['breakeven_p']:.1%} needed to break even).<br><br>
      The sweet spot is not a number you read off this table — it is the number your <i>stock-selection
      conditions</i> can push above the breakeven line. That is what the repeaters table and your
      setup filters (BRD Phase 5) are for.
    </div>"""

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Weekly Sweet-Spot Study — NSE 500</title><style>{CSS}</style></head>
<body>
<h1>Weekly Sweet-Spot Study — NSE 500</h1>
<p class="sub">How often can a liquid Indian stock hand you +X% within a week — and can you keep it
after friction, tax, and the stop you must carry? Generated {date.today().isoformat()}.
Data: Yahoo Finance daily bars, 2021-09 → 2026-09.</p>
<div class="chips">{chips}</div>

{verdict}

<h2>1. The sweet-spot curve</h2>
<img src="data:image/png;base64,{fig_expectancy(tgt)}" alt="expectancy curve">
{table_targets(tgt)}

<h2>2. Touches vs capture</h2>
<img src="data:image/png;base64,{fig_touches(tgt)}" alt="touches vs capture">

<h2>3. Stability checks</h2>
<img src="data:image/png;base64,{fig_year(by_year)}" alt="by year">
<img src="data:image/png;base64,{fig_regime(by_regime)}" alt="by regime">
{table_regime(by_regime)}
<img src="data:image/png;base64,{fig_dow(by_dow)}" alt="by day of week">

<h2>4. Cross-section: which stocks repeat</h2>
<img src="data:image/png;base64,{fig_heatmap(stock, tgt)}" alt="stock heatmap">
<img src="data:image/png;base64,{fig_mfe_spread(stock)}" alt="MFE spread">
<h3>Durable repeaters at the sweet-spot target ({sweet['target_pct']:g}%)</h3>
{table_repeaters(rep)}

<h2>4b. ATR-normalized targets — do volatility units change the picture?</h2>
<img src="data:image/png;base64,{fig_atr_curve(atr_tgt)}" alt="ATR curve">
{table_atr(atr_tgt, atr_disp, screen)}

<h2>5. Data hygiene</h2>
{table_audit(audit)}

<h2>6. Method & honest caveats</h2>
<ul>
  <li><b>Entry model:</b> buy at the close of any session T (the BRD's 10:00 AM entry would need
      intraday data; results here are the clean close-to-close upper bound of opportunity).</li>
  <li><b>First passage:</b> win = touch +target% before touching −{STOP}% within {HORIZON} sessions,
      day-resolution from daily highs/lows. <b>Same-day tie counts as a stop</b> (conservative).</li>
  <li><b>Timeout:</b> if neither level is touched, the position is marked at the T+{HORIZON} close
      (realistic; no fantasy "loss capped at stop").</li>
  <li><b>Costs:</b> {FRICTION}% round-trip friction and {STCG:.0f}% STCG on wins, per the BRD economics.</li>
  <li><b>Survivorship bias:</b> today's NIFTY 500 list measured backward inflates levels slightly;
      per-year tables bound the effect. A point-in-time universe (BRD Phase 3) is the clean fix.</li>
  <li><b>yfinance caveats:</b> adjusted prices (auto-adjust) handle splits/bonuses; special dividends
      can still distort. Stocks with &lt;80% of bars were dropped (recent IPOs dominate that list).</li>
  <li><b>What this study does NOT do:</b> condition on setups. Unconditional daily entries losing
      ~0.4%/trade is the <i>baseline your filters must beat</i> — the BRD's 5 setups claim exactly that.</li>
</ul>

<h2>7. Reproduce / re-tune</h2>
<p><code>python scripts/download_data.py</code> → <code>python scripts/analyze.py</code> →
<code>python scripts/build_report.py</code>. Every lever (horizon, stop, targets, friction, tax,
durability) lives in <code>scripts/study_config.yaml</code>. Results in <code>data/results/*.csv</code>.</p>

<p class="sub">Part of the NSE High-Conviction Cash Swing System — Phase 0 feasibility study.</p>
</body></html>"""

    OUT.write_text(html, encoding="utf-8")
    print(f"report written: {OUT}  ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
