# Change Request: CR-2026-002
## Core Trading Engine & Microstructure Optimization: Dual-Regime Routing, Archetype Ranking, ATR-Scaled Dynamic Targets & Stop Gate Rationalization

* **CR Number:** `CR-2026-002`
* **Status:** `PROPOSED — UNDER EMPIRICAL REVIEW` (2026-09-18; architectural design & hypothesis framing; **empirical review attached — see §7. Several Issues measured FALSE or harmful as specified; Phase A–D items may not proceed to implementation without passing their probe per §4.**)
* **Successor:** `CR-2026-003` (post-mortem & rebuild program) — takes forward the §8 revised program; this CR's Phases A–D are **not** to be implemented as written.
* **Created Date:** 2026-09-18
* **Author / Reviewer:** Kailash Nadh Pragmatic Review
* **Target Version:** Post-Phase 7 / Phase 8 Optimization & Production Alpha Release
* **Affected Components:**
  * `src/nse_cash/funnel/market_regime.py` (Stage 1 macro regime engine & dual-state regime tagging)
  * `src/nse_cash/funnel/pipeline.py` (Daily decision funnel: regime-aware setup dispatching)
  * `src/nse_cash/setups/catalog.py` (Setup predicates: Setup 5 stop anchor, Setup 3 envelope, Setup 4 buffer)
  * `src/nse_cash/setups/ranking.py` (Archetype ranking: decoupling $S_{\text{runner}}$, $S_{\text{reversion}}$, $S_{\text{retest}}$)
  * `src/nse_cash/core/constants.py`, `core/config.py`, `core/types.py` (New archetype weights, ATR scaling thresholds)
  * `src/nse_cash/backtest/engine.py`, `src/nse_cash/backtest/fill_model.py` (Dynamic ATR target accounting)
  * `tests/unit/test_regime.py`, `tests/unit/test_setups.py`, `tests/unit/test_ranking.py`, `tests/unit/test_engine.py`

---

## 1. Executive Summary & Context

Following the implementation of `CR-2026-001` (which corrected the physical co-fire impossibility in Setup 1 and the inverted 90-day low anchor in Setup 4), the 5-setup library now functions with mathematical consistency and zero test regressions (219/219 green; suite has since grown — 372 passing as of 2026-09-18). 

However, empirical evaluation of the full system and the findings of the 519,785-event NSE sweet-spot study reveal that while the target win rate of **48% to 56%** is mathematically sound and consistent with liquid Indian equity microstructure, **annualized portfolio returns and trade expectancy are severely suppressed by four structural bottlenecks**:

1. **The Binary Macro Regime Choke:** Funnel Stage 1 flips to `DEFENSIVE_CASH` whenever NIFTY 50 $\le \text{EMA}_{20}$ OR NIFTY 500 Breadth $\le 50.0\%$. While protective for momentum breakouts, it completely disables the system during market pullbacks—the exact periods where **Setup 2 (Mean Reversion)** generates its highest-expectancy V-bottom bounces.
2. **Archetype Ranking Distortion:** Scoring all candidates with a single $S_{\text{runner}}$ formula (which allocates 35% weight to delivery volume shock $Z_{\text{delivery}}$) systematically starves mean-reversion (Setup 2) and base-retest (Setup 4) setups, which structurally require *low/dry* volume on Day $T$.
3. **Setup 5 Structural Stop Gate Rejection:** Requiring a delivery volume shock on Day $T$ creates large green expansion candles. Measuring the structural stop from `prev_low` causes the pre-entry risk gate (`risk <= 2.2%`) to disqualify the highest-conviction momentum leaders, admitting only sluggish, narrow-range bars.
4. **Fixed Percent Target Asymmetry:** Banking Tranche 1 at a static $+2.0\%$ forces the engine to exit high-volatility names (e.g., ATR $> 4.0\%$) prematurely, forfeiting fat right-tail gains while absorbing symmetric downside volatility.

This Change Request defines the mathematical hypothesis, architectural fixes, and a phased developer checklist to transition the system from a single-regime momentum screener into an institutional-grade, regime-adaptive cash swing engine.

---

## 2. Core Hypothesis Statement & Expected Empirical Payoff

### 2.1 The Institutional Hypothesis
> **Hypothesis:** By (1) decoupling macro regime gating to route capital to mean-reversion during index corrections while reserving momentum for trend expansions, (2) scaling Tranche-1 targets with per-stock realized volatility ($0.75 \times \text{ATR}_{14}$), (3) anchoring Setup 5 stops to intraday VWAP/support rather than prior-day low, and (4) scoring setups using archetype-native ranking metrics, the system will increase trade velocity from ~35 trades/year to **90–120 trades/year**, lift net expectancy per trade from **+0.18% to +0.55%–0.75%**, and raise post-tax CAGR from **+14%–16% to +26%–34%** without increasing maximum portfolio drawdown beyond the **8.5%** risk budget.

### 2.2 The Mathematical Payoff Mechanics

The net expected return per trade $\mathbb{E}[R]$ is governed by:

$$\mathbb{E}[R] = \left(p \times \bar{R}_{\text{win}}\right) - \left((1 - p) \times \bar{R}_{\text{loss}}\right) - C_{\text{friction}}$$

Under the current architecture:
* Hit rate: $p \approx 0.52$
* Average win: $\bar{R}_{\text{win}} \approx +2.10\%$ (clamped by $+2.0\%$ static T1 on $50\%$ position)
* Average loss: $\bar{R}_{\text{loss}} \approx -1.85\%$ (curbed by stall and $-2.2\%$ stop)
* Round-trip friction: $C_{\text{friction}} = 0.35\%$
* Expectancy: $\mathbb{E}[R] = (0.52 \times 2.10\%) - (0.48 \times 1.85\%) - 0.35\% = 1.092\% - 0.888\% - 0.35\% = \mathbf{-0.146\% \text{ to } +0.10\%}$ (insufficient to beat liquid fund yield after 20% STCG).

Under the proposed CR-002 architecture:
* Hit rate: $p \approx 0.53$ (stable, aligned with first-passage reality)
* Average win: $\bar{R}_{\text{win}} \approx \mathbf{+3.15\%}$ (unlocked by dynamic ATR targets and riding Tranche 2 on Setup 3/5 runners)
* Average loss: $\bar{R}_{\text{loss}} \approx \mathbf{-1.65\%}$ (tightened Setup 5 stop anchor and T+1 rapid mean-reversion stall exit)
* Friction: $C_{\text{friction}} = 0.35\%$
* Expectancy: $\mathbb{E}[R] = (0.53 \times 3.15\%) - (0.47 \times 1.65\%) - 0.35\% = 1.669\% - 0.775\% - 0.35\% = \mathbf{+0.544\% \text{ net per trade}}$
* Compounded across 100 trades/yr in 4 slots with unallocated cash earning 6.5%: **CAGR $+28.5\%$ post-tax**.

---

## 3. Detailed Issue Analysis & Architectural Decisions

### Issue 1: Binary Macro Regime Choke Disables Mean-Reversion at the Optimal Time

#### Root Cause
In `src/nse_cash/funnel/market_regime.py`:
```python
if nifty50_close <= nifty50_ema or breadth_pct <= 50.0:
    state = MarketRegimeState.DEFENSIVE_CASH
```
And in `src/nse_cash/funnel/pipeline.py`:
```python
if regime.state is MarketRegimeState.OFFENSIVE_LONG:
    # Evaluate setups...
```
When NIFTY 50 pulls back below its 20-day EMA or market breadth drops below 50%, the pipeline stops evaluating all setups. However, **Setup 2 (Rubber-Band Pullback)** requires stocks with $\text{RSI}(2) \le 10$ in secular uptrends. These extreme oversold conditions occur almost exclusively when the broader market undergoes a short-term index pullback. By locking the system in 100% cash, Setup 2 is starved of signals precisely when its win probability and risk-reward are highest.

#### Architectural Decision: Dual-Regime Setup Routing
Split `MarketRegimeState` into three operational regimes:
1. `BULL_TREND` (Nifty $>$ 20 EMA AND Breadth $> 50\%$): Full offensive mode for **Momentum Setups (1, 3, 4, 5)**. Setup 2 is paused (fewer dip-buying opportunities in runaway trends).
2. `PULLBACK_CORRECTION` (Nifty $\le$ 20 EMA OR Breadth $\le 50\%$, but Nifty $>$ 200 SMA): Defensive for breakouts, but **Offensive for Mean-Reversion (Setup 2)**. 2 of 4 portfolio slots are reserved exclusively for Setup 2 dip-buys.
3. `SECULAR_BEAR` (Nifty $\le$ 200 SMA AND Breadth $< 30\%$): True 100% cash lock. No setups permitted.

---

### Issue 2: Single $S_{\text{runner}}$ Metric Monopolizes Slots and Penalizes Non-Momentum Setups

#### Root Cause
In `src/nse_cash/setups/ranking.py`:
$$S_{\text{runner}} = 0.35 \times \frac{\max(0, Z_{\text{delivery}})}{3.0} + 0.35 \times \text{iMOM}_{\text{pct}} + 0.30 \times (1 - \text{PV}_{\text{pct}})$$
* Setup 2 strictly requires delivery volume $\le 1.15 \times \text{SMA}_{20}$ (subdued, $Z \le 0$). Its $Z$-term is always $0.0$. Furthermore, having closed down for 3 days, its short-term residual momentum is depressed. Its median score is $0.270$.
* Setup 5 requires $Z \ge 2.0$ and top 5th percentile $\text{iMOM}$. Its score routinely exceeds $0.75$.
* In any scan where Setup 5 and Setup 2 co-fire, Setup 5 takes the slot every time, destroying strategy orthogonality.

#### Architectural Decision: Archetype-Specific Scoring & Quota Allocation
Implement the deferred CR-001 Option B with concrete score formulas and quota routing:
1. **Momentum / Runner Score ($S_{\text{runner}}$)** for Setups 1, 3, 5:
   $$S_{\text{runner}} = 0.35 \times Z_{\text{norm}} + 0.35 \times \text{iMOM}_{\text{pct}} + 0.30 \times (1 - \text{PV}_{\text{pct}})$$
2. **Mean-Reversion Exhaustion Score ($S_{\text{reversion}}$)** for Setup 2:
   $$S_{\text{reversion}} = 0.50 \times \left(1.0 - \frac{\text{RSI}(2)}{10.0}\right) + 0.50 \times \text{RS}_{50\text{d\_pct}}$$
3. **Breakout Retest Score ($S_{\text{retest}}$)** for Setup 4:
   $$S_{\text{retest}} = 0.40 \times \text{Shadow}_{\text{pct}} + 0.30 \times (1 - \text{Volume\_Ratio}) + 0.30 \times \text{RS}_{50\text{d\_pct}}$$
4. **Slot Quota:** In `BULL_TREND`, momentum gets up to 4 slots. In `PULLBACK_CORRECTION`, Setup 2 gets up to 2 slots, with remaining capital held in liquid funds.

---

### Issue 3: Setup 5 Structural Stop Gate Rejection of Prime Momentum Expansion Bars

#### Root Cause
In `src/nse_cash/setups/catalog.py`:
```python
def evaluate_setup5_residual_momentum(row: _T) -> Optional[dict]:
    ...
    # Structural stop: PRIOR day low (Setup 5 spec)
    prev_low = _v(row, "prev_low")
    return {"structural_stop": prev_low, "max_stop_pct": 0.022, ...}
```
And Stage 4 gating enforces:
```python
risk = (entry_ref - structural_stop) / entry_ref
if risk > 0.022:
    return None  # Disqualified
```
A stock exhibiting high idiosyncratic alpha and a $2.0\times$ delivery shock on a green day typically experiences an intraday expansion of $+3.0\%$ to $+5.0\%$. Consequently, `prev_low` sits $3.5\%$ to $6.0\%$ below Day $T$ Close! The pre-entry risk gate mathematically rejects almost all high-conviction breakout bars, admitting only sluggish bars with narrow range—the exact opposite of explosive momentum.

#### Architectural Decision: Volatility-Anchored Structural Stop for Setup 5
Change Setup 5 structural stop from `prev_low` to:
$$\text{Structural Stop} = \max\left(\text{Low}_T, \text{Close}_T \times (1 - 0.021)\right)$$
* Rationale: Day $T$ Low represents the institutional baseline for the breakout impulse. If Day $T$ range exceeds $2.1\%$, cap the structural stop at exactly $2.1\%$ below entry reference to preserve capital while allowing the candidate to pass Stage 4.

---

### Issue 4: Fixed $+2.0\%$ Flat Targets Suppressing Realized Alpha on High-ATR Leaders

#### Root Cause
Currently, all setups (except Setup 2 at $+1.8\%$) hardcode Tranche 1 Target to $+2.00\%$. 
In our sweet-spot study addendum B, we established that stocks cluster into distinct volatility profiles (median ATR spans $1.9\%$ to $5.4\%$). 
* For a low-ATR stock (e.g., HINDUNILVR at $1.9\%$), $+2.0\%$ is a $>1.0\times \text{ATR}$ move that takes 4 days.
* For a high-ATR leader (e.g., BSE, SUZLON, ANGELONE at $4.2\%$), $+2.0\%$ is reached within the first 60 minutes of trading. Exiting $50\%$ of the position at $+2.0\%$ forfeits the standard intraday impulse while still risking a full $-2.2\%$ stop.

#### Architectural Decision: Volatility-Scaled Dynamic Targets
Scale Tranche 1 target dynamically by 14-day ATR:
$$\text{Tranche 1 Target} = \max\left(0.020, \min\left(0.035, 0.75 \times \text{ATR}_{14}\%\right)\right)$$
* Floor: $+2.0\%$ (ensures friction and taxes are comfortably cleared).
* Cap: $+3.5\%$ (preserves high first-passage win rate).
* Tranche 2 Runner Target: $\max\left(0.055, 1.75 \times \text{ATR}_{14}\%\right)$ or trailing prior-day low.

---

### Issue 5: Setup 3 & Setup 4 Filter Over-Restriction and Retest Stop Undershoot

#### Root Cause
* **Setup 3 (RS Base):** Requiring 5-day range $\le 3.0\%$ AND proximity to 52-week High $\le 1.5\%$ produced only 120 fires across 84,825 rows ($0.14\%$). Valid institutional consolidations in volatile bull markets routinely span $4.0\%$.
* **Setup 4 (Anchor Retest):** Setup 4 stops are placed at $\min(\text{breakout\_level}, \text{low}) \times 0.998$ with a $2.00\%$ max stop gate. Real institutional retests routinely undercut the breakout level by $0.5\%–1.0\%$ during opening stop-hunts before snapping back.

#### Architectural Decision: Proximity Normalization & Buffer Tuning
1. **Setup 3:** Relax base range ceiling from $\le 3.0\%$ to $\le 4.2\%$, and proximity to 52-week high from $\le 1.5\%$ to $\le 2.5\%$. Add a dry volume check during base consolidation ($\text{Volume}_T \le 0.85 \times \text{SMA}_{20}$).
2. **Setup 4:** Expand max stop gate from $2.00\%$ to $2.20\%$. Allow Day $T$ Low to penetrate up to $-1.2\%$ below `breakout_anchor_90`, provided Day $T$ Close finishes $\ge \text{breakout\_anchor\_90} \times 0.998$ with a $\ge 40\%$ lower shadow.

---

## 4. Planned Fixes & Empirical Validation Protocol

Before merging any code changes into the core engine, every fix will undergo empirical verification using the existing reproducible toolchain (`scripts/` and DuckDB store):

```
+─────────────────────────────────────────────────────────────────────────────+
|                         EMPIRICAL VALIDATION FUNNEL                         |
+─────────────────────────────────────────────────────────────────────────────+
| 1. Probe Feature Distributions (DuckDB 2.3M bar rows)                       |
|    - Fire-rate of dual-regime routing vs binary cash switch                 |
|    - Fire-rate of revised Setup 5 stop (how many prime signals admitted)    |
|    - ATR distribution across qualifying setups                             |
+─────────────────────────────────────────────────────────────────────────────+
                                       │
                                       ▼
+─────────────────────────────────────────────────────────────────────────────+
| 2. Out-of-Sample Walk-Forward Simulation (2023 - Present)                   |
|    - Compare metrics: baseline CR-001 vs candidate CR-002                   |
|    - Verification of BRD §10.1 targets: CAGR, Win Rate, Expectancy, DD      |
+─────────────────────────────────────────────────────────────────────────────+
                                       │
                                       ▼
+─────────────────────────────────────────────────────────────────────────────+
| 3. Market Invariants & Execution Audit                                      |
|    - Run `scripts/check_market_invariants.py`                                |
|    - Zero test regressions (219 existing tests pass or update)              |
+─────────────────────────────────────────────────────────────────────────────+
```

---

## 5. Developer Implementation Checklist

### Phase A: Market Regime & Dual-Routing Architecture (`funnel/market_regime.py` & `funnel/pipeline.py`)
- [ ] **A.1** In `core/types.py`: Add `BULL_TREND`, `PULLBACK_CORRECTION`, `SECULAR_BEAR` to `MarketRegimeState`.
  - **PROBE VERDICT (§7, P0):** the CR's three rules leave 85 sessions (9.3% of 2023+) UNASSIGNED (NIFTY ≤ 200-SMA but breadth ≥ 30%). Spec hole must be resolved before implementation.
- [ ] **A.2** In `funnel/market_regime.py`: Update `evaluate_market_regime` to output the three-tier regime based on NIFTY 50 vs 20-EMA, Breadth vs 50%, and NIFTY 50 vs 200-SMA.
  - **PROBE VERDICT (P0):** the binary choke itself is TRUE — 49.7% of walk-forward sessions evaluate zero setups (41.1% EMA-fail, 36.8% breadth-fail).
- [ ] **A.3** In `funnel/pipeline.py`: Modify `decide_entries` to allow Setup 2 evaluation when regime is `PULLBACK_CORRECTION`, while reserving Setups 1, 3, 4, 5 for `BULL_TREND`.
  - **PROBE VERDICT (P1): the motivating premise is FALSIFIED.** Setup 2 fires' forward edge in PULLBACK_CORRECTION (P(win) 33.1%, E[net] −0.093%) is *worse* than in BULL_TREND (34.7%, −0.089%). And A/B (§7.4): Setup 2 carried −₹62,620 of the book's −₹91,849 trading P&L; disabling it lifts CAGR +1.9%→+4.9% and halves max DD. Routing MORE capital to Setup 2 in pullbacks would amplify the system's largest loss source. BLOCKED pending a Setup-2 reselection that shows non-negative expectancy in its own probe.

### Phase B: Setup Predicates & Structural Stop Rationalization (`setups/catalog.py`)
- [ ] **B.1** In `setups/catalog.py` (`evaluate_setup5_residual_momentum`): Change structural stop from `prev_low` to `max(low_t, close_t * 0.979)`.
  - **PROBE VERDICT (P3): rejection mechanism TRUE (298/4,073 = 7.3% of fires pass the 2.2% gate today) — but the remedy is UNSOUND.** `max(low_T, close×0.979)` makes risk = min(range_T, 2.1%) ≤ gate BY CONSTRUCTION: the Stage-4 stop gate becomes non-binding for Setup 5. Simulated cohort with the proposed stop: E[net] −0.616%/trade vs −0.085% for today's admitted cohort (13× trade volume into a worse cohort). REJECTED as specified; re-derive via the stop-width sweep (§11.2 of the findings study) before any code change.
- [ ] **B.2** In `setups/catalog.py` (`evaluate_setup3_rs_base`): Expand base range from $0.030$ to $0.042$, 52-week high threshold from $0.985$ to $0.975$, and add volume consolidation check.
  - **PROBE VERDICT (P4): fire-rate claim TRUE (1,264 fires = 0.167% vs CR's 0.14%) — but relaxation degrades edge.** Relaxed cohort E[net] −0.416% vs current −0.385%; with the CR's dry-volume check it worsens to −0.497%. More trades, worse trades. REJECTED as specified.
- [ ] **B.3** In `setups/catalog.py` (`evaluate_setup4_anchor_retest`): Adjust stop gate to $0.022$ and relax low undershoot tolerance to $-1.2\%$ with close holding above anchor.
  - **PROBE VERDICT (P4): relaxation worsens expectancy** (proposed −0.830% vs current −0.751%; median retest undercut is −3.11%, far beyond the CR's 0.5–1.0% claim, so −1.2% catches neither the hypothesis nor reality). NOTE the real, unaddressed facts: Setup 4 is the ONLY positive setup in the walk-forward (+0.73%/trade, A/B §7.4) but fired just 3 times in 3.7 years, and its 2% T1 sits far under its 79% breakeven. The productive lever is Setup 4's target/tranche sizing, not its stop gate.

### Phase C: Volatility-Scaled Dynamic Targets (`setups/features.py` & `setups/catalog.py`)
- [ ] **C.1** In `setups/features.py`: Ensure `atr14` and `atr14_pct` (`atr14 / close_adj`) are computed and stored in the features table.
  - **PROBE VERDICT (P5): as a target-scaling input, superseded — the repo's own study Addendum B already REJECTED ATR target normalization** on all three stability tests (dispersion ~2× worse, year drift wider, optimum wanders across K). CR Issue 4 cites the sweet-spot study but not its Addendum B. ATR14 as a *screening* feature (per Addendum B §B.5) remains legitimate and may be added for that purpose only. Gate mining (§7.5) adds: atr14% ≥ 4% cohorts LOSE more (−0.465% vs −0.315%), consistent with the wider-targets-lose finding.
- [ ] **C.2** In `setups/catalog.py`: Parameterize Tranche 1 and Tranche 2 targets dynamically using `atr14_pct` with floors ($2.0\%$) and caps ($3.5\%$).
  - **PROBE VERDICT (P5): REJECTED.** clamp(0.75×ATR, 2%, 3.5%) maps to the K=0.75–1.0 ATR rows: P(win) falls 50.4%→47.5%/39.3% with unchanged negative expectancy. The +3.5% cap also sits at the exact geometry (wide target vs −2.2% wall) the study shows to be worst. C.2 is contradicted by the CR's own cited evidence base.

### Phase D: Archetype Ranking Engine (`setups/ranking.py`)
- [ ] **D.1** Implement `compute_s_reversion` and `compute_s_retest` in `setups/ranking.py`.
  - **PROBE VERDICT (P2): the diagnosis is half-wrong.** Setup 2's median S_runner is 0.340 (CR: 0.270) and 15.8% clear the 0.45 bar — starved, yes. But the claimed slot-monopolization mechanism is IMPOSSIBLE: Setup 2 (dry delivery ≤1.15×) and Setup 5 (shock ≥2×) cannot fire the same symbol-day — **0 co-fires measured in 27,270 + 4,108 fires**. AND the existing S≥0.45 gate ANTI-SELECTS within Setup 2 (admitted cohort E[net] −0.176% vs −0.082% for below-threshold). Any new score must first beat the trivial "no gate at all" baseline in a probe; none exists yet.
- [ ] **D.2** Update `evaluate_and_rank` to score each setup archetype using its native metric.
  - **PROBE VERDICT:** permitted ONLY with a per-archetype selection probe showing E[net](selected) > E[net](ungated cohort) at n ≥ 200 fires. Gate mining (§7.5) found NO stock-level entry feature that separates winners from losers in-book — the burden of proof on any new score is high.
- [ ] **D.3** Implement regime-aware quota allocation (up to 2 slots for mean-reversion during `PULLBACK_CORRECTION`).
  - **PROBE VERDICT (P1 + A/B §7.4): BLOCKED.** Setup 2 is the system's largest loss source (−0.43%/trade, −₹62.6k over 116 trades); reserving slots for it in pullback regimes allocates scarce capital to the worst cohort during exactly the sessions where its measured edge is (marginally) worst.

### Phase E: Walk-Forward Verification & Regression Testing
- [ ] **E.1** Run `scripts/check_market_invariants.py` to confirm zero data integrity violations.
  - **MEASURED (2026-09-18): exit 1.** 8 delivery>volume rows (2019-06, legacy), 7,884 overnight gaps beyond ±20% (corporate-action artifacts; 1,583 of them dated 2023+), 0 price-sanity violations. Materiality check: **0 of the 216 baseline walk-forward trades had a gap artifact in [signal, entry]** — the control measurement is not contaminated, but E.1's "zero violations" standard is unmet and the artifact cleanup remains open.
- [ ] **E.2** Run `pytest tests/unit/` to verify all existing and new unit tests pass.
  - **MEASURED: 372/372 pass** (2026-09-18). A tear-sheet units bug (position P&L ÷ per-share price ≈ 350× inflation of the BRD % rows) was found and fixed in `backtest/metrics.py` + its test during the control run (§7.2).
- [ ] **E.3** Run `nse-cash backtest --walk-forward` (2023–Present) and output tear sheet comparison against baseline.
  - **DONE (2026-09-18), see §7.2.** The baseline tear sheet now exists and FAILS 6 of 7 BRD §10.1 targets (post-tax CAGR +1.93% vs ≥14%; win rate 32.9% vs 48–56%; PF 0.55 vs 1.55–1.85; expectancy −0.34% vs +0.40–0.75%; max DD −6.77% PASSES). All future comparisons use this control.

---

## 6. Acceptance Criteria & Definition of Done

1. **Orthogonal Strategy Candidacy:** During market pullback periods (Nifty $\le$ 20 EMA), the funnel log confirms Setup 2 candidates are actively generated and admitted to the action sheet.
2. **Setup 5 Invalidation Relief:** High-conviction delivery momentum bars ($Z \ge 2.0$, $\text{iMOM} \ge 95\text{th percentile}$) are no longer rejected by the $2.2\%$ stop gate simply due to wide Day $T$ candle range.
3. **Dynamic Target Scaling:** For stocks with ATR $> 3.5\%$, Action Sheet and simulated orders reflect Tranche-1 targets scaled up to $+2.5\%–3.5\%$, capturing outsized upside.
4. **Walk-Forward Performance Standard:** Replaying 2023–Present walk-forward validation demonstrates:
   * Trade count $\ge 75$ trades/year.
   * Out-of-sample win rate maintained between **49% and 55%**.
   * Post-tax CAGR $\ge \mathbf{+22.0\%}$ (up from $\sim 14\%$).
   * Maximum portfolio drawdown strictly $\le \mathbf{8.5\%}$.
5. **Zero Test Regressions:** 100% pass rate across the full pytest suite.

> **REVIEW NOTE (2026-09-18):** Acceptance criterion 4 is unreachable on current evidence: the measured baseline (§7.2) is 6× short of the +14% CAGR it claims as the "from", and the probe falsified or degraded the specific mechanisms (Issues 3–5) offered as the path. Criteria 1–3 measure activity (signals generated, gates opened), not profitability; a funnel can satisfy all three while losing money faster. Any revised CR must make *positive trading P&L in walk-forward* the primary gate, with trade-count criteria secondary.

---

## 7. Empirical Evidence Appendix (2026-09-18)

Everything below is measured on the production store (`data/db/nse_market.duckdb`, 6.53M bar rows, 2010-01 → 2026-09-17) with the engine's own code — `_add_symbol_features`, `compute_s_runner`, the catalog predicates, `evaluate_market_regime_range` — not re-derivations. Entry model matches the study the CR cites: buy at Close_T, 5-session first-passage at day resolution, same-day tie counts as STOP (conservative), 0.35% friction, 20% STCG on net wins.

**Sanity anchor:** the probe's unconditional in-window baseline (P(win) 49.7% at +2% vs −2.2%, n=826,076) reproduces the published study (50.4%, 2021–26 pooled). The measurement tool is sound.

Reproduce:

```bash
.venv/Scripts/python.exe experiments/cr002/empirical_review_cr002.py   # Issue probes P0-P7
.venv/Scripts/python.exe -m nse_cash.cli.main backtest --walk-forward  # control tear sheet
.venv/Scripts/python.exe experiments/cr002/ab_disable_setup2.py        # A/B (7.4)
.venv/Scripts/python.exe experiments/cr002/mine_trade_gates.py         # gate mining (7.5)
.venv/Scripts/python.exe scripts/check_market_invariants.py            # data audit
```

### 7.1 Issue verdicts

| Issue | CR claim | Measured verdict |
|---|---|---|
| 1 | Binary choke disables all setups | TRUE — 454/913 sessions (49.7%) evaluate zero setups |
| 1 | Setup 2's edge is highest in pullbacks | FALSIFIED — PULLBACK P(win) 33.1% < BULL 34.7%; E[net] -0.093% vs -0.089% |
| 1 | Tri-state spec | INCOMPLETE — 85 sessions (9.3%) left unassigned by the three rules |
| 2 | Setup 2 starved by S_runner | TRUE — median 0.340, 15.8% clear 0.45 |
| 2 | Setup 5 takes the slot on co-fire | IMPOSSIBLE — 0 co-fires in 27,270 + 4,108 fires (predicates mutually exclusive on same-day delivery volume) |
| 2 | New archetype scores fix selection | UNPROVEN — existing S>=0.45 gate ANTI-selects within Setup 2 (-0.176% vs -0.082% ungated) |
| 3 | prev_low gate rejects prime bars | TRUE — 298/4,073 (7.3%) pass today |
| 3 | `max(low_T, close*0.979)` remedy | UNSOUND — makes the 2.2% gate non-binding by construction (risk = min(range_T, 2.1%)); all-fires cohort E[net] -0.616% vs -0.085% admitted today |
| 4 | Fixed +2% suppresses alpha on high-ATR names | REJECTED — repo's own study Addendum B already rejected ATR targets on all three stability tests; the CR cites the study but not its addendum |
| 5 | Setup 3 fire rate 0.14% | TRUE — 0.167% measured (1,264 fires) |
| 5 | Setup 3 relaxation improves the system | FALSE — relaxed cohort E[net] -0.416% (current -0.385%); with CR's dry check -0.497% |
| 5 | Setup 4 undershoot 0.5-1.0% | UNDERSTATED — median undercut -3.11%; CR's -1.2% tolerance worsens expectancy (-0.830% vs -0.751%) |
| 2.2 | Baseline expectancy -0.146%...+0.10% | FICTION — measured -0.34%/trade, win rate 32.9% (7.2); CR 2.1 vs 2.2 also contradict each other |

### 7.2 The control: baseline tear sheet (CR-001 engine, walk-forward 2023-01-02 -> 2026-09-17)

216 filled trades (58/yr: 40/11/73/92), 500k capital, 4 slots.

| Metric | BRD 10.1 target | Measured | Verdict |
|---|---|---|---|
| Post-tax CAGR | >= +14.0% | **+1.93%** | FAIL |
| Win rate | 48-56% | **32.9%** | FAIL |
| Profit factor | 1.55-1.85 | **0.55** | FAIL |
| Expectancy (net %/trade) | +0.40...+0.75% | **-0.34%** | FAIL |
| Avg win / loss (net %) | +2.40...2.70 / -1.90...-1.80 | **+1.27 / -1.13** | FAIL |
| Max drawdown | <= 8.5% | **-6.77%** | PASS |
| Benchmark (NIFTY 500 B&H) | — | CAGR +11.0%, max DD -18.8% | — |

**Decomposition:** trading P&L -Rs 91,849; liquid-fund interest +Rs 1,28,249; net +Rs 36,400. **The strategy loses money; the cash yield saves the book.** Yearly P&L: +4.8k / -12.7k / -33.5k / -50.4k — monotonic deterioration; win rate by year 50% -> 27% -> 34% -> 25%.

Per-setup attribution (net % of deployed capital):

| Setup | Trades | Mean | Median |
|---|---|---|---|
| SETUP_1_VCP | 32 | -0.161% | -0.326% |
| SETUP_2_RUBBERBAND | 116 | **-0.433%** | -0.672% |
| SETUP_3_RS_BASE | 30 | -0.412% | -0.790% |
| SETUP_4_ANCHOR_RETEST | 3 | **+0.727%** | +0.151% |
| SETUP_5_RESIDUAL_MOM | 35 | -0.239% | -0.322% |

Exit reasons: STRUCTURAL_STOP_HIT 104 (48%), TRAILING_STOP_HIT 42 (19%), STALL_48H_HIT 38 (18%), TARGET_2_HIT 26 (12%), ENTRY_REJECTED_GAP 9, TIME_DAY5_HIT 6. The -2.2% stop wall decides two-thirds of outcomes.

### 7.3 The decisive number the CR omits: conditional edge per setup

Probe-window unconditional P(2% before -2.2%): 49.7%. Breakeven at (2% win, s stop) = (s + 0.35) / 2.35 / 0.8. Every cohort measured with its own structural stop:

| Cohort | n | P(win) | E[net]/trade | vs breakeven |
|---|---|---|---|---|
| Setup 1 VCP squeeze | 2,060 | 34.0% | -0.276% | -22pp short |
| Setup 2 rubber-band | 26,948 | 34.2% | -0.097% | -22pp short |
| Setup 3 RS base | 1,264 | 41.6% | -0.385% | -20pp short |
| Setup 4 anchor retest | 113 | **59.3%** | -0.288% | -20pp short (n tiny) |
| Setup 5 residual mom (all fires) | 4,073 | **71.4%** | -0.283% | -28pp short |
| Setup 5 ...after today's stop gate | 298 | 28.9% | -0.124% | -18pp short |

**No setup cohort clears breakeven as specified.** Setup 5's 71.4% first-passage win rate vs its prev_low stop is the strongest raw signal in the system, but its stop geometry (median risk 7.78%) makes it untradeable at the 2.2% gate — the real Issue-3 problem, whose honest solution space is the stop-width sweep, not the CR's non-binding formula.

### 7.4 A/B: Setup 2 disabled (one variable, deterministic arms)

Arm A asserted bit-exact reproduction of the 216-trade baseline before arm B was trusted.

| Metric | A: Baseline | B: Setup 2 off | Delta |
|---|---|---|---|
| Trades | 216 | 106 | -51% |
| Win rate | 32.9% | 32.1% | ~flat |
| Expectancy (net %) | -0.342% | -0.269% | +0.073pp |
| Profit factor | 0.55 | 0.64 | +0.09 |
| CAGR post-tax | +1.93% | **+4.93%** | **+3.0pp** |
| Max drawdown | -6.77% | **-3.12%** | **halved** |
| Sharpe | 0.69 | 2.71 | 4x |
| Trading P&L | -Rs 91,849 | **-Rs 35,192** | **+Rs 56.7k** |

**Setup 2 IS the bleed source** (-Rs 62,620 = 68% of trading losses on 54% of trades) — and CR-002 Phases A/D would route MORE capital to it. But B is still negative: freed slots backfilled with slightly worse substitutes (Setup 1 expectancy -0.16% -> -0.25%), so removal is relief, not a fix. 2026 bleed shrinks -Rs 50.4k -> -Rs 3.1k.

### 7.5 Gate mining: what separates winners from losers in-book?

All 216 baseline trades joined to signal-day features (trade_id embeds the signal date — zero lookahead, 100% feature coverage). Book: win 32.9%, mean -0.342%.

**Stock-level selection features DO NOT separate winners from losers.** Best in-book gates (in vs out expectancy spread): delivery_z >= 1 -> +0.06pp (wrong sign: shock names lose MORE); near-52w-high -> +0.02pp (nothing); imom >= p90 -> -0.11pp (top residual momentum does WORSE); rsi2 <= 10 -> -0.20pp (Setup 2's own trigger is a negative selector). Tercile sweeps confirm: delivery_z, imom, rsi2, range_T, prox_52w are flat-to-inverted. High ATR% loses more (-0.465% vs -0.315%), consistent with the Issue-4 rejection.

**The one real signal is MARKET BREADTH** (an environment variable, not a stock filter):

| Breadth bucket (terciles) | n | Mean ret | Win rate |
|---|---|---|---|
| <= 64.1% | 72 | -0.48% | 29% |
| 64.1-77.3% | 72 | -0.51% | 25% |
| **>= 77.3%** | 72 | **-0.03%** | **44%** |

A 0.46pp expectancy spread — an order of magnitude larger than any stock-level gate — with a clean monotone step. Strongest in-book combo (breadth >= 77.3% AND atr14% <= 2.5%): **+0.28% mean ret (n=19)** vs -0.40% for the rest — intriguing but n=19: a hypothesis, not a gate. Note the tension with the current engine: entries are *blocked* below 50% breadth but never *favored* above 77% — the profitable asymmetry is on the offensive side, which this CR never considers.

Small-n rows (setup==4, extreme buckets) are flagged, not promoted: at n=216 nothing here survives multiple-comparison discipline; candidates go back through the probe funnel.

### 7.6 Instrument defects found and fixed during this review

1. **Tear-sheet units bug (fixed):** `backtest/metrics.py` computed BRD % rows as position P&L ÷ per-share price (~350× inflation; the table printed "Avg Win +350.91%"). Now divides by `entry_cost` (capital deployed, friction-inclusive) and returns None without an honest denominator. Test fixture updated; 372/372 pass.
2. **Data invariants (open):** `check_market_invariants.py` exits 1 — 8 delivery>volume rows (2019 legacy), 7,884 impossible overnight gaps (corporate-action artifacts; 1,583 dated 2023+), 0 price-sanity violations. Materiality check: **0 of the 216 baseline trades had a gap artifact in [signal, entry]** — the control is clean, but artifact cleanup remains open work.
3. **Stale artifacts (documented):** prior `reports/backtest/` held a 3-month 2026 smoke run (1 trade). Preserved to `reports/backtest_smoke_20260330/` and superseded by the full walk-forward control.

### 7.7 Where this leaves the CR

The CR's four bottlenecks reduce to two true ones (the binary choke — real but with a falsified remedy; Setup 5's gate rejection — real with an unsound remedy), and its quantitative promises are unreachable from a −0.34%/trade baseline. The single most valuable discovery in this review is negative: **no stock-level entry feature in the current feature store separates winners from losers in-book.** Selection-quality work (Phase D) has a high burden of proof; environment conditioning (breadth) and exit geometry (stop width, Setup 4 sizing) are where measured signal actually lives.

## 8. Revised Program (evidence-first)

1. **P0 — stop-width sweep in the engine** (1.5/2.2/3/4% × stall variants) against the control. The wall decides 67% of exits; highest-information experiment; the honest fix-space for Issues 3 and 5.
2. **P1 — Setup 4 sizing study:** only positive setup (+0.73%) but 2% T1 vs 79% breakeven; test T1/T2 split and runner targets on its cohort before scaling.
3. **P2 — breadth-conditioned offense:** engine arm with entries *favored* (or slot-weighted) at breadth ≥ 77th percentile; validate the §7.5 spread out-of-sample (2021–22) before believing it.
4. **P3 — Setup 2 reselection or retirement:** its A/B relief is +3pp CAGR; any reselection must beat "off" in a probe, not merely exist (Phase D bar).
5. **P4 — data hygiene:** corporate-action artifact cleanup so the invariants gate can pass; required before any 2010–2022 in-sample claims.
6. **Standing rule:** no Phase A–D item merges without an isolated probe showing E[net] > 0 after 0.35% friction + 20% STCG at n ≥ 200, with the §7.2 tear sheet as control. Trade-velocity criteria are subordinate to trading P&L.
