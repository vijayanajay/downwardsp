# Change Request: CR-2026-001
## Core Trading Engine & Microstructure Realignment: Setups 1 & 4 Fixes, Per-Setup Ranking Normalization, GTT Limit Buffers & Execution Fidelity

* **CR Number:** `CR-2026-001`
* **Status:** `READY_FOR_IMPLEMENTATION`
* **Created Date:** 2026-09-18
* **Author / Reviewer:** Kailash Nadh Pragmatic Review
* **Target Version:** Post-Phase 7 / Phase 8 Readiness
* **Affected Components:**
  * `src/nse_cash/setups/features.py` (Feature engine: rolling accumulation & breakout lookbacks)
  * `src/nse_cash/setups/catalog.py` (Setup predicates: Setup 1 physical conflict, Setup 4 base anchor)
  * `src/nse_cash/setups/ranking.py` (Conviction scoring: orthogonal normalization across 5 setups)
  * `src/nse_cash/execution/action_sheet.py` (Kite Dual-GTT order sheet: trigger vs limit price buffers)
  * `src/nse_cash/backtest/fill_model.py` (Execution fidelity & gap realization documentation)
  * `tests/unit/test_setups.py`, `tests/unit/test_ranking.py`, `tests/unit/test_fill_model.py`

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

## 3. Developer Implementation Checklist

Follow this exact step-by-step checklist to implement the changes across the codebase.

### Phase A: Feature Engine Lookback Enhancements (`src/nse_cash/setups/features.py`)
- [ ] **A.1** Open `src/nse_cash/core/constants.py`:
  - [ ] Add constant `ACCUMULATION_LOOKBACK_DAYS = 5`.
  - [ ] Add constant `GTT_LIMIT_SLIPPAGE_BUFFER = 0.015`.
  - [ ] Update `FEATURE_COLS` list to include `"shock_a_5d"` and `"z15_count_3d"`.
- [ ] **A.2** Open `src/nse_cash/setups/features.py`:
  - [ ] In `_add_symbol_features(df: pd.DataFrame)`:
    - [ ] Calculate `shock_a_series`: `((dlv >= 2.20 * out["sma20_delivery"]) & (close > out["open_adj"])).astype(float)`.
    - [ ] Add column `out["shock_a_5d"] = shock_a_series.rolling(5, min_periods=1).max()`.
    - [ ] Calculate `z15_series`: `(out["delivery_z"] >= 1.50).astype(float)`.
    - [ ] Add column `out["z15_count_3d"] = z15_series.rolling(3, min_periods=1).sum()`.
  - [ ] In `compute_features`, ensure the new columns are preserved in `keep` and written to DuckDB.

### Phase B: Setup Predicate Corrections (`src/nse_cash/setups/catalog.py`)
- [ ] **B.1** In `evaluate_setup1_vcp_squeeze`:
  - [ ] Replace single-day delivery shock checks (`shock_a`, `shock_b`) with:
    ```python
    shock_a = (_v(row, "shock_a_5d") or 0.0) >= 1.0
    shock_b = (_v(row, "z15_count_3d") or 0.0) >= 2.0
    if not (shock_a or shock_b):
        return None
    ```
  - [ ] Retain Day $T$ volume dry-up: `vol <= 0.65 * sma20v`.
  - [ ] Retain narrow candle / Parkinson squeeze and structural stop.
- [ ] **B.2** In `evaluate_setup4_anchor_retest`:
  - [ ] Replace `base_low = _v(row, "base_low_90")` with `base_high = _v(row, "base_high_90")`.
  - [ ] Set `breakout_level = base_high`.
  - [ ] Ensure `close >= breakout_level * 0.995` and `low >= breakout_level * 0.990`.
  - [ ] Retain volume dry-up (`vol <= 0.55 * sma20v`) and rejection lower shadow ($\ge 40\%$).
  - [ ] Set `structural_stop = round(min(breakout_level, low) * 0.998, 2)`.

### Phase C: Conviction Score Normalization (`src/nse_cash/setups/ranking.py`)
- [ ] **C.1** In `src/nse_cash/setups/ranking.py`:
  - [ ] Update `compute_s_runner`:
    ```python
    def compute_s_runner(delivery_z: float, imom_percentile: float,
                         pv_percentile: float) -> float:
        w_z, w_m, w_pv = S_RUNNER_WEIGHTS
        # Normalize delivery_z from [0, 3] to [0, 1]
        z_raw = 0.0 if delivery_z is None or pd.isna(delivery_z) else float(delivery_z)
        z_norm = max(0.0, min(3.0, z_raw)) / 3.0
        m = 0.0 if imom_percentile is None or pd.isna(imom_percentile) else float(imom_percentile)
        pv = 1.0 if pv_percentile is None or pd.isna(pv_percentile) else float(pv_percentile)
        return w_z * z_norm + w_m * m + w_pv * (1.0 - pv)
    ```
  - [ ] Adjust `S_RUNNER_MIN` in `constants.py` from `0.70` to `0.45` to account for normalized scales across orthogonal setups.
  - [ ] In `evaluate_and_rank`, ensure Setup 2 and Setup 4 candidates can qualify and are evaluated fairly.

### Phase D: Kite GTT Buffer Rendering (`src/nse_cash/execution/action_sheet.py`)
- [ ] **D.1** In `src/nse_cash/execution/action_sheet.py`:
  - [ ] In table rendering, add two distinct stop fields for Kite:
    * `Stop Trigger Price`: `c.structural_stop` (rounded to ₹0.05).
    * `Stop Limit Price`: `round_to_tick(c.structural_stop * (1.0 - GTT_LIMIT_SLIPPAGE_BUFFER))` (rounded to ₹0.05).
  - [ ] In the manual playbook notes section, add explicit instruction:
    * *"Kite GTT OCO Stop Sell: Enter Trigger Price as indicated, and Limit Price with the 1.5% buffer to prevent unexecuted orders during opening gap-downs."*

### Phase E: Unit Tests & Regression Verification
- [ ] **E.1** Update `tests/unit/test_setups.py`:
  - [ ] Update `test_setup1_vcp_squeeze_triggers` to provide synthetic data containing a delivery shock 2 sessions prior and Day $T$ volume dry-up.
  - [ ] Update `test_setup4_anchor_retest_triggers` to verify retest of `base_high_90`.
- [ ] **E.2** Update `tests/unit/test_ranking.py`:
  - [ ] Verify `compute_s_runner` with normalized $Z$.
  - [ ] Verify candidates across Setups 1, 2, 3, 4, 5 can pass ranking thresholds.
- [ ] **E.3** Run complete test suite:
  ```bash
  pytest tests/ -v
  ```
  Ensure 100% passing tests.

---

## 4. Acceptance Criteria & Definition of Done

1. **Setup 1 Determinism:** `evaluate_setup1_vcp_squeeze` passes on test data where delivery shock occurred within the last 5 sessions and Day $T$ is a dry squeeze bar.
2. **Setup 4 Ceiling Check:** `evaluate_setup4_anchor_retest` evaluates against `base_high_90` rather than `base_low_90`.
3. **Multi-Setup Candidacy:** A multi-setup scan on historical data with diverse market patterns produces qualifying candidates across Setups 1, 2, 3, 4, and 5—not solely Setup 5.
4. **Action Sheet Completeness:** The Action Sheet outputs exact Kite GTT fields including Stop Trigger and Stop Limit prices with tick rounding.
5. **Zero Test Regressions:** All unit and integration tests pass without errors.
