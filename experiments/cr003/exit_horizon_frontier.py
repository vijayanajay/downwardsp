"""CR-2026-004 §2.1: exit-horizon stop/horizon policy frontier.

Post-processes the CR-003 cohort study parquet (no engine, no new assumptions):
enter T+1 open (engine parity), disaster stop at s, exit at close of day H.
Stop model: mae_H > s -> exit at -s (optimistic; engine gap-through semantics
are stricter and are verified separately in Phase D.3).

Read-only. Output mirrors probe_output_levers.txt conventions.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
PARQUET = ROOT / "reports" / "cr003" / "exit_geometry" / "cohort_signals_forward_returns.parquet"

FRICTION = 0.0035  # engine buy+sell round trip
STCG = 0.20
SLOT = 62_500.0    # tranche deployment used by the cohort study
STOPS = (0.022, 0.05, 0.08, 0.10, 0.12, 1.0)
HORIZONS = (10, 20, 40)


def net(gross: np.ndarray) -> np.ndarray:
    n = np.asarray(gross) - FRICTION
    return np.where(n > 0, n * (1 - STCG), n)


def main() -> None:
    fw = pd.read_parquet(PARQUET)
    print(f"CR-2026-004 frontier | friction {FRICTION:.2%}, STCG {STCG:.0%}, "
          f"deployment Rs {SLOT:,.0f} | cohort n={len(fw)} (1-in-5 thinned)")
    print("=" * 100)
    for setup in ("SETUP_3_RS_BASE", "SETUP_4_ANCHOR_RETEST",
                  "SETUP_1_VCP", "SETUP_5_RESIDUAL_MOM"):
        g0 = fw[fw["setup"] == setup]
        print(f"\n== {setup} (thinned n={len(g0)}, ~x5 unthinned)")
        print("  H\\stop " + "".join(f"{s:>9}" for s in STOPS))
        for h in HORIZONS:
            row = f"  H={h:>3}  "
            for s in STOPS:
                mae, cl = g0[f"mae_{h}"], g0[f"close_{h}"]
                ok = mae.notna() & cl.notna()
                ret = net(np.where(mae[ok] > s, -s, cl[ok]))
                row += f"{ret.mean() * SLOT:>9.0f}"
            print(row)
        # headline cell: s=10%, H=20
        mae, cl = g0["mae_20"], g0["close_20"]
        ok = mae.notna() & cl.notna()
        ret = net(np.where(mae[ok] > 0.10, -0.10, cl[ok]))
        print(f"  H=20 s=10%: n={int(ok.sum())} win={(ret > 0).mean() * 100:.1f}% "
              f"mean Rs {ret.mean() * SLOT:+.0f} med Rs {np.median(ret) * SLOT:+.0f} "
              f"P(stop)={(mae[ok] > 0.10).mean() * 100:.1f}%")

    g = fw[fw["setup"] == "SETUP_3_RS_BASE"]
    for name, mask in (("breadth>=77.3", g["breadth_pct"] >= 77.3),
                       ("breadth<77.3", g["breadth_pct"] < 77.3)):
        gg = g[mask & g["mae_20"].notna() & g["close_20"].notna()]
        if gg.empty:
            print(f"\nS3 H20 s10 x {name}: n=0")
            continue
        ret = net(np.where(gg["mae_20"] > 0.10, -0.10, gg["close_20"]))
        print(f"\nS3 H20 s10 x {name}: n={len(gg)} (x5={len(gg) * 5}) "
              f"mean Rs {ret.mean() * SLOT:+.0f}/trade win {(ret > 0).mean() * 100:.0f}%")
    print("\nDONE")


if __name__ == "__main__":
    sys.exit(main())
