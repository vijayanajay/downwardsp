"""A/B: baseline vs Setup-2-disabled, walk-forward 2023-present.

One variable: remove SETUP_2_RUBBERBAND from the evaluator catalog (patched in
both nse_cash.setups.catalog and nse_cash.setups.ranking, which holds its own
imported reference). Everything else — config, store, engine, metrics — is the
identical process that produced reports/backtest/tear_sheet.json.

Deterministic engine => arm A must reproduce the persisted baseline (216
trades); the script asserts it before trusting arm B.

Run:  .venv/Scripts/python.exe experiments/cr002/ab_disable_setup2.py
Writes experiments/cr002/ab_disable_setup2.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from datetime import date as Date

from nse_cash.backtest.engine import run_backtest
from nse_cash.backtest.metrics import compute_metrics
from nse_cash.core.config import load_config
from nse_cash.core.types import SetupID
from nse_cash.data.storage import MarketStore
import nse_cash.setups.catalog as catalog
import nse_cash.setups.ranking as ranking

START = Date(2023, 1, 1)
END = Date(2026, 9, 17)
OUT = Path(__file__).with_name("ab_disable_setup2.json")


def run_arm(store: MarketStore, config, label: str) -> dict:
    result = run_backtest(store, config, START, END)
    metrics = compute_metrics(store, result, index_name="NIFTY 500")
    tr = result.trades
    if not tr.empty:
        tr = tr.assign(year=pd.to_datetime(tr["entry_date"]).dt.year)
        per_year = tr.groupby("year")["realized_pnl"].agg(["count", "sum"])
        win_year = (tr[tr["realized_pnl"] > 0].groupby("year").size()
                    / tr.groupby("year").size()).round(3).to_dict()
        setup_exp = (tr.assign(ret=tr["realized_pnl"] / tr["entry_cost"] * 100)
                     .dropna(subset=["ret"]).groupby("setup")["ret"]
                     .agg(["count", "mean", "median"]).round(3))
    else:
        per_year, win_year, setup_exp = pd.DataFrame(), {}, pd.DataFrame()
    print(f"\n=== ARM {label} ===")
    print(f"  trades={metrics['trades']} win_rate={metrics['win_rate']:.4f} "
          f"pf={metrics['profit_factor']} expectancy_net_pct={metrics['expectancy_net_pct']}")
    print(f"  cagr_pre={metrics['cagr_pre_tax']:.4f} cagr_post={metrics['cagr_post_tax']:.4f} "
          f"maxDD={metrics['max_drawdown']:.4f} sharpe={metrics['sharpe']}")
    print(f"  trading PnL=Rs {tr['realized_pnl'].sum():,.0f} "
          f"interest=Rs {metrics['total_interest']:,.0f}")
    print(f"  trades/yr: {metrics['trades_per_year']}")
    print(f"  win rate/yr: {win_year}")
    print(f"  PnL/yr: {per_year['sum'].round(0).to_dict() if not per_year.empty else {}}")
    print(f"  setup counts: {metrics['setup_trades']}")
    print(f"  setup PnL: {metrics['setup_pnl']}")
    print(f"  per-setup expectancy %:\n{setup_exp.to_string()}" if not setup_exp.empty else "")
    return {
        "label": label,
        "metrics": metrics,
        "trades_per_year": metrics["trades_per_year"],
        "win_rate_by_year": win_year,
        "pnl_by_year": {str(k): float(v) for k, v in
                        (per_year["sum"].round(0).to_dict().items()
                         if not per_year.empty else [])},
        "setup_trades": metrics["setup_trades"],
        "setup_pnl": metrics["setup_pnl"],
        "per_setup_expectancy_pct": setup_exp.reset_index().to_dict(orient="records"),
        "trading_pnl": float(tr["realized_pnl"].sum()) if not tr.empty else 0.0,
    }


def main() -> None:
    config = load_config()
    store = MarketStore(Path(config.paths.duckdb_path), read_only=False)
    orig_catalog = list(catalog.SETUP_EVALUATORS)
    orig_ranking = list(ranking.SETUP_EVALUATORS)
    try:
        arm_a = run_arm(store, config, "A baseline (CR-001, all 5 setups)")
        assert arm_a["metrics"]["trades"] == 216, \
            f"arm A does not reproduce persisted baseline (216): {arm_a['metrics']['trades']}"

        no_s2 = [(sid, fn) for sid, fn in orig_catalog
                 if sid is not SetupID.SETUP_2_RUBBERBAND]
        catalog.SETUP_EVALUATORS = no_s2
        ranking.SETUP_EVALUATORS = no_s2
        arm_b = run_arm(store, config, "B Setup-2 disabled")
    finally:
        catalog.SETUP_EVALUATORS = orig_catalog
        ranking.SETUP_EVALUATORS = orig_ranking
        store.close()

    a, b = arm_a["metrics"], arm_b["metrics"]
    print("\n" + "=" * 78)
    print("A/B SUMMARY (A = CR-001 baseline, B = Setup 2 disabled)")
    rows = [
        ("trades", a["trades"], b["trades"]),
        ("trades/yr avg", round(a["trades"] / 3.71, 1), round(b["trades"] / 3.71, 1)),
        ("win rate", f"{a['win_rate']:.1%}", f"{b['win_rate']:.1%}"),
        ("profit factor", a["profit_factor"], b["profit_factor"]),
        ("expectancy net %/trade", a["expectancy_net_pct"], b["expectancy_net_pct"]),
        ("avg win net %", a["avg_win_net_pct"], b["avg_win_net_pct"]),
        ("avg loss net %", a["avg_loss_net_pct"], b["avg_loss_net_pct"]),
        ("CAGR pre-tax", f"{a['cagr_pre_tax']:+.2%}", f"{b['cagr_pre_tax']:+.2%}"),
        ("CAGR post-tax", f"{a['cagr_post_tax']:+.2%}", f"{b['cagr_post_tax']:+.2%}"),
        ("max drawdown", f"{a['max_drawdown']:.2%}", f"{b['max_drawdown']:.2%}"),
        ("sharpe", a["sharpe"], b["sharpe"]),
        ("trading PnL Rs", round(arm_a["trading_pnl"]), round(arm_b["trading_pnl"])),
        ("interest Rs", a["total_interest"], b["total_interest"]),
        ("kill switches", a["kill_switches"], b["kill_switches"]),
    ]
    for name, va, vb in rows:
        print(f"  {name:24s} A={va!s:>12s}   B={vb!s:>12s}")
    print(f"\n  S2 contribution: trades {a['setup_trades'].get('SETUP_2_RUBBERBAND', 0)}"
          f" -> {b['setup_trades'].get('SETUP_2_RUBBERBAND', 0)}; "
          f"PnL {arm_a['setup_pnl'].get('SETUP_2_RUBBERBAND', 0):,.0f} -> "
          f"{arm_b['setup_pnl'].get('SETUP_2_RUBBERBAND', 0):,.0f}")

    OUT.write_text(json.dumps({"arm_A": arm_a, "arm_B": arm_b}, indent=2, default=str),
                   encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
