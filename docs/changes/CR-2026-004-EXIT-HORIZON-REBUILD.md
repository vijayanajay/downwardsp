# Change Request: CR-2026-004

## Exit-Horizon Rebuild: 20-Session Holds, Disaster Stops, T1 Removal & Full-Slot Deployment

* **CR Number:** `CR-2026-004`
* **Status:** `PROPOSED — EVIDENCE-FIRST, CODE-MINIMAL` (2026-09-19)
* **Predecessors:**
  * `CR-2026-003` (`IMPLEMENTED` 2026-09-19: Setup 2 pruned, breadth gate built, S5 risk-parity, S4 wide geometry, S_runner demoted)
  * `CR-2026-002` (`REJECTED`), `CR-2026-001` (`IMPLEMENTED`)
* **Evidence Base:**
  * `reports/cr003/15y_3y/` — 4 arms × (15y backtest + 3y walk-forward), metrics + per-setup + trades parquet
  * `reports/cr003/x1_time_stop/` — T1 guillotine & time-stop A/B (X1a, X1b)
  * `reports/cr003/x2_risk_parity/` — narrow vs wide risk-parity A/B (X2a, X2b)
  * `reports/cr003/exit_geometry/cohort_summary.json` + `cohort_signals_forward_returns.parquet` — 405+ signal cohorts at H=2/5/10/20/40/60
  * `experiments/cr003/probe_output_levers.txt` — Lever 2–5 truth probes on the realized 216-trade book
* **Author Context:** Kailash Nadh Pragmatic Review, continued: CR-003's levers were implemented and measured. The measurements falsified several CR-003 hypotheses and located the true blocker. This CR acts on what the data actually said.

---

## 1. Executive Summary

CR-003's rebuild lifted post-tax CAGR from **+1.93% (CTRL-001)** to **+6.08% 15y / +6.38% wf3y (Arm C: Setup 2 pruned)** — still short of the +10% acceptance bar and the +15% mandate, and still negative on trading P&L in some arms. The lever probes and engine A/Bs now isolate **why**:

> **The blocker is not entries, indicators, or breadth. It is exit geometry: a 5-session, −2.2%-stop, +2%-target holding frame wrapped around signals whose edge only exists at 20–60 sessions.**

Three independent measurements converge:

1. **The cohort study found the edge and the engine discards it.** Setup 3 signal cohorts: median gross **+3.98% at 20 sessions (63.3% win)** and **+11.23% at 40 sessions (66.7% win)**. At H≤5 — where the engine forces resolution — the same cohort is ~0%. Setup 4: +7.82% @ H40, 81.2% win.
2. **The stop-wall frontier (computed from the cohort parquet, net of 0.35% friction and 20% STCG, per ₹62.5k deployed):** a −2.2% stop converts S3's +₹5,610/trade (H40, no stop) into **−₹728**. A 10% disaster stop preserves +₹4,604 (82% of the edge). The −2.2% wall sits *inside the noise band of the signals* (S3 median 20-day MAE is 5.63%; 73–78% of cohort trades touch −2.2% within 20 sessions).
3. **The engine A/Bs already whispered it.** X1b (T1 removed): win rate collapses 34%→23% but PF rises 0.74→0.87 (15y) and 0.94→1.11 (wf3y), expectancy flips positive (+₹157/trade). X2a-narrow is the **first trading-positive configuration in project history**: PF **1.30 (15y) / 1.41 (wf3y)**, +₹471/₹631 per trade, max DD −0.9% to −1.5%.

**The second blocker is capital starvation, and it is solved by the same fix.** X2a earns +₹471/trade on ~10 trades/yr ≈ +1%/yr — positive expectancy, no capital throughput. Institutional arithmetic: CAGR = per-trade edge × risk-unit turnover × deployment. A 20-session hold raises per-trade edge ~6× (S3: +₹621 → +₹1,885 at stop=10%) *and* raises slot utilization per unit of risk budget. S3+S4 fire ~85×/yr unthinned; 4 slots at 20-session holds support ~45 fills/yr; at full-slot deployment and measured expectancy that is ~₹85–99k/yr on ₹5L ≈ **+17–20%/yr gross of haircuts**. Mid-teens is the honest target; the 3-year walk-forward (which includes weak 2025) is the arbiter.

This CR proposes **X3**: Setup 3 + Setup 4 only, T1 removed, stall/time exits removed, 20-session horizon, disaster stops (8–10% gate), **full-slot deployment** (not risk-parity shrink), with pre-registered kill criteria.

---

## 2. The Load-Bearing Facts (Measured)

### 2.1 Policy frontier — enter T+1 open, disaster stop `s`, exit close of day H (net Rs/trade, ₹62.5k deployment)

Source: `experiments/cr003/exit_horizon_frontier.py` → `probe_output_exit_horizon.txt`, post-processing `cohort_signals_forward_returns.parquet` (engine-exact signal replay, 1-in-5 session thinning). Stop model: `mae_H > s → exit at −s` (optimistic fill-at-stop; the engine's gap-through semantics are more honest and are used in verification).

| Setup | H | stop −2.2% | stop −5% | stop −8% | stop −10% | stop −12% | no stop |
|---|---|---|---|---|---|---|---|
| **S3 RS Base** | 10 | +94 | +179 | +106 | +731 | +711 | +737 |
| **S3 RS Base** | 20 | +621 | +1,046 | +967 | **+1,885** | +2,090 | +2,191 |
| **S3 RS Base** | 40 | **−728** | +315 | +1,989 | **+4,604** | +5,376 | +5,610 |
| **S4 Anchor** | 20 | +442 | +513 | +1,712 | **+2,206** | +2,128 | +2,251 |
| **S4 Anchor** | 40 | +922 | +1,518 | +2,817 | **+3,206** | +3,612 | +4,216 |
| **S1 VCP** | 40 | +56 | +21 | +136 | +237 | +302 | +205 |
| **S5 Residual** | ≤20 | negative | negative | negative | negative | negative | negative |

Detail at S3, H=20, s=10%: win 61.2%, mean +₹1,885, median +₹1,281, P(stopped) 22.4%. S4, H=20, s=10%: win 62.5%, mean +₹2,206, P(stopped) 6.2%.

**Reading:** every profitable cell sits at wide stops and long horizons. The −2.2% column — today's engine — is the worst or near-worst cell for the setups with real edge.

### 2.2 Realized exit mix is a maximum-friction machine (Arm C, 15y, 267 trades)

| Exit | n | Total P&L | Avg/trade |
|---|---|---|---|
| TARGET_2_HIT (+2% guillotine survivors' runner) | 35 | +₹142,648 | **+₹4,076** |
| TIME_DAY5_HIT | 13 | +₹34,767 | +₹2,674 |
| STRUCTURAL_STOP_HIT (−2.2% wall) | 108 | **−₹186,926** | −₹1,731 |
| STALL_48H_HIT | 48 | −₹32,720 | −₹682 |
| TRAILING_STOP_HIT | 63 | −₹29,625 | −₹470 |

The exits that truncate at 5 sessions or at −2.2% are 82% of trades and all of the bleed. The rare escapes prove where the money lives.

### 2.3 Engine A/B ledger (all runs: `reports/cr003/`)

| Arm | Window | Trades | Win | PF | Exp/trade | CAGR post-tax | Max DD |
|---|---|---|---|---|---|---|---|
| A baseline (CR-001) | bt15y | 660 | 32.4% | 0.60 | −₹407 | +4.03% | −5.81% |
| A baseline | wf3y | 211 | 34.1% | 0.62 | −₹346 | +1.73% | −6.93% |
| B CR-003 default | bt15y | 500 | 31.6% | 0.66 | −₹358 | +4.95% | −3.40% |
| B CR-003 default | wf3y | 184 | 36.4% | 0.82 | −₹164 | +4.56% | −4.78% |
| **C = B1 (S2 pruned)** | **bt15y** | 267 | 34.5% | 0.74 | −₹269 | **+6.08%** | −3.23% |
| **C** | **wf3y** | 100 | 39.0% | 0.94 | −₹55 | **+6.38%** | −2.25% |
| D = E1 (gate demoted) | bt15y | 908 | 31.3% | 0.53 | −₹500 | +0.95% | −8.32% |
| D | wf3y | 241 | 32.0% | 0.57 | −₹402 | **−0.23%** | −8.00% |
| X1a hold=20 (T1 on) | wf3y | 81 | 46.9% | 1.01 | +₹5 | +6.18% | −1.97% |
| X1b no-T1 | wf3y | 77 | 27.3% | 1.11 | **+₹157** | +6.38% | −2.48% |
| **X2a narrow** | **bt15y** | 36 | 30.6% | **1.30** | **+₹471** | **+6.97%** | **−0.88%** |
| **X2a narrow** | **wf3y** | 28 | 32.1% | **1.41** | **+₹631** | **+7.82%** | **−1.47%** |
| X2b wide parity | wf3y | 31 | 29.0% | 1.13 | +₹222 | +7.23% | −1.59% |

**Reading:** expectancy is monotonically improved by removing the guillotine (X1b) and by refusing the worst candidates (X2a). None of these arms touch the horizon/stop combination — X3 is the first arm that does.

### 2.4 CR-003 levers: post-mortem verdicts

| CR-003 Lever | Verdict | Measured evidence |
|---|---|---|
| L1 Prune Setup 2 | **CONFIRMED (kept)** | −₹62.6k→−₹35.2k book; Arm C best full-catalog arm (PF 0.74/0.94) |
| L2 Breadth offense gate | **DOWNGRADED to sizing knob** | Realized book: ≥77.3% cohort ex-S2 +₹8.5k vs −₹37.8k below — but the cohort frontier shows S3 × breadth<77.3 mean +₹2,504 vs +₹720 above (thinned n=17/32, noisy, direction contradicts the gate). A hard gate starved 2024 to 1 trade. Use `breadth_sizing_multiplier` only if it survives A/B. |
| L3 Setup 5 risk parity | **FALSIFIED** | Wide-stop cohort E[net] −0.19%/trade (probe L3); frontier: S5 negative at every swing horizon ≤20d; 2025–26 cohorts deeply negative. Raw 71.4% win rate was an artifact of exits, not edge. **Quarantine S5.** |
| L4 Setup 4 T1=+3.5% | **FALSIFIED (direction reversed)** | T1=3.5% −₹171/trade vs T1=2% −₹216 — both negative. X1b shows the fix is removing T1 entirely (−₹157 → +₹157 in engine). The guillotine is the bug, not its height. |
| L5 S_runner gate demotion | **FALSIFIED in combination** | Probe L5 micro-evidence mixed (gate keeps better cohorts in-book), but Arm D (gate off) is the worst arm in the ledger: PF 0.53–0.57, CAGR −0.23% wf3y. Count-without-expectancy is death. **Keep the gate enforced.** |

---

## 3. The X3 Arm: Specification

One idea: **hold where the edge lives, size like an institution, stop only at disaster.**

| Knob | X3 value | Mechanism status |
|---|---|---|
| Catalog | `enable_setup1=false`, `enable_setup3=true`, `enable_setup4=true`, `enable_setup5=false` | exists (Phase A.1) |
| T1 guillotine | `risk.t1_enabled=false` | exists (X1b, byte-tested) |
| Stall/Day-5 exits | `risk.time_stop_enabled=false` | exists (X1b) |
| Horizon | `risk.max_holding_days=20` | exists (X1a used 20) |
| Disaster stop gate | `catalog.setup3_max_stop_pct=0.10` (sub-arm 0.08) + stage4 admits to setup gate | exists via X2 wide-stop admission path |
| **Full-size wide stops** | **NEW `risk.wide_stop_full_size: bool = false`** | **the only new engine logic** |
| T2 override (sub-arm) | **NEW `catalog.setup3_t2_target / setup4_t2_target: float \| None = None`**; X3b sets 0.20 (targetless) | small catalog plumbing |
| Deployment | Full slot ₹1.25L per fill (NOT parity-shrunk) | NEW flag above |

### 3.1 Portfolio heat & kill-switch interaction (must be argued, not assumed)

Full slot × 10% stop = up to ₹12.5k heat/trade = 2.5% of ₹5L portfolio; 4 concurrent = 10% theoretical, above the 7.5% kill switch. Mitigations, in preference order:
1. **Sub-arm X3c uses an 8% gate** (heat 1.6%/trade; frontier cost vs 10%: S3 H20 ₹967 vs ₹1,885 — acceptable).
2. The 7.5% kill switch remains armed — it is the backstop, and its realized firing rate is itself a measured X3 output.
3. `breadth_sizing_multiplier` (e.g. 0.75 below 77.3% breadth) as a later refinement, only if the A/B supports it.

### 3.2 Why >15% is plausible and falsifiable

Expected-value chain (S3+S4, stop=10%, H=20, full slot): ~85 fires/yr → slot-capped ~45 fills/yr × mean +₹1,885–2,206 ≈ ₹85–99k/yr ≈ **+17–20%/yr** before haircuts for (a) signal clustering (4 slots busy → missed fires), (b) gap-through-stop slippage beyond −10%, (c) 2025-style regime decay. Haircuts push the estimate to the mid-teens; the wf3y walk-forward decides. If it lands below +12%, the 15% mandate is empirically dead for daily-resolution NSE cash equities and the X2a "liquid-plus" profile (7.8% CAGR at −1.5% DD) becomes the shippable product.

---

## 4. Implementation Checklist

Every item is flag-gated; X3 arms are constructed purely from `SystemConfig` overrides.

### Phase A: Configuration (`src/nse_cash/core/config.py`, `config/config.yaml`)
- [ ] **A.1** `RiskConfig.wide_stop_full_size: bool = False` — when True with a wide setup gate: Stage 4 admits up to the setup's `max_stop_pct`, and the engine sizes **full slot** (no parity shrink). Rupee heat per trade = `stop_pct × slot_capital`, reported in the tear sheet as `portfolio_heat_pct`.
- [ ] **A.2** `CatalogConfig.setup3_t2_target: float | None = None` and `setup4_t2_target: float | None = None` — None keeps engine defaults; a value overrides the T2 target for that setup (X3b uses 0.20).
- [ ] **A.3** YAML comments documenting the X3 arm recipe (copy of §3 table).

### Phase B: Engine & Gate (`src/nse_cash/funnel/stage4_gate.py`, `backtest/engine.py`, `backtest/fill_model.py`)
- [ ] **B.1** Stage 4: when `wide_stop_full_size` is True, admit candidates up to the setup gate (extend the existing `risk_parity_stops` admission branch; no parity factor applied).
- [ ] **B.2** Engine sizing: `shares = floor(slot_capital / execution_price)` when `wide_stop_full_size`, else the existing parity/legacy branches.
- [ ] **B.3** Fill model: gap-through-stop already fills at open (GTT semantics) — **verify with a unit test that a −18% overnight gap on a 10%-stop position books ≈ −18%, not −10%**. This is the honest version of the frontier's optimistic fill-at-stop model.
- [ ] **B.4** T2 override plumbing in predicate targets (setup3/setup4).

### Phase C: Verification & Run Book (`experiments/cr003/run_x3_ab.py`)
- [ ] **C.1** Arms (each = one `SystemConfig`): `X3a` (S3+S4, gate 10%, T2 default), `X3b` (X3a + T2=0.20 targetless), `X3c` (gate 8%, T2 default), control `X2a` rerun for comparability. Windows: bt15y + wf3y, same harness as `run_arms_15y_3y.py`.
- [ ] **C.2** Tear sheets into `reports/cr003/x3_exit_horizon/` with per-setup, per-year, heat, and kill-switch firing counts.

### Phase D: Data-Integrity Audit (runs BEFORE trusting any X3 result)
- [ ] **D.1 Survivorship:** the feature frame holds 1,055 symbols in 2023–26. Cross-check the store's PIT universe rebuild against known 2023–26 NSE delistings; confirm cohort stats are not long-biased by missing dead symbols. If PIT healing is absent for the cohort script, rerun with delisted names restored before Phase C verdicts.
- [ ] **D.2 Power:** rerun the cohort collection with all 5 sampling offsets (unthinned cohorts) so the frontier cells carry honest n and per-year slices — especially 2025 (the year every arm bled).
- [ ] **D.3 Slippage sensitivity:** recompute frontier cells at stop-fill = open-if-gapped (worst case) vs stop price (best case); require X3's kill thresholds to hold under the worst case.

### Phase E: Test Regression
- [ ] **E.1** `pytest tests/unit/` green, including new tests: wide-stop-full-size admission, full-slot sizing, gapped-stop booking, T2 override.

---

## 5. Hypotheses, Expected Outputs & Kill Thresholds

| Sub-arm | Hypothesis | Expected wf3y output | Kill / revert threshold |
|---|---|---|---|
| **X3a** | 20d horizon + 10% disaster stop + full slot monetizes the S3/S4 cohort edge end-to-end | CAGR ≥ +15%, PF ≥ 1.30, win ≥ 50%, trades ≥ 60, DD ≤ 6.5% | CAGR < +12% OR PF < 1.15 OR DD > 7.5% → **X3 falsified** |
| **X3b** | Removing the +6% T2 cap releases the right tail (mfe_med H40 = +19.3% on S3) | Δ CAGR vs X3a ≥ +2 pp | Δ CAGR < +1 pp → keep T2 default |
| **X3c** | 8% gate buys back most of the edge at 60% of the heat | CAGR within 2 pp of X3a with fewer kill-switch firings | Edge collapses vs X3a → prefer X3a with heat mitigations |

**Program-level acceptance (replaces CR-003 §6.1 for this rebuild):**
1. Post-tax CAGR (wf3y, worst-case slippage) ≥ **+12%** — continue; ≥ **+15%** — mandate met.
2. PF ≥ 1.30; win rate ≥ 50%; max DD ≤ 6.5%; kill-switch firings ≤ 2 in wf3y.
3. Positive trading P&L in every calendar year of wf3y, or a documented regime explanation for any losing year.

**The abandonment rule stands (CR-003 §6.2), upgraded:** if X3a–X3c all fail their thresholds, the 15% mandate is falsified **for daily-resolution fixed-target cash swing trading on NSE**. Ship X2a as a "liquid-plus" product (measured +7.8% CAGR, −1.5% DD — beats LIQUIDBEES by ~1.5 pp with real risk accounting), or return to 100% liquid funds. Do not invent a CR-005 catalog tweak; the probe funnel has now falsified every stock-level selection feature and every narrow-exit geometry in this book.

**Benchmark honesty:** the true hurdle is NIFTY 500 buy-and-hold on the same windows, not LIQUIDBEES. The tear sheet must print the B&H delta (compute_metrics already computes it — require it in every verdict).

---

## 6. Results Log

| Run ID | Arm | Trades | Win | PF | Exp/trade | CAGR post-tax | Max DD | Verdict |
|---|---|---|---|---|---|---|---|---|
| CTRL-001 | CR-001 baseline (wf3y) | 216 | 32.9% | 0.55 | — | +1.93% | −6.77% | FAIL |
| ARM-B | S2 disabled (wf3y) | 106 | 32.1% | — | — | +4.93% | −3.12% | recovery |
| C-15y | S2 pruned (bt15y) | 267 | 34.5% | 0.74 | −₹269 | +6.08% | −3.23% | best full catalog |
| C-wf3y | S2 pruned (wf3y) | 100 | 39.0% | 0.94 | −₹55 | +6.38% | −2.25% | best full catalog |
| D-15y/wf3y | gate demoted | 908/241 | 31–32% | 0.53–0.57 | −₹400/−₹500 | +0.95%/−0.23% | −8.0/−8.3% | FALSIFIED (keep gate) |
| X1a | hold=20 (wf3y) | 81 | 46.9% | 1.01 | +₹5 | +6.18% | −1.97% | neutral-positive |
| X1b | no T1 (wf3y) | 77 | 27.3% | 1.11 | +₹157 | +6.38% | −2.48% | geometry works, starved |
| X2a | narrow (bt15y/wf3y) | 36/28 | 31–33% | 1.30/1.41 | +₹471/+₹631 | +6.97%/+7.82% | −0.9/−1.5% | first trading-positive arm |
| X3a/b/c | — | — | — | — | — | — | — | **PENDING** |

---

## Appendix A: Dead vs Alive Mechanisms (post-CR-003 measurement)

| Component | Status | Empirical rationale |
|---|---|---|
| Setup 2 rubber-band | DEAD (kept dead) | L1 confirmed: 68% of historical losses |
| Setup 5 residual momentum | **DEAD (newly falsified)** | Probe L3 + frontier: negative expectancy at every horizon × stop combination, incl. risk-parity sizing |
| S_runner gate demotion | **DEAD (gate stays enforced)** | Arm D: PF 0.53–0.57, CAGR −0.23% wf3y |
| Setup 4 T1=+3.5% | **DEAD** | Probe L4: −₹171/trade; X1b: removing T1 flips expectancy positive |
| −2.2% structural wall | **DEAD (replaced by 8–10% disaster stop)** | Frontier: converts +₹5.6k edge into −₹0.7k on S3 |
| 48h-stall + Day-5 exits | **DEAD for X3 (removed)** | Exit mix: stall exits −₹682/trade avg; horizon truncation forfeits the H20–H40 edge |
| +2%/+6% T1/T2 targets | **DEAD (T1 off; T2 override A/B)** | X1b +₹157/trade; mfe_med ≫ targets |
| Setup 3 + Setup 4 at 20d | **ALIVE (core)** | Cohort: +3.98%/+7.82% median @ H20–40, 63–81% win; X2a PF 1.30–1.41 |
| Breadth gate | DOWNGRADED to sizing knob | L2 vs frontier contradiction; starving trades costs more than the filter earns |
| Risk-parity sizing | SUPERSEDED by full-size + wide gate for X3 | Parity shrinks the very trades that carry the edge; heat is managed by gate width instead |
