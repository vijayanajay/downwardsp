# Quantitative Swing Algorithm Library (algos.md)

## NSE High-Conviction Cash Swing Setups (Institutional Grade)

* **Document Version:** 4.1 (CR-2026-001 sync: normalized S_runner, frozen Setup-4 anchor, GTT stop-limit buffer)
* **Target Asset Class:** Indian Equities (NSE Cash Segment / `EQ` Series Only)
* **Execution Timing:** **10:00 AM IST** (Post-Opening Price Discovery & Spread Stabilization; Manual Execution via Dual-GTT OCO)
* **Holding Horizon:** **2 to 5 Trading Days**
* **Exit Architecture:** **Asymmetric 2-Tranche Model** (50% at Base Target $\rightarrow$ Breakeven Stop at EOD; 50% Trailing Runner)
* **Risk Model:** Pre-Entry Structural Stop Gate ($\le \mathbf{-2.20\%}$) + 48-Hour Time-Decay Stall Protection (Exit at $T+2$ if gain $< +0.80\%$)
* **Catalog Architecture:** **5 Modular, Orthogonal Microstructure Setups** (Zero Machine Learning / Black Boxes; 100% Free Official Bhavcopy & MTO Data)

---

## 1. Algorithmic Design Philosophy & Asymmetric Runner Identification

### 1.1 The Kailash Nadh Engineering Mindset: Can We Predict Stocks That Go $>+2\%$ in 5 Days?
* **The Honest Truth:** No statistical model or neural network can guarantee with 100% certainty which individual stock will explode for $+6\%$ vs stall at $+0.5\%$. Anyone claiming certain prediction in financial markets is selling snake oil.
* **The Quantitative Reality:** You **can** mathematically identify stocks with **Asymmetric Right-Tail Skew (High Probability of Fat Outsized Moves)**.
* In Indian cash equities, multi-day explosive impulses ($+3\%$ to $+8\%$ in 3–5 days) occur due to physical market microstructure dynamics:
  1. **Locked Demat Float Squeeze:** Deliverable quantity spikes ($Z \ge +2.0\sigma$) while intraday range compresses. When floating supply is locked in demat, any subsequent buying pressure forces an outsized percentage surge.
  2. **Zero Overhead Resistance:** Stocks breaking out of multi-month bases or within 1.5% of 52-week Highs have zero trapped bagholders looking to sell at breakeven.
  3. **High-Conviction Support Defense:** Quality leaders in secular uptrends retesting prior multi-month breakout bases on dried-up volume offer asymmetrical risk-to-reward.
  4. **Beta-Neutral Idiosyncratic Momentum:** Stocks exhibiting strong residual alpha ($\text{iMOM} > 95\text{th percentile}$) that move independently of Nifty choppiness.

### 1.2 Mathematical Formulation: Asymmetric Runner Skew Score ($S_{\text{runner}}$)
Every qualifying candidate at Stage 3 is scored using the composite runner formula:

$$S_{\text{runner}} = 0.35 \times \frac{\max(0, Z_{\text{Delivery}})}{3} + 0.35 \times \text{iMOM}_{\text{Percentile}} + 0.30 \times (1 - \text{PV}_{\text{Percentile}})$$

* Where:
  * $Z_{\text{Delivery}}$: 20-day standardized delivery volume shock, winsorized at $+3\sigma$ and normalized to $[0, 1]$ (CR-2026-001: a dry-up day has $Z \le 0$; the normalized term contributes 0 instead of a penalty).
  * $\text{iMOM}_{\text{Percentile}}$: 36-day residual momentum percentile in Top 500 universe ($0.0 \text{ to } 1.0$).
  * $\text{PV}_{\text{Percentile}}$: 5-day Parkinson volatility percentile relative to 60-day history (lower is better, range compression).
* Candidates with $S_{\text{runner}} \ge \mathbf{0.45}$ are prioritized for 10:00 AM execution. (CR-2026-001: the original 0.70 bar mathematically precluded every dry-volume setup — measured: of 50,217 Setup-2 predicate fires, exactly 1 cleared it. 0.45 admits ~8.1% of Setup-2 fires and 72.5% of Setup-3 fires on 593k real feature rows. Every scan logs per-setup funnel counts so the next re-freeze is an evidence decision.)

```
                             THE 5-SETUP ORTHOGONAL CATALOG
                                            │
      ┌─────────────────────────────────────┼─────────────────────────────────────┐
      ▼                                     ▼                                     ▼
┌──────────────────┐               ┌──────────────────┐                  ┌──────────────────┐
│ FLOAT SQUEEZE &  │               │ HIGH-PROBABILITY │                  │ MOMENTUM, RS &   │
│ CONTRACTION      │               │ MEAN REVERSION   │                  │ ANCHOR RETESTS   │
├──────────────────┤               ├──────────────────┤                  ├──────────────────┤
│ Setup 1: Delivery│               │ Setup 2: Secular │                  │ Setup 3: RS Base │
│          VCP &   │               │          Rubber- │                  │ Setup 4: Anchor  │
│          Parkin. │               │          Band    │                  │          Retest  │
│          Squeeze │               │                  │                  │ Setup 5: Resid.  │
│                  │               │                  │                  │          MOM     │
└──────────────────┘               └──────────────────┘                  └──────────────────┘
```

---

## 2. Core Quantitative Setups Catalog (The 5 Orthogonal Setups)

---

### Setup 1: Delivery Absorption & Volatility Contraction (VCP + Parkinson Squeeze)

#### 1. Rationale & Microstructure
Institutional accumulation on NSE leaves an undeniable physical footprint: high deliverable quantity over recent sessions (iceberg accumulation) followed by an abrupt volume dry-up and severe intraday range contraction. With floating supply locked in demat accounts, even modest incremental buying pressure causes an outsized $+2.0\%$ to $+6.0\%$ move over 3 to 5 days.

#### 2. Mathematical Rules
1. **Secular Trend Alignment:**
   $$\text{Close}_t > \text{SMA}_{200}(\text{Close}) \quad \text{AND} \quad \text{SMA}_{50}(\text{Close}) > \text{SMA}_{200}(\text{Close})$$
2. **Institutional Accumulation Footprint:**
   * **Condition A (Single Shock):** Within the last 5 sessions, at least 1 day recorded:
     $$\text{Delivery\_Qty} \ge 2.20 \times \text{SMA}_{20}(\text{Delivery\_Qty}) \quad \text{AND} \quad \text{Close} > \text{Open}$$
   * **OR Condition B (Multi-Day Passive Iceberg):** Standardized delivery volume shock $\text{Delivery\_Z} \ge +1.50$ for at least 2 of the last 3 sessions.
3. **Supply Exhaustion (Volume Dry-Up):** Day $T$ traded volume drops to:
   $$\text{Volume}_T \le 0.65 \times \text{SMA}_{20}(\text{Volume})$$
4. **Intraday Volatility Compression (Dual-Engine):**
   * Narrow daily candle: $\frac{\text{High}_T - \text{Low}_T}{\text{Close}_T} \le 0.015 \quad (\le 1.50\% \text{ Intraday Range})$
   * **OR** 5-day Parkinson Volatility in lowest 15th percentile of 60-day distribution:
     $$\text{PV}_5(t) = \sqrt{ \frac{1}{4 \ln 2 \cdot 5} \sum_{k=0}^{4} \left( \ln \frac{\text{High}_{t-k}}{\text{Low}_{t-k}} \right)^2 } \le \text{Percentile}_{15}(\text{PV}_{60})$$

#### 3. Execution & Dual-GTT Parameters
* **Entry:** Day $T+1$ at **10:00 AM IST** (ensuring price is holding between Day $T$ Close and Close $+ 1.2\%$).
* **Pre-Entry Risk Gate:** Structural Stop strictly $\le 2.20\%$.
* **GTT 1 (Tranche 1 - 50% Qty):** Target $+2.00\%$ | Stop $\le -2.20\%$.
* **GTT 2 (Tranche 2 - 50% Qty):** Target $+5.00\%$ to $+6.00\%$ (or Trailing Low) | Stop $\le -2.20\%$.
* **48-Hour Stall Exit:** If price gain $< +0.80\%$ by Day $T+2$ at 3:15 PM $\rightarrow$ Cancel GTTs & Exit at Market.

---

### Setup 2: Secular Uptrend Rubber-Band Pullback (Mean Reversion)

#### 1. Rationale & Microstructure
High-quality, liquid large/mid-cap leaders (`Close > 200 SMA`) in structural bull trends rarely decline for 3 consecutive sessions without triggering an institutional dip-buying reflex. When the pullback occurs on low delivery volume, it indicates routine retail exhaustion rather than institutional liquidation.

#### 2. Mathematical Rules
1. **Secular Trend Filter:**
   $$\text{Close}_t > \text{SMA}_{200}(\text{Close}) \quad \text{AND} \quad \text{Slope}(\text{SMA}_{200}) > 0$$
2. **Consecutive Pullback:** Stock has closed lower for **3 consecutive sessions**:
   $$\text{Close}_T < \text{Close}_{T-1} < \text{Close}_{T-2}$$
3. **No Institutional Dumping:** The pullback occurred on subdued delivery volume:
   $$\text{Delivery\_Qty}_T \le 1.15 \times \text{SMA}_{20}(\text{Delivery\_Qty})$$
4. **Extreme Short-Term Exhaustion:**
   $$\text{RSI}(2)_T \le 10.0$$

#### 3. Execution & Dual-GTT Parameters
* **Entry:** Day $T+1$ at **10:00 AM IST** if 10:00 AM price $> \text{Low}(9:15-9:30\text{ AM})$.
* **Pre-Entry Risk Gate:** Structural Stop strictly $\le 2.20\%$.
* **GTT 1 (Tranche 1 - 50% Qty):** Target $+1.80\%$ | Stop $\le -2.20\%$.
* **GTT 2 (Tranche 2 - 50% Qty):** Target $+3.50\%$ | Stop $\le -2.20\%$.
* **48-Hour Stall Exit:** Exit on Day $T+2$ at 3:15 PM if no bounce occurs (gain $< +0.80\%$).

---

### Setup 3: Cross-Sectional Relative Strength (RS) Base Consolidation

#### 1. Rationale & Microstructure
Stocks outperforming 95% of the NIFTY 500 universe during broad market consolidation are backed by strong domestic institutional sponsorship. When broader market indices stabilize, these relative strength leaders break out with zero overhead trapped supply.

#### 2. Mathematical Rules
1. **Macro Filter:** NIFTY 50 `Close > 20-day EMA`.
2. **Mansfield Relative Strength Ranking:**
   $$\text{RS}_i(t) = \left( \frac{\text{Price}_i(t) / \text{NIFTY500}(t)}{\text{SMA}_{50}(\text{Price}_i / \text{NIFTY500})} - 1 \right) \times 100$$
   * Must rank in the **Top 5th Percentile** of the Top 500 liquid universe.
3. **Base Consolidation:** Traded within a tight $\le 3.0\%$ range over the preceding 5 sessions, situated within $1.5\%$ of 52-week High.

#### 3. Execution & Dual-GTT Parameters
* **Entry:** Day $T+1$ at **10:00 AM IST**.
* **Pre-Entry Risk Gate:** Structural Stop $\le 2.20\%$.
* **GTT 1 (Tranche 1 - 50% Qty):** Target $+2.00\%$ | Stop $\le -2.20\%$.
* **GTT 2 (Tranche 2 - 50% Qty):** Target $+6.00\%$ (Trailing Low) | Stop $\le -2.20\%$.
* **48-Hour Stall Exit:** Exit on Day $T+2$ at 3:15 PM if gain $< +0.80\%$.

---

### Setup 4: Multi-Month Base Breakout & Low-Volume Retest (The Anchor Retest)

#### 1. Rationale & Microstructure
When an established quality liquid stock breaks out of a 3-to-6 month horizontal consolidation on heavy delivery and subsequently pulls back for 1 to 2 sessions on dried-up volume to retest the previous resistance-turned-support level, institutions aggressively defend this zone. This offers the cleanest mathematical risk-to-reward because the invalidation anchor is directly below entry.

#### 2. Mathematical Rules
1. **Base Breakout Confirmation (within last 3 to 7 sessions):** Stock closed above $1.02 \times$ its prior 90-session resistance ceiling (CR-2026-001: the anchor is the PRE-breakout ceiling, FROZEN at the breakout day — the rolling 90-day high includes the rally bars and would test retests of the rally peak, measured +3.5% above the true ceiling by day 1). The retest happens 3 to 7 sessions after that breakout.
2. **Support Retest:** Day $T$ Low touches within $\pm 0.8\%$ of the frozen breakout level and holds above it.
3. **Volume Dry-Up on Retest:**
   $$\text{Volume}_T \le 0.55 \times \text{SMA}_{20}(\text{Volume})$$
4. **Intraday Rejection Tail:** $\text{Close}_T > \text{Open}_T$ and lower shadow $\ge 40\%$ of daily candle range.

#### 3. Execution & Dual-GTT Parameters
* **Entry:** Day $T+1$ at **10:00 AM IST**.
* **Pre-Entry Risk Gate:** Breakout Support Level must be $\le 2.00\%$ below 10:00 AM entry.
* **GTT 1 (Tranche 1 - 50% Qty):** Target $+2.00\%$ | Stop $\le -2.00\%$.
* **GTT 2 (Tranche 2 - 50% Qty):** Target $+6.00\%$ | Stop $\le -2.00\%$.
* **48-Hour Stall Exit:** Active on Day $T+2$ at 3:15 PM.

---

### Setup 5: Cross-Sectional Residual / Idiosyncratic Momentum (Blitz Factor)

#### 1. Rationale & Microstructure
Broad market beta accounts for 60–70% of a stock's daily variance. Stripping out beta via a rolling 36-day OLS regression against NIFTY 50 isolates pure idiosyncratic stock-specific alpha ($\epsilon_i$). Stocks with top residual momentum and institutional delivery shocks drift $+2\%$ to $+6\%$ unhindered by broader index chop.

#### 2. Mathematical Rules
1. **Rolling Idiosyncratic Regression (36 Days):**
   $$R_i(t) = \alpha_i + \beta_i R_{\text{NIFTY}}(t) + \epsilon_i(t)$$
2. **Idiosyncratic Momentum Score:**
   $$\text{iMOM}_i = \frac{\sum_{t=0}^{20} \epsilon_i(t)}{\sigma_{36}(\epsilon_i)} \quad \text{in Top 5th percentile of Top 500 Universe}$$
3. **Delivery Volume Shock:**
   $$\text{Delivery\_Qty}_T \ge 2.0 \times \text{SMA}_{20}(\text{Delivery\_Qty}) \quad \text{AND} \quad \text{Close}_T > \text{Open}_T$$

#### 3. Execution & Dual-GTT Parameters
* **Entry:** Day $T+1$ at **10:00 AM IST**.
* **Pre-Entry Risk Gate:** Structural Stop $\le 2.20\%$.
* **GTT 1 (Tranche 1 - 50% Qty):** Target $+2.00\%$ | Stop $\le -2.20\%$.
* **GTT 2 (Tranche 2 - 50% Qty):** Target $+6.00\%$ | Stop $\le -2.20\%$.
* **48-Hour Stall Exit:** Active on Day $T+2$ at 3:15 PM.

---

## 3. Master Algorithmic Comparison Matrix (Kailash Pragmatic Standard)

| Setup ID | Setup Name | Microstructure Anomaly | Tranche 1 (50%) | Tranche 2 (50%) | Max Stop Gate | 48h Stall Rule | Realistic Win Rate |
|---|---|---|---|---|---|---|---|
| **Setup 1** | Delivery VCP & Parkinson Squeeze | Demat float lock & range compression | +2.00% | Trailing / +5% to +6% | -2.20% | Active | 52% - 58% |
| **Setup 2** | Secular Rubber-Band Pullback | 200-SMA leader dip mean reversion | +1.80% | +3.50% | -2.20% | Active | 54% - 60% |
| **Setup 3** | Cross-Sectional RS Base | High RS breakout near 52-week High | +2.00% | Trailing / +6% | -2.20% | Active | 50% - 56% |
| **Setup 4** | Anchor Base Retest | Multi-month breakout retest on dry volume | +2.00% | Trailing / +6% | -2.00% | Active | 53% - 59% |
| **Setup 5** | Residual Momentum Drift | Beta-neutral idiosyncratic alpha shock | +2.00% | Trailing / +6% | -2.20% | Active | 50% - 56% |

---

## 4. Practical Extension & Validation Protocol for New Algorithms

To maintain a minimalist, institutional-grade catalog:
1. **Empirical Microstructure Grounding:** Must be based on structural supply/demand, demat float absorption, or institutional execution realities (never lagging indicator-only crossovers or neural network black boxes).
2. **Backtestable on Free Official Data:** Must rely exclusively on official NSE Bhavcopy, MTO delivery files, and Corporate Actions archives.
3. **Execution at 10:00 AM IST:** Must execute after morning price discovery with the Morning Gap-Up filter ($\le \text{Close}_T + 1.2\%$) using the Dual-GTT OCO manual playbook.
4. **Friction & STCG Accounting:** Must maintain positive mathematical expectancy after subtracting **0.35% round-trip friction** and **20.0% STCG tax**.
