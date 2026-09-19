"""CR-2026-003 X1: exit-geometry A/B — T1 elimination + time-stop knobs.

Arms (risk geometry only; catalog/ranking flags identical to arm C — the
15-year winner: Setup 2 pruned, S_runner gate enforced):
  C_x1_control : risk.t1_enabled=True,  time_stop_enabled=True, days=5
                 (the shipped geometry: T1 +2% guillotine, Day-2 stall,
                 Day-5 time exit — the exact arm-C control for this A/B)
  X1a_time20   : same tranche geometry, time_stop ladder off,
                 max_holding_days=20 — the horizon experiment in isolation
  X1b_no_t1    : T1 tranche eliminated (full-size position, no breakeven
                 arm) + time_stop off + 20 sessions — the cohort-arithmetic
                 configuration (the paying shape per exit_geometry analysis)

Windows: bt15y = 2011-09-19..2026-09-18 (engine replays to store end
2026-09-17), wf3y = 2023-09-19..2026-09-18. Resumable: a run is skipped when
its metrics file exists. Arm C's two runs are seeded by copying the existing
15y_3y C_B1_only artifacts (identical config) if the x1 metrics file is
absent — one honest engine run, no duplicate compute.

Artifacts: reports/cr003/x1_time_stop/<arm>__<window>__{metrics,per_setup}.json
+ trades.parquet (with MFE/MAE telemetry columns) + run.log
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
OUT = ROOT / "reports" / "cr003" / "x1_time_stop"
OUT.mkdir(parents=True, exist_ok=True)

BT_START, BT_END = date(2011, 9, 19), date(2026, 9, 18)
WF_START, WF_END = date(2023, 9, 19), date(2026, 9, 18)

WINDOWS = {
    "bt15y": (BT_START, BT_END),
    "wf3y": (WF_START, WF_END),
}

# Catalog/ranking = arm C exactly; X1 changes ONLY risk geometry.
_C_ARM = {"catalog": {"enable_setup2": False},
          "ranking": {"enforce_s_runner_gate": True}}

ARMS: dict[str, SystemConfig] = {
    "C_x1_control": SystemConfig(
        **_C_ARM,
        risk={"t1_enabled": True, "time_stop_enabled": True,
              "max_holding_days": 5}),
    "X1a_time20": SystemConfig(
        **_C_ARM,
        risk={"t1_enabled": True, "time_stop_enabled": False,
              "max_holding_days": 20}),
    "X1b_no_t1": SystemConfig(
        **_C_ARM,
        risk={"t1_enabled": False, "time_stop_enabled": False,
              "max_holding_days": 20}),
}

PER_TRADE_COLS = ["trade_id", "symbol", "setup", "entry_date", "exit_date",
                  "entry_price", "exit_price", "exit_reason", "realized_pnl",
                  "mfe_pct", "mae_pct", "mfe_day", "mae_day"]

SEED = {"C_x1_control": ("C_B1_only", ROOT / "reports" / "cr003" / "15y_3y")}


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
    """Copy the 15y_3y C_B1_only artifacts as C_x1_control (same config).

    Verifies the config identity before seeding: enable_setup2=False +
    enforce_s_runner_gate=True + the legacy risk geometry. The seeded trades
    parquet is widened with zero-fill telemetry columns (the 15y_3y run
    predates the MFE/MAE columns) so per-setup tables stay uniform.
    """
    src_arm, src_dir = SEED["C_x1_control"]
    for window in WINDOWS:
        m = OUT / f"C_x1_control__{window}__metrics.json"
        if m.exists():
            continue
        src = src_dir / f"{src_arm}__{window}__metrics.json"
        if not src.exists():
            raise FileNotFoundError(
                f"cannot seed C_x1_control/{window}: missing {src}")
        payload = json.loads(src.read_text())
        assert payload["config"] == {"enable_setup2": False,
                                     "enforce_s_runner_gate": True}, \
            "seeded control config drifted from arm C"
        payload["arm"] = "C_x1_control"
        payload["seeded_from"] = str(src)
        m.write_text(json.dumps(payload, indent=2, default=str))
        ps = json.loads((src_dir / f"{src_arm}__{window}__per_setup.json")
                        .read_text())
        (OUT / f"C_x1_control__{window}__per_setup.json").write_text(
            json.dumps(ps, indent=2))
        t = pd.read_parquet(src_dir / f"{src_arm}__{window}__trades.parquet")
        for col in ("mfe_pct", "mae_pct"):
            if col not in t.columns:
                t[col] = 0.0
        t.to_parquet(OUT / f"C_x1_control__{window}__trades.parquet",
                     index=False)
        print(f"[seed] C_x1_control__{window} <- {src.name}", flush=True)


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
