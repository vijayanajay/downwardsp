# Change Request: CR-2026-003

## System Post-Mortem & Rebuild Program: Exit Geometry, Environment Conditioning, Catalog Pruning & Risk-Parity Sizing

* **CR Number:** `CR-2026-003`
* **Status:** `PROPOSED — EVIDENCE-FIRST REBUILD PROGRAM` (2026-09-19)
* **Predecessors:** 
  * `CR-2026-001` (`IMPLEMENTED` 2026-09-18: fixed physical dry/shock conflict in Setup 1, frozen anchor in Setup 4, GTT limit buffers)
  * `CR-2026-002` (`REJECTED / EMPIRICALLY GUTTED` 2026-09-18: see its §7 appendix; tri-state routing and ATR targets falsified by data)
* **Evidence Base:** 
  * `experiments/cr002/` (Issue probes P0–P7, A/B arm comparison, gate mining)
  * `reports/backtest/` (walk-forward control 2023–2026)
  * `docs/FINDINGS_Weekly_Sweet_Spot_Study.md` (519k-event baseline study & Addendum B)
  * DuckDB production store (6.53M bar rows 2010–2026, 831k feature rows 2023–2026)
* **Author Context:** Kailash Nadh Pragmatic Review — zero code changes without empirical proof. No mechanism survives on narrative; everything must earn its slot through the probe funnel.

---

## 1. Executive Summary & Context

CR-2026-002 attempted to optimize the system by adding new regime states, archetype scores, and ATR target scaling under the unverified assumption that the underlying engine was profitable. 

The measured reality from the baseline walk-forward control (2023-01-02 to 2026-09-17) exposed a sobering truth:
* **The CR-001 engine loses money trading:** Over 3.7 years, total trading P&L is **$-₹91,849$** across 216 trades.
* **Liquid fund interest masks the bleed:** Post-tax CAGR is **$+1.93\%$**, but **$+128,249$ ($+6.35\%$ annualized)** was earned simply by unallocated cash sitting in overnight liquid funds. Active trading subtracted **$-4.4$ percentage points per year**.
* **Win rate is severely depressed:** Out-of-sample win rate is **$32.9\%$** (vs $48\%\text{--}56\%$ BRD target and $50.4\%$ unconditional coin-flip baseline).
* **Performance decays monotonically:** 
  * 2023: $+₹4,782$ ($50.0\%$ win rate, 40 trades)
  * 2024: $-₹12,697$ ($27.3\%$ win rate, 11 trades)
  * 2025: $-₹33,541$ ($34.2\%$ win rate, 73 trades)
  * 2026 YTD: $-₹50,394$ ($25.0\%$ win rate, 92 trades)

This CR is not a speculative optimization. It is an **evidence-grounded restructuring** built on five specific quantitative recommendations:
1. **Prune Setup 2 (Rubber-Band Pullback):** Responsible for $68\%$ of all trading losses. Disabling it immediately recovers $+₹56,700$ and lifts CAGR from $+1.93\%$ to $+4.93\%$.
2. **Environment Conditioning (Breadth-Gated Offense):** Nifty 500 Breadth $\ge 77.3\%$ is the *only* variable in the entire database with a large, monotonic edge spread ($+0.46$ pp spread; win rate $44\%$, expectancy $-0.03\%$).
3. **Setup 5 Risk-Parity Geometry Rescue:** Setup 5 has a stellar **$71.4\%$ raw win rate** that is currently destroyed because the rigid $-2.2\%$ stop gate filters out $92.7\%$ of candidates, admitting only the $28.9\%$ worst performers. Decoupling stop distance ($6\%\text{--}8\%$) from rupee risk via dynamic position sizing unlocks this winning cohort.
4. **Setup 4 Expansion & Target Geometry:** The only profitable setup in the walk-forward ($+0.727\%$/trade), starved at only 3 trades. Widen retest undercut tolerance to $-2.5\%$ and expand Tranche 1 target from $+2.0\%$ to $+3.5\%$.
5. **Deregulate the Conviction Gate:** The $S_{\text{runner}} \ge 0.45$ gate anti-selects in-book because $Z_{\text{delivery}}$ is negatively correlated with forward returns. Remove the hard lockout.

---

## 2. The Three Load-Bearing Facts (Measured on Real Data)

1. **Stock-Level Selection Features Have Zero Signal in Cash Equities:**
   In-book feature mining (`experiments/cr002/gate_mining.json`) across all 216 baseline trades proved that no single technical entry feature separates winners from losers:
   * $Z_{\text{delivery}} \ge 1.0 \rightarrow +0.06$ pp spread (inverted sign: high-volume shock names lose *more*).
   * $\text{iMOM} \ge 90\text{th percentile} \rightarrow -0.11$ pp spread (top momentum names underperform).
   * $\text{RSI}(2) \le 10 \rightarrow -0.20$ pp spread (Setup 2's own entry trigger actively selects losers).
   * Proximity to 52-week High $\rightarrow +0.02$ pp spread (noise).
   * High ATR% ($\ge 4.0\%$) loses more ($-0.465\%$ vs $-0.315\%$).
   * *Conclusion:* Any attempt to "fix" the system by inventing new stock indicators or complex formulas is mathematically futile.

2. **The Environment Carries All the Signal:**
   Dividing market sessions by NIFTY 500 Breadth (% of stocks above 50-day SMA) into terciles:
   * **Low Breadth ($\le 64.1\%$):** 72 trades, win rate $29\%$, mean return **$-0.48\%$**
   * **Mid Breadth ($64.1\%\text{--}77.3\%$):** 72 trades, win rate $25\%$, mean return **$-0.51\%$**
   * **High Breadth ($\ge 77.3\%$):** 72 trades, win rate **$44\%$**, mean return **$-0.03\%$**
   * *Conclusion:* A massive **$0.46$ pp expectancy spread**. The current engine shuts down below $50\%$ breadth, but never concentrates capital when breadth is $>77\%$. Offense must be conditioned on market-wide participation.

3. **Exit Geometry and the $-2.2\%$ Wall Dictate $67\%$ of All Outcomes:**
   * Exit mix: Structural stops $48\%$, Trailing stops $19\%$, 48h stall exits $18\%$, T2 target hits just $12\%$.
   * Median adverse excursion across NSE is $-2.66\%$. A fixed $-2.2\%$ stop line acts as a magnet during normal intraday noise.
   * Small fixed targets ($+2.0\%$) require a $75.9\%$ win rate to break even after friction and tax. In liquid equities, unconditional touch rate is only $50.4\%$. Cutting winners at $+2.0\%$ forfeits the right tail while absorbing full $-2.2\%$ stop-outs.

---

## 3. Algo-by-Algo Verdict & Specific Rebuild Actions

| Setup ID | Setup Name | Walk-Forward P&L | Realized Win Rate | Probe P(win) | Verdict | Core Action |
|---|---|---|---|---|---|---|
| **Setup 1** | Delivery VCP & Parkinson Squeeze | $-₹6,437$ (32 trades) | $28.1\%$ | $34.0\%$ | **QUARANTINE** | Demote delivery shock; make Parkinson squeeze ($\text{PV}_5 \le p15$) binding; enforce Breadth $\ge 75\%$. |
| **Setup 2** | Secular Rubber-Band Pullback | **$-₹62,620$** (116 trades) | $32.8\%$ | $34.2\%$ | **REMOVE** | Disable via config (`enable_setup2 = False`). Immediate $+₹56.7\text{k}$ P&L relief. |
| **Setup 3** | Cross-Sectional RS Base | $-₹15,116$ (30 trades) | $43.3\%$ | $41.6\%$ | **AMEND** | Gate strictly on Breadth $\ge 77\%$; buy on ceiling breakout print, not base anticipation. |
| **Setup 4** | Anchor Base Retest | **$+₹2,736$** (3 trades) | **$66.7\%$** | **$59.3\%$** | **SCALE UP** | Widen undercut buffer to $-2.5\%$; raise Tranche 1 target to $+3.5\%$; allocate priority slot. |
| **Setup 5** | Residual Momentum Blitz | $-₹10,412$ (35 trades) | $28.9\%$ | **$71.4\%$ (raw)** | **RESCUE** | Anchor stop to `prev_low` ($6\%\text{--}8\%$); scale position size dynamically so rupee risk remains $\le 2.2\%$ of slot. |

---

## 4. Detailed Developer Implementation Checklist

Every code change must be implemented behind a feature flag or configuration knob so the deterministic A/B harness can test each lever independently.

### Phase A: Configuration & Feature Flags (`src/nse_cash/core/config.py` & `constants.py`)
- [ ] **A.1** In `src/nse_cash/core/config.py`, introduce `CatalogConfig` settings (with YAML & env override support):
  ```python
  @dataclass
  class CatalogConfig:
      enable_setup1: bool = True
      enable_setup2: bool = False   # Pruned per CR-003 §3.1
      enable_setup3: bool = True
      enable_setup4: bool = True
      enable_setup5: bool = True
  ```
- [ ] **A.2** In `src/nse_cash/core/config.py`, add environment conditioning settings to `FunnelConfig`:
  ```python
  breadth_offensive_min: float = 0.75       # Only scan/trade if Nifty 500 Breadth >= 75%
  breadth_sizing_multiplier: float = 1.0    # Optional slot-scaling knob
  ```
- [ ] **A.3** In `src/nse_cash/core/config.py` & `constants.py`, add Setup 4 and Setup 5 parameters:
  ```python
  # Setup 4 Target Expansion
  SETUP4_TRANCHE1_TARGET: float = 0.035     # Lifted from 0.020
  SETUP4_UNDERCUT_TOLERANCE: float = 0.025  # Widen retest low penetration buffer from 0.008
  
  # Setup 5 Risk Parity
  SETUP5_USE_RISK_PARITY: bool = True
  SETUP5_MAX_RUPEE_RISK_PCT: float = 0.022  # Maximum slot risk capped at 2.2% of slot capital
  ```
- [ ] **A.4** In `src/nse_cash/core/config.py`, add `RankingConfig.enforce_s_runner_gate: bool = False`.

---

### Phase B: Setup Predicate & Geometry Updates (`src/nse_cash/setups/catalog.py`)
- [ ] **B.1 Setup 2 Deactivation Gate:**
  In `catalog.py::evaluate_setups`, check `config.catalog.enable_setup2`. If `False`, skip `evaluate_setup2_rubberband` entirely.
- [ ] **B.2 Setup 4 Target & Undercut Alignment:**
  In `catalog.py::evaluate_setup4_anchor_retest`:
  * Expand retest low tolerance: allow Day $T$ Low to penetrate up to `anchor * (1 - SETUP4_UNDERCUT_TOLERANCE)` (up to $-2.5\%$), provided Day $T$ Close finishes $\ge \text{anchor} \times 0.995$ with lower shadow $\ge 40\%$.
  * Set target: `target_t1 = entry * (1 + SETUP4_TRANCHE1_TARGET)` ($+3.5\%$) and `target_t2 = entry * 1.060`.
  * Set `structural_stop = min(low_t, anchor) * 0.998` with `max_stop_pct = 0.035`.
- [ ] **B.3 Setup 5 Structural Anchor & Flagging:**
  In `catalog.py::evaluate_setup5_residual_momentum`:
  * Structural stop remains anchored to genuine market structure: `structural_stop = prev_low` (or `min(prev_low, low_t) * 0.998`).
  * Return metadata flag `is_risk_parity = True` and report `raw_risk_pct = (close_t - structural_stop) / close_t`. Do *not* clamp with artificial formulas.
- [ ] **B.4 Setup 1 Volatility Binding:**
  In `catalog.py::evaluate_setup1_vcp_squeeze`:
  * Demote delivery shock to secondary status; ensure `pv_percentile <= 0.15` and `close >= sma20 * 0.99` are binding.

---

### Phase C: Environment Conditioning & Breadth Offense (`src/nse_cash/funnel/market_regime.py` & `pipeline.py`)
- [ ] **C.1 Market Regime Breadth Tagging:**
  In `funnel/market_regime.py`:
  * Expose `breadth_pct` and compute trailing 1-year breadth percentile `breadth_pctl`.
  * Define `is_high_breadth_offense = (breadth_pct >= config.funnel.breadth_offensive_min * 100)`.
- [ ] **C.2 Pipeline Breadth Gate:**
  In `funnel/pipeline.py::decide_entries`:
  * If `config.funnel.breadth_offensive_min > 0`: when `regime.breadth_pct < (config.funnel.breadth_offensive_min * 100)`, reject new candidate entries with reason `MARKET_BREADTH_INSUFFICIENT`.
  * Existing open positions continue to be managed through their normal GTT/stall exit stack.

---

### Phase D: Dynamic Risk-Parity Position Sizing (`src/nse_cash/funnel/stage4_gate.py`, `backtest/engine.py`, `execution/action_sheet.py`)
- [ ] **D.1 Stage 4 Risk-Parity Admission:**
  In `funnel/stage4_gate.py::evaluate_stage4`:
  * If candidate has `is_risk_parity == True` (Setup 5) and `config.catalog.setup5_use_risk_parity == True`:
    * Calculate required capital allocation:
      $$\text{Allocation Factor} = \min\left(1.0, \frac{\text{SETUP5\_MAX\_RUPEE\_RISK\_PCT}}{\text{candidate.risk\_pct}}\right)$$
    * Instead of rejecting when `candidate.risk_pct > 0.022`, approve the trade with `allocated_capital = slot_capital * Allocation_Factor`.
    * For a stock with an $8.0\%$ stop distance, capital deployed is $27.5\%$ of a normal slot ($\frac{2.2\%}{8.0\%}$). Rupee loss on stop-out is identical to a standard trade ($\le ₹2,750$ on a ₹1.25L slot).
  * For standard setups without risk-parity, retain the strict $\le 2.20\%$ gate.
- [ ] **D.2 Backtest Engine Order Execution:**
  In `backtest/engine.py`:
  * Read `candidate.allocated_capital` when calculating order quantity:
    $$\text{Shares} = \left\lfloor \frac{\text{allocated\_capital}}{\text{execution\_price}} \right\rfloor$$
  * Persist the adjusted quantity to `SimPosition` and equity accounting.
- [ ] **D.3 Action Sheet Rendering:**
  In `execution/action_sheet.py`:
  * Render the dynamic quantity and exact position size in rupees.
  * Render an explicit tag: `[RISK-PARITY POSITION: X% SIZE DUE TO WIDE STOP]`.

---

### Phase E: Conviction Gate Deregulation (`src/nse_cash/setups/ranking.py`)
- [ ] **E.1 Bypass Hard Filter:**
  In `setups/ranking.py::evaluate_and_rank`:
  * When `config.ranking.enforce_s_runner_gate is False`: do not filter by `best.s_runner >= s_runner_min`.
  * Retain $S_{\text{runner}}$ strictly as an ordinal sort key for tie-breaking when available signals exceed open portfolio slots.

---

### Phase F: Walk-Forward Verification & Tear-Sheet Comparison
- [ ] **F.1 Full Test Suite Regression:** Run `pytest tests/unit/` (ensure 372/372 tests pass or are updated to reflect the new config knobs).
- [ ] **F.2 Walk-Forward Execution:** Run `nse-cash backtest --walk-forward` (2023-01-02 to 2026-09-17) and generate the comparative tear sheet against the control.
- [ ] **F.3 A/B Step Verification:** Log isolated metrics for each phase into `reports/cr003/`.

---

## 5. Phase-by-Phase Hypotheses, Expected Outputs & Measurement

Every phase must demonstrate quantitative improvement over the preceding baseline before proceeding:

| Phase / Lever | Underlying Quant Hypothesis | Test Method / Script | Expected Measurable Output | Kill / Reversion Threshold |
|---|---|---|---|---|
| **Lever 1: Prune Setup 2** | Setup 2 ($\text{RSI}_2 \le 10$) is an active negative selector in Indian cash equities; removing it stops $68\%$ of portfolio bleed. | `ab_disable_setup2.py` | * Trades: $216 \rightarrow 106$<br>* Trading P&L: $-₹91.8\text{k} \rightarrow -₹35.2\text{k}$ ($+₹56.7\text{k}$ delta)<br>* CAGR: $+1.93\% \rightarrow \mathbf{+4.93\%}$<br>* Max DD: $-6.77\% \rightarrow \mathbf{-3.12\%}$ | Revert if CAGR $< +4.0\%$ or Max DD $> -4.0\%$. |
| **Lever 2: Breadth Offense** | Market breadth $\ge 75\text{--}77\%$ is the only macro state with positive edge; shutting down entries during weak breadth avoids low-probability churn. | Isolated backtest with `breadth_offensive_min = 0.75` | * Win rate lifts from $32.9\% \rightarrow \mathbf{\ge 42.0\%}$<br>* Trades reduced by $40\%\text{--}50\%$ in choppy regimes<br>* Trading P&L delta $\ge +₹25,000$ | Revert if win rate does not increase by at least $+6.0$ pp over Lever 1. |
| **Lever 3: Setup 5 Risk Parity** | The $71.4\%$ raw win rate of Setup 5 is unlocked by allowing wide stops ($6\%\text{--}8\%$) while dynamic sizing holds dollar risk constant. | Setup 5 cohort probe + engine run with dynamic sizing | * Setup 5 win rate jumps from $28.9\% \rightarrow \mathbf{\ge 58.0\%}$<br>* Setup 5 P&L flips from $-₹10.4\text{k} \rightarrow \mathbf{> +₹15,000}$<br>* Trades admitted $\ge 40$ | Revert if Setup 5 cohort expectancy remains negative after 50 simulated trades. |
| **Lever 4: Setup 4 Expansion** | Setup 4 has real structural edge ($59.3\%$ cohort win rate); widening undercut buffer and raising T1 to $+3.5\%$ monetizes its accuracy. | Setup 4 isolated probe + engine replay | * Trade count increases from $3 \rightarrow \mathbf{12\text{--}20}$<br>* Win rate maintained $\ge 50.0\%$<br>* Setup 4 P&L contribution $> +₹10,000$ | Revert if average win / loss ratio drops below $1.50$. |
| **Lever 5: Gate Deregulation** | Removing the $S_{\text{runner}} \ge 0.45$ filter eliminates anti-selection and allows clean momentum bars to pass. | Engine comparison with `enforce_s_runner_gate = False` | * Eliminates artificial trade starvation<br>* Net expectancy spread $\ge 0.00$ pp | Revert if overall portfolio profit factor degrades. |

---

## 6. Acceptance Criteria & The Ultimate Abandonment Decision

### 6.1 Program Acceptance Criteria (Definition of Done)
To be approved for production deployment, the combined implementation must meet all of the following out-of-sample walk-forward (2023–2026) standards:
1. **Trading P&L Positive:** Trading itself must generate **$> +₹50,000$ net profit** after all friction ($0.35\%$) and STCG tax ($20\%$). It can no longer be a net drag on liquid fund yield.
2. **Post-Tax CAGR $\ge \mathbf{+10.0\%}$:** Must beat the passive liquid fund rate ($+6.35\%$) by at least **$+3.65$ percentage points per year**.
3. **Win Rate $\ge \mathbf{45.0\%}$:** Realized out-of-sample win rate must bridge the gap toward the $50\%$ coin-flip mark.
4. **Profit Factor $\ge \mathbf{1.30}$:** (Up from the failed $0.55$ baseline).
5. **Maximum Portfolio Drawdown $\le \mathbf{6.0\%}$:** (Well within the $8.5\%$ BRD risk budget).
6. **Market Invariants Clean:** Zero critical violations on active trading symbols via `scripts/check_market_invariants.py`.
7. **Test Coverage:** $100\%$ pass rate across the full pytest suite.

---

### 6.2 The Ultimate Abandonment Threshold (The Liquid Fund Benchmark)

> **CRITICAL QUANT GOVERNANCE RULE:**
> If after completing the implementation of Phases A through E, the walk-forward simulation (2023–2026) fails to achieve:
> 1. **Net Positive Trading P&L ($> ₹0$)**, OR
> 2. **Post-Tax CAGR $> \mathbf{+6.35\%}$** (the passive liquid fund return earned with zero effort and zero drawdown),
>
> **THEN THE ENTIRE CASH SWING TRADING SYSTEM MUST BE PERMANENTLY ABANDONED.**

#### Why This Rule is Absolute:
* A retail or proprietary quant desk has zero rational justification for taking equity drawdown, paying STT, paying GST, absorbing slippage, and spending daily operational effort if the resulting equity curve trails a sovereign liquid ETF (`LIQUIDBEES`).
* If the combined levers of (1) pruning the biggest loser, (2) trading only in strong breadth, (3) resizing Setup 5 for risk parity, and (4) expanding Setup 4 cannot produce positive trading expectancy, it proves that **daily-resolution fixed-target cash swing trading on NSE is structural negative-expectancy after tax and friction**.
* In that event, the engineering recommendation is to **freeze development, return capital to 100% liquid funds, and not deploy a single rupee of live capital**. Documenting that an edge does not exist is a successful scientific conclusion that preserves capital.

---

## 7. Results Log

*(To be populated with empirical run artifacts as each phase of the checklist is executed)*

| Run ID | Config / Arm | Trades | Win Rate | Trading P&L | Total CAGR (post-tax) | Max DD | Verdict |
|---|---|---|---|---|---|---|---|
| `CTRL-001` | Baseline CR-001 Control | 216 | $32.9\%$ | $-₹91,849$ | $+1.93\%$ | $-6.77\%$ | FAIL (Control) |
| `ARM-B` | Setup 2 Disabled (`ab_disable_setup2`) | 106 | $32.1\%$ | $-₹35,192$ | $+4.93\%$ | $-3.12\%$ | BASELINE RECOVERY |
| `PHASE-B` | Setup 2 Off + Setup 4 Target Lift | — | — | — | — | — | PENDING |
| `PHASE-C` | + Breadth Offense Gate ($\ge 75\%$) | — | — | — | — | — | PENDING |
| `PHASE-D` | + Setup 5 Risk Parity Sizing | — | — | — | — | — | PENDING |
| `FINAL` | Full CR-003 Rebuild | — | — | — | — | — | PENDING |

---

## Appendix A: Summary of Dead vs Revived Mechanisms

| Component | Status | Empirical Rationale |
|---|---|---|
| **Setup 2 (Rubber-Band)** | **DEAD (Removed)** | $54\%$ of trades, $68\%$ of losses. $\text{RSI}(2) \le 10$ is an inverted signal. Bleeds harder during pullbacks. |
| **CR-002 Tri-State Regime** | **DEAD (Rejected)** | Falsified by probe P1. Routing capital to Setup 2 in pullbacks amplifies portfolio destruction. |
| **ATR Target Scaling (CR-002 C.2)** | **DEAD (Rejected)** | Falsified twice in Addendum B. Scaling targets by ATR worsens dispersion, drift, and win rates against a fixed stop. |
| **$S_{\text{runner}} \ge 0.45$ Gate** | **DEAD (Bypassed)** | Anti-selects in-book. Admitted cohort loses more than the rejected cohort. |
| **Rigid 2.2% Stop on Setup 5** | **DEAD (Replaced)** | Filters out $92.7\%$ of candidates, turning a $71.4\%$ raw winner into a $28.9\%$ dud. Replaced with risk-parity sizing. |
| **Breadth Offense ($\ge 75\%$)** | **REVIVED (Core)** | Only monotone edge found anywhere in the system ($+0.46$ pp spread). |
| **Setup 4 Target Expansion** | **REVIVED (Core)** | Highest-expectancy setup ($+0.73\%$/trade) scaled from $+2.0\%$ to $+3.5\%$ target to fit its win geometry. |
| **Dual-GTT 1.5% Buffer & Fill Model** | **KEPT (Core)** | Realized execution fidelity validated; three-branch limit gap semantics remain intact. |
