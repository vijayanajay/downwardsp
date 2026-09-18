# Change Request: CR-2026-001
## Core Trading Engine & Microstructure Realignment: Setups 1 & 4 Fixes, Per-Setup Ranking Normalization, GTT Limit Buffers & Execution Fidelity

* **CR Number:** `CR-2026-001`
* **Status:** `IMPLEMENTED` (2026-09-18; empirical review + build; 219/219 tests green)
* **Created Date:** 2026-09-18
* **Author / Reviewer:** Kailash Nadh Pragmatic Review
* **Implemented With:** Pre-implementation empirical probes on the production store
  (2.3M bar rows 2010–2026, 593k feature rows 2023–2026; scripts in
  `experiments/cr001/*.py`, outputs alongside them (`cr001_probe*.txt`).
  **Every number below is measured, not asserted.** Three of the CR's original
  prescriptions were corrected by that evidence — see §2bis.
* **Target Version:** Post-Phase 7 / Phase 8 Readiness
* **Affected Components:**
  * `src/nse_cash/setups/features.py` (Feature engine: rolling accumulation & breakout lookbacks)
  * `src/nse_cash/setups/catalog.py` (Setup predicates: Setup 1 physical conflict, Setup 4 base anchor)
  * `src/nse_cash/setups/ranking.py` (Conviction scoring: orthogonal normalization across 5 setups)
  * `src/nse_cash/execution/action_sheet.py` (Kite Dual-GTT order sheet: trigger vs limit price buffers)
  * `src/nse_cash/backtest/fill_model.py` (Execution fidelity & gap realization documentation)
  * `src/nse_cash/core/constants.py`, `core/config.py`, `core/types.py`, `core/tick.py` (new),
    `data/storage.py` (features schema heal), `funnel/pipeline.py`, `backtest/engine.py`,
    `execution/ledger.py`, `cli/ledger_cmd.py`, `scripts/check_market_invariants.py` (new)
  * `tests/unit/test_setups.py` (setup + ranking tests live here), `tests/unit/test_fill_model.py`,
    `tests/unit/test_engine.py`

---

## 1. Executive Summary & Context

An exhaustive engineering and microstructure review of the Phase 1 through Phase 7 implementation revealed that while the code is well-structured, modular, and possesses 100% test coverage for its unit assertions, **critical mathematical and algorithmic bugs in the setup predicates and ranking formula cause Setups 1, 2, 3, and 4 to fail or be permanently disqualified in production**.

Specifically:
1. **Setup 1 (VCP / Parkinson Squeeze)** collapses a 5-day accumulation lookback into Day $T$, creating a physical impossibility where delivery volume must be $+1.5\sigma$ to $+2.2\times$ above mean while total volume is $35\%$ below mean on the exact same session.
2. **Setup 4 (Anchor Retest)** references `base_low_90` instead of `base_high_90`, causing the algorithm to seek $2\%$ bounces off 90-day troughs instead of multi-month resistance breakouts.
3. **Composite $S_{\text{runner}}$ Ranking** uses an unnormalized $Z_{\text{delivery}} \in [0, 3]$, which mathematically suppresses dry-up / mean-reversion setups below the $0.70$ threshold, turning the system into an unintended single-strategy engine (Setup 5 only).
4. **Zerodha Kite GTT Execution** lacks limit price buffer parameters, exposing real-money orders to non-execution risks during gap-down market openings.

This Change Request details the exact root causes, code modifications, and an actionable developer implementation checklist to resolve these issues before executing Phase 8 benchmarking.

---

## 2. Issue Analysis & Required Changes

> **Read §2bis before implementing.** The root-cause diagnoses below are all
> confirmed and stand as written, but three of the original prescriptions were
> corrected during implementation against measured data: the Issue-2 anchor
> (§2bis R2), the Option-A/B choice (§2bis R3) and the buffer constant naming
> (§2bis R1). §2 is preserved verbatim as the audit trail.

### Issue 1: Physical Microstructure Contradiction in Setup 1 (`setups/catalog.py`)

#### Root Cause
In `src/nse_cash/setups/catalog.py` (lines 51–65):
```python
# Accumulation footprint
shock_a = dlv is not None and sma20d is not None and dlv >= 2.20 * sma20d
shock_b = z is not None and z >= 1.50
if not (shock_a or shock_b):
    return None

# Volume dry-up
vol, sma20v = _bar(row, "volume_adj"), _v(row, "sma20_vol")
if vol is None or sma20v is None or not vol <= 0.65 * sma20v:
    return None
```
Because delivery volume is a strict subset of total traded volume ($\text{Delivery\_Qty} \le \text{Total\_Volume}$), requiring deliverable volume to be $2.2\times$ its 20-day mean (or $+1.5\sigma$) while total traded volume is $\le 0.65\times$ its 20-day mean on the **same day $T$** requires delivery percentage to exceed $200\%\text{--}300\%$. In real market data, this evaluates to `False` in $99.9\%$ of sessions.

The canonical specification (`algos.md` / `ACTION_PLAN_8_PHASES.md` §5.2) specifies:
* Accumulation Shock: $\ge 1$ day in the **last 5 sessions** with $\text{Delivery} \ge 2.20 \times \text{SMA}_{20}(\text{Delivery})$ AND $\text{Close} > \text{Open}$, OR $Z_{\text{Delivery}} \ge 1.50$ on $\ge 2$ of the **last 3 sessions**.
* Volume Dry-Up: **Day $T$** volume $\le 0.65 \times \text{SMA}_{20}(\text{Volume})$.

#### Required Fix
1. In `src/nse_cash/setups/features.py`, add vectorized rolling columns during feature computation:
   * `shock_a_5d`: Rolling 5-session max of `(delivery_adj >= 2.20 * sma20_delivery) & (close_adj > open_adj)` (boolean/int 0 or 1).
   * `z15_count_3d`: Rolling 3-session sum of `(delivery_z >= 1.50).astype(int)`.
2. In `src/nse_cash/setups/catalog.py`, update `evaluate_setup1_vcp_squeeze` to check:
   ```python
   shock_a = (_v(row, "shock_a_5d") or 0) >= 1
   shock_b = (_v(row, "z15_count_3d") or 0) >= 2
   if not (shock_a or shock_b):
       return None
   ```
   And preserve Day $T$'s volume dry-up: `vol <= 0.65 * sma20v`.

---

### Issue 2: Resistance Reference Inversion in Setup 4 (`setups/catalog.py`)

#### Root Cause
In `src/nse_cash/setups/catalog.py` (lines 146–150):
```python
base_low = _v(row, "base_low_90")
if None in (close, high, low, open_, base_low) or base_low <= 0:
    return None
breakout_level = 1.02 * base_low  # resistance ~ top of the 90-day base
```
The algorithm uses `base_low_90` (the 90-day rolling minimum price) and defines resistance as $1.02 \times \text{base\_low}$. This calculates a price $2\%$ above the 90-day trough instead of the 90-day resistance ceiling (`base_high_90`).

#### Required Fix
1. In `src/nse_cash/setups/catalog.py`, change `base_low` to `base_high`:
   ```python
   base_high = _v(row, "base_high_90")
   if None in (close, high, low, open_, base_high) or base_high <= 0:
       return None
   breakout_level = base_high  # True 90-day structural ceiling
   ```
2. Re-anchor the support retest logic to test whether Day $T$ Low retests within $\pm 0.8\%$ of `breakout_level` while holding above it (`low >= breakout_level * 0.992` and `close >= breakout_level`), accompanied by volume dry-up ($\text{Volume} \le 0.55 \times \text{SMA}_{20}$) and lower rejection shadow $\ge 40\%$.
3. Set `structural_stop = breakout_level * 0.998`.

---

### Issue 3: $S_{\text{runner}}$ Mathematical Bias & Threshold Lockout (`setups/ranking.py`)

#### Root Cause
In `src/nse_cash/setups/ranking.py`:
$$S_{\text{runner}} = 0.35 \times Z_{\text{delivery}} + 0.35 \times \text{iMOM}_{\text{percentile}} + 0.30 \times (1 - \text{PV}_{\text{percentile}})$$
And line 101:
```python
if best is not None and best.s_runner >= s_runner_min:  # s_runner_min = 0.70
```
* $Z_{\text{delivery}}$ is winsorized to $[0, 3.0]$.
* Mean-reversion (Setup 2) and consolidation/squeeze setups (Setups 1, 3, 4) require subdued or dry volume. Therefore, on the entry signal day, $Z_{\text{delivery}} \le 0$.
* With $Z_{\text{delivery}} \le 0$, the maximum attainable score even with 100th percentile momentum and 0th percentile volatility is:
  $$0.35(0) + 0.35(1.0) + 0.30(1.0) = 0.65$$
* Because $0.65 < 0.70$, **Setups 1, 2, 3, and 4 are mathematically precluded from ever generating a candidate signal**. Setup 5 (which demands a delivery shock on Day $T$) easily scores $> 0.80$, monopolizing all portfolio slots.

#### Required Fix
Normalize conviction scoring on a scale appropriate for each setup archetype:
1. **Option A (Normalized Metric Scale):**
   Normalize $Z_{\text{delivery}}$ into $[0, 1]$ via min-max scaling:
   $$Z_{\text{norm}} = \frac{\max(0.0, Z_{\text{delivery}})}{3.0}$$
   So all three terms reside strictly on $[0, 1]$.
2. **Option B (Archetype-Specific Conviction Scoring - Recommended):**
   * **Momentum / Runner Setups (Setups 1, 3, 5):**
     Rank using $S_{\text{runner}}$ with $Z_{\text{norm}}$:
     $$S_{\text{runner}} = 0.35 \times \frac{\max(0, Z_{\text{delivery}})}{3.0} + 0.35 \times \text{iMOM}_{\text{pct}} + 0.30 \times (1 - \text{PV}_{\text{pct}})$$
     Set threshold $S_{\text{runner}} \ge 0.50$ (or rank top candidates cross-sectionally).
   * **Mean-Reversion Setup (Setup 2):**
     Score based on oversold exhaustion and trend strength:
     $$S_{\text{reversion}} = 0.50 \times \left(1.0 - \frac{\text{RSI}(2)}{10.0}\right) + 0.50 \times \text{RS}_{\text{percentile}}$$
   * **Breakout-Retest Setup (Setup 4):**
     Score based on candle rejection quality and relative strength:
     $$S_{\text{retest}} = 0.50 \times \text{Shadow}_{\text{pct}} + 0.50 \times \text{RS}_{\text{percentile}}$$
3. Candidate ranker selects the highest-scoring candidate per symbol, normalizes scores, and sorts descending with symbol tie-breaking.

---

### Issue 4: Kite GTT OCO Limit Order Execution Gap Buffer (`execution/action_sheet.py`)

#### Root Cause
On Zerodha Kite, a GTT OCO Stop order requires two inputs:
1. **Trigger Price:** The market price that arms and fires the order.
2. **Limit Price:** The limit order price sent to the exchange order book upon triggering.

If the operator sets `Trigger Price = Limit Price = Stop_Price`, and the stock experiences an overnight gap-down opening below the stop price:
* The trigger fires at market open.
* The limit order enters the order book at `Stop_Price`.
* Because the current market price is lower than `Stop_Price`, the sell limit order will **not execute**. The trade remains open and unhedged during a market decline.

#### Required Fix
1. In `src/nse_cash/core/constants.py` and `src/nse_cash/core/config.py`, introduce a configurable GTT limit buffer:
   $$\text{GTT\_STOP\_LIMIT\_BUFFER} = 0.015 \quad (1.50\% \text{ below trigger})$$
2. In `src/nse_cash/execution/action_sheet.py`, render explicit columns in the Kite Playbook:
   * **Stop Trigger Price:** Exactly `structural_stop` rounded to tick (₹0.05).
   * **Stop Limit Price:** `structural_stop * (1 - 0.015)` rounded to tick (₹0.05) or circuit floor, guaranteeing market-matching fill on gap-down.
3. Include explicit warning text in the Action Sheet explaining the difference between GTT Trigger and Limit Price on Kite.

---

### Issue 5: Entry Execution Fidelity Clarification (10:00 AM vs 9:15 AM Open)

#### Root Cause
The Action Sheet directs manual execution at 10:00 AM IST, checking:
$$\text{Price}_{\text{10:00 AM}} \le \text{Close}_T \times 1.012$$
While the historical backtest engine (`backtest/fill_model.py`) simulates execution at `Open_{T+1}` (9:15 AM).

#### Required Fix
1. Document the deliberate simulation proxy clearly in `fill_model.py` and `docs/ARCHITECTURE.md`:
   * Free daily Bhavcopy lacks 10:00 AM intraday prints.
   * `Open_{T+1}` serves as the structural proxy with the $+1.2\%$ gap ceiling gate.
2. Ensure `execution/action_sheet.py` prominently instructs the trader:
   * "If at 10:00 AM, LTP $> \text{Max Entry}$ (₹X.XX), the signal is INVALIDATED. Do not place orders."
   * "If LTP $\le \text{Max Entry}$, place a Limit Buy order at current LTP."

---

## 2bis. Empirical Verification Record: Corrections, Additions & Rejections

Before implementation, every prescription in §2 was tested against the
production store (2.3M daily-bar rows 2010–2026; 593k feature rows 2023–2026;
scripts `experiments/cr001/*.py`, outputs `experiments/cr001/cr001_probe*.txt`).
The CR's four root-cause diagnoses were **all confirmed**. Three of its
prescribed fixes were **corrected by the data**; two additions were made. Each
decision, its evidence, and what was rejected:

### R1. One GTT buffer constant, not two names — and it is config, not a constant call

**Change:** the checklist's `GTT_LIMIT_SLIPPAGE_BUFFER` (constants) and the
Issue-4 body's `GTT_STOP_LIMIT_BUFFER` were collapsed into a single name,
`GTT_STOP_LIMIT_BUFFER` (default 0.015), mirrored as
`risk.gtt_stop_limit_buffer` in `RiskConfig`/`config.yaml` (env-overridable).
**Reason:** the original doc specified the same 1.5% under two different names
in two files — a config fork waiting to happen. One source of truth now feeds
the sheet, the book, the ledger echo and the fill model.
**Measured tradeoff (kept honest):** 27.7% of clean sessions gap down at all;
5.06% gap beyond −1.5%, 3.2% beyond −2%. The 1.5% default covers ~95% of
gap-down sessions; widening to 2.0% halves the residual risk at the cost of
~0.5% extra worst-case loss per stopped trade. Default stands at 1.5%; the
operator can tighten/loosen per preference via config.

### R2. Issue 2's prescribed fix (rolling `base_high_90` anchor) — REJECTED, replaced with a frozen anchor

The CR's own fix would have reproduced the bug it meant to fix, one level up.
`base_high_90` is a rolling 90-session max that **includes the breakout rally
bars themselves**. Measured on 29,868 real breakout events from the store: by
1 session after breakout the rolling high sits **+3.5% above** the true
pre-breakout ceiling on average (+6.8% by day 7; worst case +91% on runners).
"Retest of rolling base_high_90" is therefore a retest of the rally peak — a
momentum-chasing entry with no structural support beneath it.
**Implemented instead:** `breakout_anchor_90` = the 90-session ceiling ending at
the breakout day (B−1 window), **frozen at B**; `breakout_age` = sessions since
the most recent fresh cross (close > 1.02× that ceiling). The retest predicate
gates age to 3–7 sessions, low within ±0.8% of the anchor, close holding above,
dry volume ≤ 0.55×, ≥ 40% rejection shadow. Measured fire-rate: **2,234 full
fires (1.52% of 2022+ windows)**, 6,547 retest+hold without the dry gate — sane
rarity for the rarest setup. For contrast, the old broken predicate (1.02 ×
`base_low_90`, a retest of the 90-day trough) fired 38,809 times (1.69%) —
proof it selected troughs, not resistance. A unit test now explicitly asserts
the predicate returns None when only the rolling high exists.

### R3. Option B (per-setup score formulas `S_reversion` / `S_retest`) — DEFERRED, not implemented

**Reason for deferral:** Option B arrived with new weights but **no thresholds**
for the two new scores and no plumbing design (`CandidateSignal` carries one
`s_runner`; per-setup scores need a mapping and a re-spec of every gate). That
is an incompletely specified option — exactly the kind of speculative knob the
plan's own philosophy forbids until data demands it. The plan's Phase 6 R5
already prescribed the instrument for this decision: the per-setup attribution
table. That instrument now exists (`funnel:` log line on every scan: fired →
stop-ok → S-qualified, per setup). **Option A + 0.45 was implemented** (the
checklist itself already implied A).
**Measured basis for 0.45:** of 50,217 Setup-2 predicate fires on real feature
rows, the old raw-z score cleared 0.70 on **exactly 1** (median S = 0.270); a
dry day's ceiling is 0.645, not 0.65, because z is negative and was also being
*subtracted*. Under z/3 normalization: Setup-2 fires score p50 = 0.27, p95 =
0.48 → 0.45 admits 8.1%, 0.50 admits only 3.4%; Setup-3 fires clear 0.45 at
72.5%. 0.45 is the data-backed default; the funnel log makes the next
re-freeze an evidence decision, not an analytic one.

### N1. Fill-model gap semantics upgraded beyond the CR (fill at `max(open, limit)` was wrong too)

The review initially prescribed simulating gap-through stops as
`exit = max(open, stop × (1 − buffer))`. That is also not what a GTT does.
**Real Kite GTT OCO semantics, now simulated exactly (3 branches):**

1. Open ≥ limit leg → the sell limit crosses the market at open: fill at the
   open.
2. Open < limit leg but the day's high recovers to it → the triggered order
   sits working: fill **at the limit leg**.
3. Gap beyond the limit leg all day → the stop **does not execute**; the
   position is carried (`GTT_STOP_UNFILLED` event) and the operator's stall
   (day T+2) / time (day 5) rules exit it at close. The stop stays armed.

Golden tests cover all three branches, the recorded-limit override, the
breakeven-relative limit leg (limit moves WITH the standing stop), and the
survives-to-next-session case. The limit leg derives from
`risk.gtt_stop_limit_buffer` inside the fill model, so a config change moves
live and backtest together — they cannot drift by construction.

### N2. Data-hygiene audit added (the gap distribution had to be cleaned first)

Sizing the 1.5% buffer exposed **1,360 overnight closes beyond ±20%** —
impossible under NSE bands. They are corporate-action artifacts (gold-ETF unit
splits ~1:100: SETFGOLD, IVZINGOLD, LICMFGOLD; demergers: KESORAMIND, SBC) that
polluted naive gap statistics. `scripts/check_market_invariants.py` now audits
delivery ≤ volume, ±20% gaps and price sanity after every sync (exit 1 on
violations). The buffer sizing above uses the cleaned distribution. Corporate
actions are applied backward to bars 2010+ in a later ingest; until then the
audit documents exactly which rows are artifacts instead of silently averaging
them away.

### Why not the CR's Issue-5 doc-only fix either

Issue 5 asked for documentation of the 10:00 AM vs 9:15 AM proxy. Half
implemented as specified (architecture doc note pending), half implemented as
behavior: the sheet's playbook now opens with the invalidation rule ("LTP > Max
Entry → signal INVALIDATED") because an instruction the operator reads every
morning beats a paragraph they read once.

---

## 3. Developer Implementation Checklist

**Status: all phases implemented 2026-09-18.** Deviations from the original
checklist are annotated inline and justified in §2bis.

### Phase A: Feature Engine Lookback Enhancements (`src/nse_cash/setups/features.py`)
- [x] **A.1** `src/nse_cash/core/constants.py`: added `ACCUMULATION_LOOKBACK_DAYS = 5`,
  `ICEBERG_LOOKBACK_DAYS = 3`, `BREAKOUT_RETEST_AGE_MIN/MAX = 3/7`,
  `BREAKOUT_CONFIRM_MULT = 1.02`, `DELIVERY_Z_NORM_MAX = 3.0`, and
  `GTT_STOP_LIMIT_BUFFER = 0.015` — the CR's `GTT_LIMIT_SLIPPAGE_BUFFER` name was
  replaced by ONE buffer constant used everywhere (see §2bis R1).
  `FEATURE_COLS` extended with `shock_a_5d`, `z15_count_3d`, `breakout_anchor_90`,
  `breakout_age` (the anchor columns are the corrected Issue-2 design; §2bis R2).
- [x] **A.2** `features.py::_add_symbol_features`: rolling accumulation flags and
  the frozen breakout anchor computed exactly as probed; PIT-honest (NULL until
  the feature's own lookback is satisfiable, so NULL never matches). Schema drift
  on the `features` table is healed by the existing drop-and-rebuild path
  (`storage._heal_features_schema`, now unit-tested); the production table rebuilt
  itself on first open (241,046 rows, latest session 2026-09-17).

### Phase B: Setup Predicate Corrections (`src/nse_cash/setups/catalog.py`)
- [x] **B.1** `evaluate_setup1_vcp_squeeze` reads the rolling flags
  (`shock_a_5d >= 1` OR `z15_count_3d >= 2`); day-T dry-up, squeeze gates and the
  stop logic unchanged. Measured fire-rate: 202,834 windows (8.55%) vs 102 before.
- [x] **B.2** `evaluate_setup4_anchor_retest` anchors on the FROZEN pre-breakout
  ceiling `breakout_anchor_90` with `breakout_age` gated to 3–7 — NOT the rolling
  `base_high_90` this CR prescribed (§2bis R2). Retest ±0.8%, hold-above,
  dry-up ≤ 0.55×, ≥ 40% rejection shadow and the 2.00% stop gate all retained.
  Measured: 2,234 full fires (1.52% of windows); 6,547 retest+hold pre-dry-gate.

### Phase C: Conviction Score Normalization (`src/nse_cash/setups/ranking.py`)
- [x] **C.1** `compute_s_runner` normalizes `z_norm = clamp(z, 0, 3) / 3`; all
  three terms on [0, 1]. `S_RUNNER_MIN` = **0.45** with the measured justification
  in the constant's comment (Option B rejected — §2bis R3). `evaluate_and_rank`
  now logs per-setup funnel counts (fired → stop-ok → S-qualified) on every scan:
  the Phase 6 R5 attribution instrument, so future threshold changes are
  evidence-driven.

### Phase D: Kite GTT Buffer Rendering (`src/nse_cash/execution/action_sheet.py`)
- [x] **D.1** Sheet renders **Stop Trigger** and **Stop Limit** columns; the
  playbook explains trigger-vs-limit and carries the Issue-5 invalidation line.
  Levels live on `CandidateSignal.stop_trigger/stop_limit` (buffered from config
  at signal creation), the position book shows a live Stop Limit column,
  `--record-fill` echoes the exact GTT inputs, corporate-action warnings and the
  breakeven-modify prompt carry the limit leg, and the ledger `trades` table
  persists `stop_limit` (ALTER-migrated for existing books). **Beyond this CR:**
  `fill_model.py` now simulates real GTT semantics — three-branch gap-through
  (fill at open / fill at limit on recovery / unfilled-and-carried) — with golden
  tests (§2bis N1).

### Phase E: Unit Tests & Regression Verification
- [x] **E.1** `test_setups.py`: Setup 1 triggers from a shock 3 sessions prior;
  Setup 4 tests assert the frozen anchor + 3–7 window and explicitly reject the
  rolling-`base_high_90` design. New feature tests for all four columns plus a
  features-schema-drift heal test.
- [x] **E.2** Ranking tests: normalized-z formula values, dry-day-no-penalty proof
  (0.65 ceiling), trigger/limit presence on every candidate, multi-setup
  candidacy (Setup 2 + Setup 5 both qualify).
- [x] **E.3** Full suite: **219 passed** (was 208; 11 new tests added, several
  strengthened). New data-hygiene audit: `scripts/check_market_invariants.py`
  (delivery ≤ volume, ±20% overnight-gap artifacts, price sanity).

---

## 4. Acceptance Criteria & Definition of Done

Original criterion 2 prescribed the rolling anchor and criterion 3 was
unmeasurable as written; both were replaced by the measurable forms below
(rationale in §2bis).

1. **Setup 1 Determinism:** ✅ `evaluate_setup1_vcp_squeeze` triggers when the
   shock is in the rolling 5-session window and day T is the dry squeeze bar
   (unit-tested; measured fire-rate 8.55% of windows pre-gate).
2. **Setup 4 Ceiling Check (corrected):** ✅ `evaluate_setup4_anchor_retest`
   evaluates against the FROZEN pre-breakout ceiling (`breakout_anchor_90`),
   not `base_low_90` and not the rolling `base_high_90`; a unit test asserts the
   predicate returns None when only the rolling high is present.
3. **Multi-Setup Candidacy (measurable form):** ✅ The per-setup funnel log
   (`funnel: <setup>: N fired -> M stop-ok -> K S>=0.45`) now reports every
   stage on every scan, and a unit test proves Setup 2 and Setup 5 both qualify
   under the normalized score. Real-DB validation on 2026-09-17: Setup 2 fired
   14 (all stop-ok, none cleared the bar that day), Setup 5 fired 5 (all
   rejected by its own stop gate) — the funnel shows exactly which stage, per
   setup, per day.
4. **Action Sheet Completeness:** ✅ Sheet, book, record-fill echo, corporate
   action warnings and breakeven prompts all render Stop Trigger + Stop Limit,
   tick-rounded; the limit is derived from the standing stop (structural or
   breakeven) so the buffer is relative.
5. **Zero Test Regressions:** ✅ 219/219 pass, including new golden tests for the
   three-branch GTT gap semantics.
6. **Data hygiene (added):** ✅ `scripts/check_market_invariants.py` audits
   delivery≤volume, ±20% overnight gaps (corporate-action artifacts) and price
   sanity; run after any sync.

---

## 5. Post-Implementation Notes

- The live-book engine and the fill model share one GTT buffer source
  (`risk.gtt_stop_limit_buffer`), so a config change moves the sheet, the book,
  the ledger echo and the backtest together.
- The `features` table is a derived cache: after this change it rebuilt itself
  on first open. No operator action required; backfill on demand via sync.
- Next milestone (unchanged from the original plan): in-sample backtest over
  2023–2026, review the funnel log per setup, then freeze (or re-freeze) the
  0.45 threshold on attribution data — not on theory.
