# Change Request: CR-2026-003

## System Post-Mortem & Rebuild Program: Exit Geometry, Environment Conditioning, and Catalog Pruning

* **CR Number:** `CR-2026-003`
* **Status:** `PROPOSED — EVIDENCE-FIRST PROGRAM` (2026-09-18)
* **Predecessors:** CR-2026-001 (implemented), CR-2026-002 (**empirically gutted — see its §7 appendix; do not implement Phases A–D as written**)
* **Evidence base:** `experiments/cr002/` (Issue probes P0–P7, A/B, gate mining), `reports/backtest/` (walk-forward control), `reports/backtest_smoke_20260330/` (preserved smoke run), `data/results/` (Phase-0 study + Addendum B)
* **Author context:** Kailash Nadh pragmatic review — no mechanism survives this CR on argument; everything survives or dies by the probe funnel.

---

## 1. Purpose

CR-2026-002 assumed the engine under it was profitable and asked how to make it more profitable. Measured reality (CR-002 §7.2): **the CR-001 engine loses ₹91,849 trading over 3.7 years (CAGR +1.93%, of which +6.35% comes from liquid-fund interest on idle cash — trading itself subtracts ~4.4pp/year)**, wins 32.9% of the time against a 48–56% BRD target, and deteriorates monotonically (+₹4.8k in 2023 → −₹50.4k in 2026 YTD). No setup clears breakeven. No stock-level entry feature separates winners from losers in-book.

This CR therefore does not optimize. It **prunes, re-anchors exit geometry, conditions on environment, and gates every future change behind a kill test**. Brutality is the point: mechanisms that measurably do not work are named for removal, not tuned.

## 2. The Three Load-Bearing Facts (all measured, see CR-002 §7)

1. **Selection has no signal.** Across the whole book, no entry-time stock feature separates winners from losers (CR-002 §7.5): delivery_z ≥ 1 → +0.06pp spread (wrong sign), imom ≥ p90 → −0.11pp, rsi2 ≤ 10 → −0.20pp, near-52w → +0.02pp. Tercile sweeps flat-to-inverted. **Any new selection score is dead on arrival until this changes.**
2. **The environment carries the signal.** Breadth terciles: ≤64.1% → −0.48%/trade; 64–77% → −0.51%; **≥77.3% → −0.03%** (win 44% vs 25–29%). A 0.46pp expectancy spread — the only large, monotone effect found anywhere in this system. The engine blocks entries below 50% breadth but never *favors* them above 77%.
3. **Exits decide everything.** Exit mix: structural stops 48%, trailing stops 19%, stall exits 18%, T2 target hits just 12%. The only cohort with real edge (Setup 5 fires, 71.4% first-passage win vs its prev_low stop) is untradeable because its stop geometry (median risk 7.78%) collides with the 2.2% gate. The wall, not the trigger, is the business model — and the business model is losing.

## 3. Algo-by-Algo Verdict (brutal section)

Verdict legend: **REMOVE** (delete from the catalog), **AMEND** (keep with the stated constraints only), **QUARANTINE** (paper-trade/diagnostic only, no capital). Sample sizes: setup-level verdicts rest on 216 engine trades + 831k-row cohort probe; nothing here is "confirmed," everything is "measured with the n given."

### 3.1 Setup 2 — Rubber-Band Pullback (`evaluate_setup2_rubberband`) — **REMOVE**
- Largest trade count (116/216 = 54%) and largest loss (−₹62,620 = 68% of trading losses; −0.433%/trade, median −0.672%).
- Its own trigger (RSI2 ≤ 10) is a **negative selector** in-book (−0.20pp spread). Its forward edge in pullback regimes — the regime CR-002 wanted to reserve slots for — is *worse* than in bull regimes (P(win) 33.1% vs 34.7%).
- A/B with it disabled: CAGR +1.93% → +4.93%, max DD halved (−6.77% → −3.12%), Sharpe ×4. **The single highest-certainty action available.**
- *What would resurrect it:* a probe where the Setup 2 cohort beats "off" (arm B) on E[net] at n ≥ 200 with a *pre-registered* selection rule. Given §2.1, the bar is very high. Until then: removal, not tuning.
- CR-2026-002's Issue 1/Issue 2/Phase A/D (tri-state routing, archetype score, mean-reversion quotas) are **dead** with it. Do not implement.

### 3.2 Setup 3 — RS Base (`evaluate_setup3_rs_base`) — **AMEND or REMOVE**
- 30 trades, −0.412%/trade, 43.3% win but avg win +1.26% vs avg loss −1.69% — the worst win/loss shape in the book.
- Fire rate 0.167% (measured). CR-002's relaxation (range ≤ 4.2%, prox ≥ 97.5%, dry-volume check) **degrades** expectancy further (−0.416% / −0.497%) — the fix directions proposed were all wrong.
- *Constraints if kept:* entry only at breadth ≥ 77th percentile (§2.2); stop = low_T as now; **and** a probe showing cohort E[net] > ungated arm B baseline. Otherwise remove. Do not widen any threshold.

### 3.3 Setup 1 — VCP Squeeze (`evaluate_setup1_vcp_squeeze`) — **QUARANTINE, then decide by probe**
- 32 trades, −0.161%/trade (least-bad of the losers), but avg win +1.68% vs avg loss −0.88% — the only setup whose shape is right; it loses on hit rate (28%), not geometry.
- Absorbed backfilled slots when Setup 2 was disabled and its expectancy *got worse* (−0.16% → −0.25%) — evidence that its marginal trades are junk, not that its core is broken.
- *Constraints if kept:* breadth gate as above; pv_percentile ≤ 0.15 (the squeeze leg) must be the binding condition, and the shock-flag conditions (shock_a_5d/z15_count_3d) are demoted to tie-breakers — in-book, "shock within 5d" trades have *negative* spread (−0.16pp).

### 3.4 Setup 5 — Residual Momentum (`evaluate_setup5_residual_momentum`) — **AMEND (exit geometry only); highest-priority rescue**
- Full fire cohort wins **71.4%** of first-passage races vs its own prev_low stop — the strongest raw signal in the system (probe P7). But only 7.3% of fires pass the 2.2% gate; the admitted residue is the *worst* slice (28.9% win, med MFE +1.40%). The gate's net effect: keep the losers, discard the winners.
- CR-2026-002's remedy is **mathematically void** (`max(low_T, close×0.979)` makes risk ≡ min(range_T, 2.1%) — gate non-binding) and empirically terrible (all-fires cohort at its stop: −0.616%/trade). **Do not implement B.1.**
- *The honest constraint change:* stop must be defined by **stop-width sweep data**, not by a formula that trivially passes the gate (see §4.1). Expect the answer to be a wider, ATR-informed stop with reduced position size — constant rupee risk, different geometry.

### 3.5 Setup 4 — Anchor Retest (`evaluate_setup4_anchor_retest`) — **KEEP AS-IS; fix its target, not its stop**
- Only positive setup in the book (+0.727%/trade, 3 trades — n too small to trust, n large enough to respect) and the best cohort P(win) in the probe (59.3% vs 49.7% unconditional).
- CR-002's relaxation (−1.2% undershoot) worsens expectancy (−0.830% vs −0.751%) and rests on an understated premise (median undercut is −3.11%, not 0.5–1.0%). Its real problem is the 2% T1 vs 79% breakeven — it wins often and gets paid nothing.
- *Amendment:* size/target study (§4.2). Touch nothing else.

### 3.6 Selection score — S_runner ≥ 0.45 (`ranking.py`, `S_RUNNER_MIN`) — **REMOVE THE GATE, keep the score as telemetry**
- The gate **anti-selects**: within Setup 2, admitted cohort −0.176% vs −0.082% for below-threshold. Its top term (delivery_z) is inverted in-book (§2.1).
- Until a score demonstrates positive spread in a probe, the honest configuration is **no conviction gate** (arm B already runs near-threshold-free and is better than baseline).

### 3.7 Regime router — binary OFFENSIVE/DEFENSIVE (`market_regime.py`, `pipeline.py`) — **AMEND (asymmetric offense), not CR-002's tri-state**
- The binary choke is real (49.7% of sessions evaluate nothing) but CR-002's tri-state is falsified as a Setup-2 delivery mechanism and has a spec hole (9.3% of sessions unassigned).
- The measured asymmetry is elsewhere: **entries above the 77th-percentile breadth are ~0.46pp/trade better**; the engine never exploits this. Amendment in §4.3.

### 3.8 Tranche/stall/runner exit stack — **AMEND (this is where the money is)**
- T2 target hits: 26 trades, **+2.44% avg, 100% winners** — the profit engine is 12% of the book.
- TRAILING_STOP_HIT: 42 trades, 69% positive, mean −0.09% — roughly a scratch; harmless.
- STALL_48H_HIT: 38 trades, 21% positive, mean −0.32% — a slow-bleed exit that closes trades that might have recovered, at a worse price than the trail would. **Prime suspect for removal/tightening in the sweep.**
- STRUCTURAL_STOP_HIT: 104 trades, 2% positive, −1.27% avg — the wall. Its width is *the* open variable (§4.1).
- Constraint note: the profitable cohort geometry (T2 +2.44%) was earned *through* the current wall — sweep arms must prove they don't cannibalize these 26 trades.

### 3.9 Kill switch, cooldown, gap ceiling, GTT limit leg — **KEEP** (7.5% DD cap never fired in 3.7y; gap ceiling rejected 9 bad entries; no anomalies observed). Not every mechanism is guilty.

### 3.10 ATR target scaling (`C.2`) — **REMOVED** (already rejected by the repo's own Addendum B on all three stability tests; CR-002 §7.1). ATR14 may return only as a screening feature or as the *stop* unit in §4.1 with size compensation.

## 4. The Experiment Queue (each with hypothesis, method, and kill criteria)

Standing rules: every arm runs the same walk-forward (2023-01-02 → 2026-09-17), same config, deterministic; every probe vs the §7.2 control; nothing merges on n < 200 cohort trades without out-of-sample confirmation; multiple comparisons acknowledged — pre-register the hypothesis before running.

### 4.1 E1 — Stop-Width Sweep (P0; highest information)
- **Hypothesis:** the −2.2% wall converts too many would-be winners into losers; wider stops with proportionally smaller size keep rupee risk constant while raising P(reach T2).
- **Method:** arms at stop ∈ {1.5%, 2.2% (control), 3.0%, 4.0%} × {fixed, 1×ATR14, 1.5×ATR14} × {stall on, stall off}, size scaled to keep slot rupee-risk constant. Run per-setup cohorts AND the full engine.
- **Kill criteria:** an arm survives only if trading P&L > control AND PF > control AND max DD ≤ 8.5% AND the T2-hit count does not drop below control. If *no* arm beats control: the wall is correctly sized and the selection is simply negative — proceed directly to pruning (§3.1–3.2) and §4.3.
- **Prediction to falsify:** wider stops will help Setup 5 (71.4% FP cohort) and hurt the book through bigger per-trade losses; the split will tell us which effect dominates.

### 4.2 E2 — Setup 4 Sizing/Target Study
- **Hypothesis:** Setup 4's 59.3% cohort P(win) vs 79% breakeven means its T1 (2%) is too small relative to its stop geometry; raising T1 for Setup 4 only (to 2.5/3%) turns a positive cohort into a profitable one.
- **Method:** isolated cohort FP study first (no engine changes); then a one-variable engine arm (Setup 4 T1 ∈ {2.0, 2.5, 3.0%}).
- **Kill criteria:** cohort study must show E[net] > 0 at the proposed target at n ≥ 100 fires before any engine arm runs; engine arm must beat control on trading P&L without degrading other setups' attribution.
- **Honesty note:** 3 engine trades is anecdote-level. This experiment is cheap; do not let its positive current sign overweight the program.

### 4.3 E3 — Breadth-Conditioned Offense (P2)
- **Hypothesis:** conditioning on environment beats conditioning on stocks: gating or weighting entries by NIFTY 500 breadth ≥ 77th percentile (measured 77.3% over 2023+) captures the only strong spread found (−0.03% vs −0.50%/trade).
- **Method:** (a) first, out-of-sample validation of the tercile spread on 2021–2022 data (the spread must hold direction, ideally magnitude); (b) engine arms: {breadth ≥ Q2 gate}, {breadth ≥ Q2 → 2× slot weighting}, {breadth < Q2 → reduce slot capital to half}. Breadth percentile computed from a trailing 2-year window (no full-history leak).
- **Kill criteria:** the 2021–22 spread must be ≥ +0.15pp (at least a third of the 2023+ spread) to proceed to engine arms; engine arm must beat arm B (Setup-2-off) control, not just the original baseline.
- **This is the only experiment that can plausibly reach positive trading P&L without new features.**

### 4.4 E4 — Catalog Pruning Validation (confirms §3)
- **Hypothesis:** arm B (Setup 2 off) is better because Setup 2 is bad, not because of luck; removing Setup 3 as well does not hurt further.
- **Method:** arms: {S2 off (have)}, {S2+S3 off}, {S2 off + no S-gate}, each with/without the §4.3 breadth gate.
- **Kill criteria:** if {S2+S3 off} < {S2 off}, Setup 3 stays with its §3.2 constraints; if {no S-gate} > {S-gate}, remove `S_RUNNER_MIN` enforcement in `ranking.py`.

### 4.5 E5 — Feature Expansion for Selection (only after E1–E4)
- **Hypothesis:** the current feature store lacks the *information* selection needs (not better math on the same columns).
- **Method:** candidate features with a priori logic: F&O participation, sector momentum rank, market-cap bucket, earnings-proximity flag, gap context (prior-day gap direction), prior-day delivery *reversal*. Each enters the same probe funnel: cohort E[net] spread at n ≥ 200 before any code change.
- **Kill criteria:** any feature that cannot show positive spread out-of-sample is dropped permanently. Two consecutive failed features → freeze selection work for the cycle (diminishing returns).

### 4.6 E6 — Data Hygiene (parallel, unblocks in-sample claims)
- **Task:** resolve 7,884 impossible overnight gaps (corporate-action artifacts; 1,583 dated 2023+) and 8 delivery>volume rows; make `check_market_invariants.py` exit 0.
- **Why:** until then, every claim is walk-forward-only and the 2010–2022 in-sample window is unusable for validation depth. Measured: 0/216 baseline trades touched — the current control is clean, so this is not urgent, but it is required before *any* longer-history validation (and before trusting in-sample windows for E1's ATR-stop arms).

## 5. What Still Needs Checking (open questions, ranked)

1. **Does the breadth spread replicate out-of-sample (2021–22)?** (E3a — gates everything environmental.)
2. **Is there any stop geometry under which Setup 5's 71.4% cohort is tradeable?** (E1 cohort slice.)
3. **Are the 26 T2 winners concentrated in specific setups/regimes** — i.e., is the profit engine itself a conditional phenomenon? (Blotter analysis, cheap, do with E1.)
4. **Does the stall rule pay for itself?** It closes 38 trades at −0.32%; how many of those reached T2 later in the counterfactual? (Needs a counterfactual simulator extension — small.)
5. **Was the interest-only comparison decisive?** Interest-only CAGR is +6.35% vs arm B's +4.93% — currently, *not trading at all beats every trading arm*. Any accepted change must clear this bar, not just "beat the baseline."
6. **Why is 2024's book 1/5th the size of 2025/2026's** (11 trades vs 73/92) despite 61% OFFENSIVE sessions? If slots were locked by stalled trades, the stall rule and slow exits are leaking opportunity. (Blotter forensics, cheap.)
7. **Sector concentration:** the sector gate allowed up to 4 distinct sectors; are losses clustered in specific sectors? (Cheap descriptive pass.)
8. **Is the walk-forward deterioration (2023 +₹4.8k → 2026 −₹50.4k) regime drift or alpha decay?** If 2023's profit came from one or two lucky trades, the system never had edge; if decay is smooth, the market regime is the story. (Yearly attribution decomposition.)

## 6. Acceptance Criteria for CR-2026-003 (Definition of Done)

1. Every §3 REMOVE/AMEND decision is implemented behind a feature flag or config knob (no deletes without a flag: the A/B harness must be able to re-enable any pruned path for re-testing).
2. E1–E4 arms completed with artifacts in `experiments/cr003/` and a results table appended to this document.
3. At least one configuration achieves **positive trading P&L (not just CAGR above interest-only)** over the walk-forward, OR the program concludes with a documented recommendation to stop trading this system and hold the liquid fund — which is an acceptable, evidence-based outcome.
4. `check_market_invariants.py` exit 0 (E6) or a documented blocking reason.
5. All existing tests pass (372/372) plus new tests for any changed gate/router behavior.
6. No change accepted on n < 200 cohort trades without explicit out-of-sample confirmation documented in §7.

## 7. Results Log (append as experiments complete)

*(empty — to be filled by the experiment queue)*

---

## Appendix A: The One-Line Summary of Each Dead Mechanism

| Mechanism | The brutal one-liner |
|---|---|
| Setup 2 | 54% of the trades, 68% of the losses; its own trigger is a negative selector; removing it is the best trade the system ever made. |
| Setup 3 | 43% win rate, worst payoff shape in the book, and every proposed fix makes it worse. |
| S_runner ≥ 0.45 | Conviction gate that admits the worse half. Confidence theater. |
| Setup 5 as-configured | 71.4% of races won, then thrown away by a gate that keeps the 7.3% worst-geometry subset. |
| CR-002 tri-state | Routes capital to the biggest loser during the regime where it loses hardest; 9.3% of sessions not even defined. |
| ATR targets (C.2) | Rejected by the repo's own study, twice; the CR proposed it without reading its own addendum. |
| The BRD §10.1 targets | Written before a single walk-forward existed; the system misses 6 of 7 and the "baseline" they assume was fiction. |
| "More trades" as a goal | Velocity criteria in a negative-expectancy system are an accelerator, not a KPI. |
