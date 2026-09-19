"""CR-2026-003: per-algo results for the 15-year backtest + 3-year walk-forward.

Windows (user spec):
  - 15-year backtest: 2011-09-19 .. 2026-09-18 (store ends 2026-09-17; the
    engine replays only dates that exist, so the end date is inclusive-up-to-data).
  - Walk-forward: 2023-09-19 .. 2026-09-18.

Arms (each = one SystemConfig; flags only, everything else identical):
  A baseline_pre_cr003 : every CR-003 flag off -> exact pre-CR-003 behavior.
  B cr003_default      : enable_setup2=False + enforce_s_runner_gate=False.
  C B1_only            : enable_setup2=False, gate still enforced.
  D E1_only            : setup 2 enabled, gate demoted.

Each arm x window runs the deterministic engine and writes:
  reports/cr003/15y_3y/<arm>__<window>__metrics.json   (portfolio metrics)
  reports/cr003/15y_3y/<arm>__<window>__per_setup.json (per-algo attribution)
  reports/cr003/15y_3y/<arm>__<window>__trades.parquet (trade rows)

Read-only wrt the market DB except the engine's own warmup healing (PIT
universe rebuild + features for the 2010-2022 stretch, one-time) and its
governance circuit-hit backfill, which run_backtest performs itself.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from nse_cash.backtest.engine import run_backtest  # noqa: E402
from nse_cash.backtest.metrics import compute_metrics  # noqa: E402
from nse_cash.core.config import SystemConfig  # noqa: E402
from nse_cash.data.storage import MarketStore  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
logging.getLogger("nse_cash").setLevel(logging.INFO)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "reports" / "cr003" / "15y_3y"
OUT.mkdir(parents=True, exist_ok=True)

BT_START, BT_END = date(2011, 9, 19), date(2026, 9, 18)
WF_START, WF_END = date(2023, 9, 19), date(2026, 9, 18)

WINDOWS = {
    "bt15y": (BT_START, BT_END),
    "wf3y": (WF_START, WF_END),
}

ARMS: dict[str, SystemConfig] = {
    "A_baseline_pre_cr003": SystemConfig(
        catalog={"enable_setup2": True},
        ranking={"enforce_s_runner_gate": True}),
    "B_cr003_default": SystemConfig(),
    "C_B1_only": SystemConfig(catalog={"enable_setup2": False},
                              ranking={"enforce_s_runner_gate": True}),
    "D_E1_only": SystemConfig(catalog={"enable_setup2": True},
                              ranking={"enforce_s_runner_gate": False}),
}

PER_TRADE_COLS = ["trade_id", "symbol", "setup", "entry_date", "exit_date",
                  "entry_price", "exit_price", "exit_reason", "realized_pnl"]


def per_setup_table(trades: pd.DataFrame) -> dict:
    """Per-algo attribution: trades, wins, net P&L, avg, hit rate, exit mix."""
    rows = {}
    for setup, g in trades.groupby("setup"):
        pnl = g["realized_pnl"].astype(float)
        exits = g["exit_reason"].value_counts().to_dict()
        rows[str(setup)] = {
            "trades": int(len(g)),
            "wins": int((pnl > 0).sum()),
            "win_rate": round(float((pnl > 0).mean()), 4),
            "pnl_net": round(float(pnl.sum()), 2),
            "avg_pnl": round(float(pnl.mean()), 2),
            "median_pnl": round(float(pnl.median()), 2),
            "worst_trade": round(float(pnl.min()), 2),
            "best_trade": round(float(pnl.max()), 2),
            "exit_mix": {k: int(v) for k, v in sorted(exits.items())},
        }
    return rows


def run_arm(store: MarketStore, arm: str, cfg: SystemConfig,
            window: str, start: date, end: date) -> None:
    tag = f"{arm}__{window}"
    metrics_path = OUT / f"{tag}__metrics.json"
    if metrics_path.exists():
        print(f"[skip] {tag} already done", flush=True)
        return
    t0 = time.time()
    print(f"[run ] {tag}: {start} .. {end}", flush=True)
    result = run_backtest(store, cfg, start, end)
    metrics = compute_metrics(store, result, index_name="NIFTY 500")
    trades = result.trades

    payload = {
        "arm": arm,
        "window": window,
        "start": str(start),
        "end": str(end),
        "config": {
            "enable_setup2": cfg.catalog.enable_setup2,
            "enforce_s_runner_gate": cfg.ranking.enforce_s_runner_gate,
        },
        "metrics": metrics,
        "runtime_seconds": round(time.time() - t0, 1),
    }
    metrics_path.write_text(json.dumps(payload, indent=2, default=str))
    (OUT / f"{tag}__per_setup.json").write_text(
        json.dumps(per_setup_table(trades), indent=2))
    cols = [c for c in PER_TRADE_COLS if c in trades.columns]
    trades[cols].to_parquet(OUT / f"{tag}__trades.parquet", index=False)
    m = metrics
    print(f"[done] {tag}: trades={m.get('trades')} win={m.get('win_rate')} "
          f"PF={m.get('profit_factor')} P&L={m.get('expectancy_net')} "
          f"CAGR={m.get('cagr_post_tax')} DD={m.get('max_drawdown')} "
          f"({payload['runtime_seconds']}s)", flush=True)


def main() -> None:
    db = ROOT / "data" / "db" / "nse_market.duckdb"
    store = MarketStore(db, read_only=False)
    try:
        for window, (start, end) in WINDOWS.items():
            for arm, cfg in ARMS.items():
                run_arm(store, arm, cfg, window, start, end)
    finally:
        store.close()
    print("ALL RUNS COMPLETE", flush=True)


if __name__ == "__main__":
    main()
