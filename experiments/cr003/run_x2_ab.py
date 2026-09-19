"""CR-2026-003 X2: S3/S4-only cohort + wide stops with risk-parity sizing.

Funnel base for every arm: Setup 2 pruned, S_runner gate ENFORCED (arm C),
X1b exit geometry (t1_enabled=False, time_stop_enabled=False, 20 sessions).

Arms:
  X1b_control       : the X1 winner verbatim — S1/S3/S4/S5 funnel, 2.2% wall.
                      Seeded from reports/cr003/x1_time_stop/X1b_no_t1__*
                      (identical config, assert-verified) when absent.
  X2a_narrow        : catalog = S3+S4 only. Stops stay walled at 2.2%
                      (risk_parity_stops=False). Isolates the COHORT effect.
  X2b_wide_parity   : X2a + risk_parity_stops=True + S3 gate widened to
                      3.5% (S4 already carries its wide gate at 3.5% via
                      setup4_max_stop_pct; its retest geometry stays the
                      historical 0.8% — only the STOP width changes here).
                      Isolates the STOP-ROOM + PARITY-SIZING effect.

Windows: bt15y = 2011-09-19..2026-09-18 (replays to store end 2026-09-17),
wf3y = 2023-09-19..2026-09-18. Resumable on existing metrics files.

Artifacts: reports/cr003/x2_risk_parity/<arm>__<window>__{metrics,per_setup}.json
+ trades.parquet + run.log
"""

from __future__ import annotations

import json
import logging
import shutil
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
OUT = ROOT / "reports" / "cr003" / "x2_risk_parity"
OUT.mkdir(parents=True, exist_ok=True)

BT_START, BT_END = date(2011, 9, 19), date(2026, 9, 18)
WF_START, WF_END = date(2023, 9, 19), date(2026, 9, 18)

WINDOWS = {
    "bt15y": (BT_START, BT_END),
    "wf3y": (WF_START, WF_END),
}

_X1B_GEOMETRY = {"t1_enabled": False, "time_stop_enabled": False,
                 "max_holding_days": 20}

ARMS: dict[str, SystemConfig] = {
    "X1b_control": SystemConfig(risk={**_X1B_GEOMETRY}),
    "X2a_narrow": SystemConfig(
        catalog={"enable_setup1": False, "enable_setup2": False,
                 "enable_setup3": True, "enable_setup4": True,
                 "enable_setup5": False},
        ranking={"enforce_s_runner_gate": True},
        risk={**_X1B_GEOMETRY}),
    "X2b_wide_parity": SystemConfig(
        catalog={"enable_setup1": False, "enable_setup2": False,
                 "enable_setup3": True, "enable_setup4": True,
                 "enable_setup5": False,
                 "setup3_max_stop_pct": 0.035},
        ranking={"enforce_s_runner_gate": True},
        risk={**_X1B_GEOMETRY, "risk_parity_stops": True}),
}

PER_TRADE_COLS = ["trade_id", "symbol", "setup", "entry_date", "exit_date",
                  "entry_price", "exit_price", "exit_reason", "realized_pnl",
                  "mfe_pct", "mae_pct", "mfe_day", "mae_day"]

SEED = {"X1b_control": ("X1b_no_t1", ROOT / "reports" / "cr003" / "x1_time_stop")}


def per_setup_table(trades: pd.DataFrame) -> dict:
    rows = {}
    for setup, g in trades.groupby("setup"):
        pnl = g["realized_pnl"].astype(float)
        exits = g["exit_reason"].value_counts().to_dict()
        mfe = g["mfe_pct"].astype(float)
        rows[str(setup)] = {
            "trades": int(len(g)),
            "wins": int((pnl > 0).sum()),
            "win_rate": round(float((pnl > 0).mean()), 4),
            "pnl_net": round(float(pnl.sum()), 2),
            "avg_pnl": round(float(pnl.mean()), 2),
            "median_pnl": round(float(pnl.median()), 2),
            "avg_mfe_pct": round(float(mfe.mean()), 4),
            "exit_mix": {k: int(v) for k, v in sorted(exits.items())},
        }
    return rows


def _seed_control_runs() -> None:
    """Copy X1b artifacts as the control (config-identity assert first)."""
    src_arm, src_dir = SEED["X1b_control"]
    for window in WINDOWS:
        m = OUT / f"X1b_control__{window}__metrics.json"
        if m.exists():
            continue
        src = src_dir / f"{src_arm}__{window}__metrics.json"
        if not src.exists():
            raise FileNotFoundError(
                f"cannot seed X1b_control/{window}: missing {src}")
        payload = json.loads(src.read_text())
        assert payload["config"] == {
            "enable_setup2": False,
            "enforce_s_runner_gate": True,
            "t1_enabled": False,
            "time_stop_enabled": False,
            "max_holding_days": 20,
        }, "seeded control config drifted from X1b"
        payload["arm"] = "X1b_control"
        payload["seeded_from"] = str(src)
        m.write_text(json.dumps(payload, indent=2, default=str))
        ps = json.loads((src_dir / f"{src_arm}__{window}__per_setup.json")
                        .read_text())
        (OUT / f"X1b_control__{window}__per_setup.json").write_text(
            json.dumps(ps, indent=2))
        shutil.copy2(src_dir / f"{src_arm}__{window}__trades.parquet",
                     OUT / f"X1b_control__{window}__trades.parquet")
        print(f"[seed] X1b_control__{window} <- {src.name}", flush=True)


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
            "enable_setup1": cfg.catalog.enable_setup1,
            "enable_setup2": cfg.catalog.enable_setup2,
            "enable_setup3": cfg.catalog.enable_setup3,
            "enable_setup4": cfg.catalog.enable_setup4,
            "enable_setup5": cfg.catalog.enable_setup5,
            "enforce_s_runner_gate": cfg.ranking.enforce_s_runner_gate,
            "setup3_max_stop_pct": cfg.catalog.setup3_max_stop_pct,
            "risk_parity_stops": cfg.risk.risk_parity_stops,
            "t1_enabled": cfg.risk.t1_enabled,
            "time_stop_enabled": cfg.risk.time_stop_enabled,
            "max_holding_days": cfg.risk.max_holding_days,
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
    _seed_control_runs()
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
