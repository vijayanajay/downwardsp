# 8-Phase Master Action Plan: NSE High-Conviction Cash Swing Trading System
**Production Engineering Blueprint — Kailash Nadh Pragmatic Architecture Edition**

* **Document Version:** 1.0 (Master Execution Roadmap)
* **Authoritative References:** [BRD_NSE_High_Conviction_Cash_Swing_System.md](file:///d:/Code/downwardsp/docs/BRD_NSE_High_Conviction_Cash_Swing_System.md) | [algos.md](file:///d:/Code/downwardsp/docs/algos.md)
* **Core Stack:** Python 3.11+, DuckDB, SQLite, Parquet, Polars/Pandas, Click, Rich, Pytest
* **Execution Paradigm:** Local-First, Zero Paid APIs, Deterministic Microstructure Signals, Manual Dual-GTT Execution on Zerodha Kite

---

## Engineering Philosophy & Principles (The Kailash Nadh Mindset)

1. **Radical Simplicity & Zero Bloat:**
   - No unnecessary cloud dependencies, no microservices overhead, no distributed clusters when a single-process local Python engine with Parquet + DuckDB handles 15+ years of daily equity data in milliseconds.
   - Zero black-box neural networks, no PyTorch/TensorFlow, and no edge-distorted wavelet transforms. Only deterministic, auditable microstructure metrics (Demat float absorption, Parkinson volatility compression, relative strength, resistance retests, and residual alpha).
2. **Elimination of the Win-Rate Trap (Asymmetric 2-Tranche Model):**
   - Tranche 1 (50% Qty): Exits at $+2.0\%$ gross target to lock in profit, cover all transaction costs, and move Tranche 2 stop to Breakeven ($0.0\%$) at EOD.
   - Tranche 2 (50% Qty): Trailing runner capturing $+4.0\%$ to $+8.0\%$ outsized institutional impulses.
3. **48-Hour Stall Defense:**
   - Systematic time-decay exit: If an impulse fails to gain $\ge +0.80\%$ by Day $T+2$ at 3:15 PM, exit at market, converting potential $-2.2\%$ stop losses into small scratch trades.
4. **First-Class Transaction Costs & STCG Accounting:**
   - Exact round-trip friction (~0.35%) including STT (0.20%), NSE turnover (0.0069%), SEBI charges (0.0002%), Stamp Duty (0.015%), GST (18%), CDSL DP charges (₹15.93 flat per tranche sell), and execution slippage (0.10%).
   - Flat 20.0% STCG tax applied across all historical backtest years.
5. **Pragmatic Manual Execution Boundary:**
   - Broker API automated bots (token refreshes, websocket loops, order placement APIs) are explicitly out of scope. The system acts as a deterministic intelligence engine generating an unambiguous 10:00 AM Daily Trading Action Sheet for manual Dual-GTT OCO placement on Zerodha Kite.

---

## Master Architecture & Phase Progression

```
+----------------------------------------------------------------------------------------------------+
|                                      8-PHASE EXECUTION ROADMAP                                     |
+----------------------------------------------------------------------------------------------------+
| PHASE 1: Project Scaffolding, Core Architecture & Config Engine                                    |
|          Setup modular layout, Pydantic configuration, Rich logging, and CLI skeleton.             |
|                                                                                                    |
| PHASE 2: Free NSE Data Ingestion, Scraping & Local Storage Engine                                  |
|          Bhavcopy, MTO delivery, Corporate Actions, HTTP client, Parquet + DuckDB storage.         |
|                                                                                                    |
| PHASE 3: Corporate Action Adjustment Engine & Dynamic PIT Universe Engine                         |
|          Backward split/bonus calibration, rolling 90-day ADTV >= 5Cr, ASM/GSM & circuit filters.   |
|                                                                                                    |
| PHASE 4: Market Regime Engine & Sector Diversification Gate (Funnel Stages 1, 2, 4)                |
|          NIFTY 20EMA, 500 Breadth >50%, 1 trade/sector limit, 4-slot portfolio capacity gate.     |
|                                                                                                    |
| PHASE 5: Quantitative Setups Catalog & Asymmetric Runner Skew Scoring (S_runner)                   |
|          5 orthogonal setups, feature library, composite S_runner formula, pre-entry 2.2% gate.    |
|                                                                                                    |
| PHASE 6: High-Fidelity Backtesting & Simulation Engine                                             |
|          13-yr in-sample (2010-2022) + 3.5-yr walk-forward (2023-Present), 2 tranches, STCG, stall.  |
|                                                                                                    |
| PHASE 7: Portfolio Ledger, State Machine & Daily 10:00 AM Action Sheet Generator                   |
|          SQLite trade ledger, 10:00 AM Kite Dual-GTT Action Sheet renderer, EOD stall tracker.     |
|                                                                                                    |
| PHASE 8: Test Suite, Benchmarking, Production Runbook & Developer Guide                            |
|          Unit/E2E test suite, performance validation, operational runbook, architecture guide.     |
+----------------------------------------------------------------------------------------------------+
```

---

## Detailed 8-Phase Action Plan & Baby-Step To-Dos

---

### Phase 1: Project Scaffolding, Core Architecture & Config Engine

#### Objective
Establish a clean, robust, type-annotated codebase structure, configuration schemas, logging framework, and the standalone CLI entrypoint (`nse-cash`).

#### To-Do List

- [x] **1.1 Directory Tree Creation**
  - [x] Create directory `src/nse_cash/core/` (types, config, math, constants, adjustments).
  - [x] Create directory `src/nse_cash/data/` (HTTP fetcher, Bhavcopy parser, MTO delivery parser, Corporate actions, DuckDB/Parquet storage).
  - [x] Create directory `src/nse_cash/funnel/` (Market regime gate, liquidity & governance filter, sector allocator).
  - [x] Create directory `src/nse_cash/setups/` (The 5 quantitative setups, indicator library, $S_{\text{runner}}$ scorer, ranking).
  - [x] Create directory `src/nse_cash/backtest/` (Event-driven engine, trade manager, friction & tax calculator, risk manager, metrics).
  - [x] Create directory `src/nse_cash/execution/` (SQLite ledger, trade state machine, morning scanner, action sheet renderer).
  - [x] Create directory `src/nse_cash/cli/` (Click/Typer CLI commands: `sync`, `scan`, `ledger`, `backtest`, `status`).
  - [x] Create directory `tests/unit/`, `tests/integration/`, `tests/fixtures/`.
  - [x] Create directory `data/raw/`, `data/processed/`, `data/db/`, `reports/`, `logs/`.

- [x] **1.2 Package & Environment Configuration (`pyproject.toml`)**
  - [x] Create `pyproject.toml` with Python $\ge 3.11$.
  - [x] Define dependencies: `polars>=1.0.0`, `pandas>=2.2.0`, `duckdb>=1.0.0`, `pyarrow>=15.0.0`, `numpy>=1.26.0`, `scipy>=1.12.0`, `pydantic>=2.7.0`, `click>=8.1.0`, `rich>=13.7.0`, `requests>=2.31.0`, `tenacity>=8.2.0`, `pytest>=8.0.0`, `pytest-cov>=4.1.0`.
  - [x] Configure CLI entry point: `[project.scripts] nse-cash = "nse_cash.cli.main:cli"`.

- [x] **1.3 Central Configuration Engine (`src/nse_cash/core/config.py`)**
  - [x] Create `config/config.yaml` with default system parameters.
  - [x] Implement Pydantic `SystemConfig` model with strict validation:
    - `capital.base_capital`: float = 500,000.0 (₹5 Lakhs base)
    - `capital.num_slots`: int = 4 (Exactly 4 concurrent positions)
    - `capital.slot_capital`: float = 125,000.0 (₹1.25 Lakhs per slot)
    - `capital.tranche_capital`: float = 62,500.0 (₹62.5k per tranche)
    - `risk.max_structural_stop`: float = 0.022 (Max 2.20% stop gate)
    - `risk.max_gap_entry`: float = 0.012 (+1.20% morning gap-up ceiling)
    - `risk.stall_threshold`: float = 0.008 (+0.80% minimum gain at Day T+2 3:15 PM)
    - `risk.max_holding_days`: int = 5 (Hard Day 5 3:15 PM time exit)
    - `risk.kill_switch_drawdown`: float = 0.075 (7.5% portfolio drawdown triggers 10-day halt)
    - `friction.stt_delivery`: float = 0.001 (0.1% buy + 0.1% sell)
    - `friction.nse_turnover`: float = 0.0000345 (0.00345% buy + sell)
    - `friction.sebi_fee`: float = 0.000001 (0.0001% buy + sell)
    - `friction.stamp_duty`: float = 0.00015 (0.015% buy only)
    - `friction.gst_rate`: float = 0.18 (18% on brokerage & exchange fees)
    - `friction.dp_charge_per_sell`: float = 15.93 (₹15.93 flat per sell day)
    - `friction.slippage_per_side`: float = 0.0005 (0.05% in + 0.05% out = 0.10% round-trip)
    - `friction.stcg_tax_rate`: float = 0.20 (20.0% flat STCG)
    - `paths.raw_dir`: Path = "data/raw"
    - `paths.parquet_dir`: Path = "data/processed"
    - `paths.duckdb_path`: Path = "data/db/nse_market.duckdb"
    - `paths.ledger_db_path`: Path = "data/db/portfolio_ledger.sqlite3"
  - [x] Add YAML loader function `load_config(path: Optional[Path] = None) -> SystemConfig` with environment variable overrides.

- [x] **1.4 High-Performance Structured Logging Framework (`src/nse_cash/core/logger.py`)**
  - [x] Set up Rich Console formatting with distinct log levels (`DEBUG`, `INFO`, `SUCCESS`, `WARNING`, `ERROR`).
  - [x] Set up rotating file log handler writing to `logs/nse_cash.log` (10MB per file, 5 backups).
  - [x] Implement context manager `log_execution_time(task_name: str)` to measure subsystem latency.

- [x] **1.5 Domain Type Definitions & Enums (`src/nse_cash/core/types.py`)**
  - [x] Define `SeriesType` enum (`EQ`, `BE`, `SM`).
  - [x] Define `MarketRegimeState` enum (`OFFENSIVE_LONG`, `DEFENSIVE_CASH`).
  - [x] Define `SetupID` enum (`SETUP_1_VCP`, `SETUP_2_RUBBERBAND`, `SETUP_3_RS_BASE`, `SETUP_4_ANCHOR_RETEST`, `SETUP_5_RESIDUAL_MOM`).
  - [x] Define `TrancheID` enum (`TRANCHE_1_BASE`, `TRANCHE_2_RUNNER`).
  - [x] Define `TrancheState` enum (`PENDING`, `ACTIVE`, `TARGET_HIT`, `STOPPED_OUT`, `STALL_EXITED`, `TIME_EXITED`).
  - [x] Define `ExitReason` enum (`TARGET_1_HIT`, `TARGET_2_HIT`, `TRAILING_STOP_HIT`, `STRUCTURAL_STOP_HIT`, `STALL_48H_HIT`, `TIME_DAY5_HIT`, `KILL_SWITCH`).
  - [x] Define Pydantic dataclasses: `DailyBar`, `DeliveryRecord`, `CorporateAction`, `CandidateSignal`, `TradeOrder`, `PortfolioPosition`, `BacktestMetrics`.

- [x] **1.6 Master CLI Entrypoint Skeleton (`src/nse_cash/cli/main.py`)**
  - [x] Implement Click CLI group with `--version`, `--verbose`, and subcommands:
    - `sync` (Data ingestion & archival)
    - `scan` (4-stage funnel & 10:00 AM Action Sheet)
    - `ledger` (Portfolio state & active GTT tracking)
    - `backtest` (13Y In-Sample + 3.5Y Walk-Forward engine)
    - `status` (System health & data integrity)

---

### Phase 2: Free NSE Data Ingestion, Scraping & Local Storage Engine

#### Objective
Build a robust, self-healing, zero-cost data pipeline that downloads and uncompresses daily official NSE Bhavcopies, Security-wise Delivery reports (MTO), and Corporate Actions directly from NSE archives into DuckDB and Parquet storage.

#### To-Do List

- [x] **2.1 Resilient NSE HTTP Client (`src/nse_cash/data/fetcher.py`)**
  - [x] Implement `NSEHttpClient` using `requests.Session` (or `httpx`) with custom browser headers:
    - `User-Agent`: Modern Chrome/Edge user-agent string
    - `Accept-Encoding`: `gzip, deflate, br`
    - `Accept-Language`: `en-US,en;q=0.9`
  - [x] Implement cookie bootstrapping method `_bootstrap_cookies()` requesting `https://www.nseindia.com/` before hitting protected endpoints.
  - [x] Add exponential backoff retry logic with jitter using `tenacity` (retry on HTTP 403, 429, 500, 502, 503, 504; max 5 attempts).
  - [x] Implement local disk caching for downloaded archives in `data/raw/{YYYY}/{MM}/` to prevent duplicate network hits.

- [x] **2.2 Daily Cash Bhavcopy Ingestion Engine (`src/nse_cash/data/bhavcopy.py`)**
  - [x] URL Pattern: `https://archives.nseindia.com/content/historical/EQUITIES/{YYYY}/{MMM}/cm{DD}{MMM}{YYYY}bhav.csv.zip`.
  - [x] Support format variation fallback (e.g., new NSE PR / UDiFF Bhavcopy reporting structure `sec_bhavdata_full_{DDMMYYYY}.csv` if primary URL differs).
  - [x] In-memory decompression of zip files via `zipfile.ZipFile`.
  - [x] Parse CSV via Polars/Pandas:
    - Filter: `SERIES == 'EQ'` (strictly cash equity).
    - Map columns: `SYMBOL` $\rightarrow$ `symbol`, `OPEN` $\rightarrow$ `open`, `HIGH` $\rightarrow$ `high`, `LOW` $\rightarrow$ `low`, `CLOSE` $\rightarrow$ `close`, `LAST` $\rightarrow$ `last`, `TOTTRDQTY` $\rightarrow$ `volume`, `TOTTRDVAL` $\rightarrow$ `turnover`, `TIMESTAMP` $\rightarrow$ `date`.
    - Type casting: Float for prices, Int64 for volume, Float for turnover, Date for timestamp.
  - [x] Validate against empty files, missing columns, and market holidays.

- [x] **2.3 Security-Wise Delivery Report (MTO) Ingestion (`src/nse_cash/data/delivery.py`)**
  - [x] URL Pattern: `https://archives.nseindia.com/archives/equities/mto/MTO_{DDMMYYYY}.DAT` (or `.csv`).
  - [x] Parser for DAT / CSV structure:
    - Handle Record Type 20 (Security-wise client delivery).
    - Extract: `symbol`, `series` (filter `EQ`), `traded_qty` (int), `deliverable_qty` (int), `delivery_pct` (float).
  - [x] Join delivery data with daily Bhavcopy on `(symbol, date)` into unified daily table.

- [x] **2.4 NSE Corporate Actions Ingestion (`src/nse_cash/data/corporate_actions.py`)**
  - [x] Ingest Corporate Actions archive: `https://www.nseindia.com/api/corporates-corporateActions?index=equities` and historical archives.
  - [x] Parse records: `symbol`, `series`, `purpose`, `ex_date`, `record_date`, `bc_start_date`, `bc_end_date`.
  - [x] Extract numerical ratios for:
    - Stock Splits (e.g., `SPLIT FROM RS 10 TO RS 2` $\rightarrow$ Ratio 5:1, $A=4, B=1$)
    - Bonus Issues (e.g., `BONUS 1:1`, `BONUS 1:2`, `BONUS 3:1` $\rightarrow$ Ratio $A:B$)
    - Rights Issues & Dividends (for informational logging).

- [x] **2.5 NIFTY 50 & NIFTY 500 Benchmark Ingestion (`src/nse_cash/data/indices.py`)**
  - [x] Ingest daily historical OHLC for NIFTY 50 (`^NSEI`) and NIFTY 500 from NSE Index archives or local cache.
  - [x] Store index series into DuckDB table `market_indices`.

- [x] **2.6 Unified Local Storage Layer (`src/nse_cash/data/storage.py`)**
  - [x] Setup DuckDB database at `data/db/nse_market.duckdb`.
  - [x] Create indexed tables:
    - `daily_bars` (`symbol`, `date`, `open`, `high`, `low`, `close`, `last`, `volume`, `turnover`, `deliverable_qty`, `delivery_pct`, PRIMARY KEY(`symbol`, `date`))
    - `corporate_actions` (`symbol`, `ex_date`, `purpose`, `action_type`, `ratio_a`, `ratio_b`, `adjustment_factor`)
    - `market_indices` (`index_name`, `date`, `open`, `high`, `low`, `close`, `volume`)
    - `pit_universe` (`date`, `symbol`, `adtv_90`, `rank`)
  - [x] Implement Parquet export partitioned by year: `data/processed/daily_bars_{YYYY}.parquet`.

- [x] **2.7 Synchronization CLI Implementation (`src/nse_cash/cli/sync.py`)**
  - [x] Implement `nse-cash sync`:
    - [x] With no args: Syncs current/latest trading day (run after 6:45 PM IST).
    - [x] `nse-cash sync --from 2010-01-01 --to 2026-09-06`: Historical multi-year backfill with multi-threaded downloading and Rich progress bar.
    - [x] Idempotency check: Skip dates already fully present in database unless `--force` is provided.

---

### Phase 3: Corporate Action Adjustment Engine & Dynamic PIT Universe Engine

#### Objective
Build the mathematical corporate action adjustment engine to eliminate false signals and formulate the survivorship-bias-free dynamic Point-in-Time Top 500 universe.

#### To-Do List

- [x] **3.1 Corporate Action Adjustment Calculator (`src/nse_cash/core/adjuster.py`)**
  - [x] Implement mathematical adjustment formula:
    - For split/bonus ratio $A:B$ (e.g., $1:1$ bonus $\implies A=1, B=1$; $10\rightarrow 2$ split $\implies A=4, B=1$):
      $$AF_t = \begin{cases} \frac{B}{A + B} & \text{for } t < t_{\text{ex}} \\ 1.0 & \text{for } t \ge t_{\text{ex}} \end{cases}$$
  - [x] Compute backwards price and volume adjustments:
    $$\text{Price}_{\text{adj}}(t) = \text{Price}_{\text{raw}}(t) \times AF_t$$
    $$\text{Volume}_{\text{adj}}(t) = \frac{\text{Volume}_{\text{raw}}(t)}{AF_t}$$
    $$\text{Delivery}_{\text{adj}}(t) = \frac{\text{Deliverable\_Qty}_{\text{raw}}(t)}{AF_t}$$
  - [x] Handle chained cascading corporate actions (multiple splits/bonuses on the same symbol across time) using cumulative multiplication:
    $$\text{Cumulative\_AF}(t) = \prod_{k: t_{\text{ex}, k} > t} AF_k$$
  - [x] Populate adjusted columns in DuckDB: `open_adj`, `high_adj`, `low_adj`, `close_adj`, `volume_adj`, `delivery_adj`.

- [x] **3.2 Dynamic Point-in-Time (PIT) Universe Engine (`src/nse_cash/core/universe.py`)**
  - [x] For each historical trading date $t$:
    - [x] Compute rolling 90-day Average Daily Traded Value ($\text{ADTV}_{90}$):
      $$\text{ADTV}_{90}(t) = \frac{1}{90} \sum_{k=0}^{89} (\text{Close}_{\text{raw}}(t-k) \times \text{Volume}_{\text{raw}}(t-k))$$
    - [x] Enforce liquidity gate: $\text{ADTV}_{90}(t) \ge \text{₹5,00,00,000}$ (₹5.00 Crores).
    - [x] Enforce price floor gate: $\text{Close}_{\text{raw}}(t) \ge \text{₹50.00}$ (Zero penny stocks).
    - [x] Rank all qualifying NSE `EQ` stocks by $\text{ADTV}_{90}(t)$ descending and select the Top 500.
  - [x] Store daily PIT membership in table `pit_universe(date, symbol, adtv_90, rank)`.

- [x] **3.3 Microstructure & Governance Filter Engine (`src/nse_cash/core/governance.py`)**
  - [x] Ingest SEBI ASM (Additional Surveillance Measure) / GSM (Graded Surveillance Measure) lists. Exclude any stock currently in ASM/GSM stages.
  - [x] Ingest daily price circuit band data:
    - [x] Exclude stocks with price circuit band $\le 5\%$.
    - [x] Exclude stocks that hit their upper or lower circuit limit in the preceding 3 trading sessions ($t-1, t-2, t-3$).
  - [x] Ingest NSE Board Meeting & Corporate Results Calendar:
    - [x] Exclude stocks with board meetings scheduled within the next 3 trading sessions ($t$ to $t+3$) to avoid binary earnings gap risk.

- [x] **3.4 Data Verification & Health Check CLI (`src/nse_cash/cli/status.py`)**
  - [x] Implement `nse-cash status`:
    - [x] Displays total dates ingested, date range, total symbols, missing date anomalies.
    - [x] Displays PIT Top 500 universe count for the latest trading date.
    - [x] Displays active corporate actions applied.

---

### Phase 4: Market Regime Engine & Sector Diversification Gate (Funnel Stages 1, 2, 4)

#### Objective
Implement Funnel Stage 1 (Macro Regime), Stage 2 (Governance & Liquidity), and Stage 4 (Sector allocation & pre-entry constraints) to govern capital deployment and portfolio risk.

#### To-Do List

- [ ] **4.1 Macro Market Regime Engine (`src/nse_cash/funnel/market_regime.py`)**
  - [ ] Compute 20-day Exponential Moving Average ($\text{EMA}_{20}$) of NIFTY 50 Close.
  - [ ] Macro Condition 1: Check if NIFTY 50 $\text{Close}_t > \text{EMA}_{20}(\text{Close})$.
  - [ ] Compute NIFTY 500 50-day Simple Moving Average ($\text{SMA}_{50}$) for all constituent stocks.
  - [ ] Compute Market Breadth:
    $$\text{Breadth}(t) = \frac{\text{Count of NIFTY 500 stocks with } \text{Close}_t > \text{SMA}_{50}(\text{Close})}{\text{Total Active NIFTY 500 stocks}} \times 100\%$$
  - [ ] Macro Condition 2: Check if $\text{Breadth}(t) > 50.0\%$.
  - [ ] Regime Decision Logic:
    - If NIFTY 50 $\le \text{EMA}_{20}$ OR $\text{Breadth} \le 50.0\% \implies$ `DEFENSIVE_CASH` (100% Cash switch: zero new swing entries generated; existing trades managed to their stop/target/stall exits).
    - If both pass $\implies$ `OFFENSIVE_LONG` (Allow Stage 3 setup evaluations).

- [ ] **4.2 Sector Classification & Diversification Gate (`src/nse_cash/funnel/sector_gate.py`)**
  - [ ] Ingest and maintain standard NSE Sector / Industry classification mapping (`data/nse_sectors.json`): e.g., Auto, Banking, FMCG, IT, Metals, Pharma, Energy, etc.
  - [ ] Sector Constraint Rule: Maximum **1 open position per Sector** across the 4 concurrent portfolio slots.
  - [ ] When evaluating candidates, reject any candidate whose sector is already occupied by an active trade.

- [ ] **4.3 Pre-Entry Structural Risk & Capacity Controller (`src/nse_cash/funnel/stage4_gate.py`)**
  - [ ] Portfolio Capacity Check: Verify number of open slots $< 4$. If all 4 slots are occupied, abstain from new entries.
  - [ ] Pre-Entry Max-Risk Gate:
    $$\text{Structural\_Risk\_Pct} = \frac{\text{Entry\_Ref} - \text{Structural\_Stop}}{\text{Entry\_Ref}} \times 100\%$$
    - If $\text{Structural\_Risk\_Pct} > 2.20\% \implies$ Candidate is **strictly disqualified**.
  - [ ] 10:00 AM Gap Invalidation Rule:
    $$\text{If } \text{Price}_{\text{10:00 AM}} > \text{Close}_T \times 1.012 \implies \text{Signal Cancelled (Reject trade)}$$

---

### Phase 5: Quantitative Setups Catalog & Asymmetric Runner Skew Scoring ($S_{\text{runner}}$)

#### Objective
Implement the 5 deterministic quantitative trading setups from `algos.md`, the vectorized indicator feature engine, and the composite Asymmetric Runner Skew Scorer ($S_{\text{runner}}$).

#### To-Do List

- [ ] **5.1 Vectorized Indicator & Microstructure Features Engine (`src/nse_cash/setups/features.py`)**
  - [ ] Trend Indicators: $\text{SMA}_{200}(\text{Close})$, $\text{SMA}_{50}(\text{Close})$, $\text{SMA}_{20}(\text{Close})$, $\text{Slope}(\text{SMA}_{200})$.
  - [ ] Volume & Delivery Moving Averages: $\text{SMA}_{20}(\text{Volume})$, $\text{SMA}_{20}(\text{Delivery\_Qty})$.
  - [ ] Standardized Delivery Volume Z-Score:
    $$Z_{\text{Delivery}}(t) = \frac{\text{Delivery\_Qty}_t - \text{SMA}_{20}(\text{Delivery\_Qty})}{\sigma_{20}(\text{Delivery\_Qty})}$$
  - [ ] 5-day Parkinson Volatility ($PV_5$):
    $$\text{PV}_5(t) = \sqrt{ \frac{1}{4 \ln 2 \cdot 5} \sum_{k=0}^{4} \left( \ln \frac{\text{High}_{t-k}}{\text{Low}_{t-k}} \right)^2 }$$
  - [ ] Parkinson Percentile Rank ($\text{PV}_{\text{Percentile}}$): Rank of $\text{PV}_5(t)$ within its rolling 60-day historical distribution ($0.0 \text{ to } 1.0$).
  - [ ] 2-Period RSI ($\text{RSI}(2)$) using Wilder's smoothing.
  - [ ] Mansfield Relative Strength ($\text{RS}_{\text{Mansfield}}$) against NIFTY 500:
    $$\text{RS}_i(t) = \left( \frac{\text{Close}_i(t) / \text{NIFTY500}(t)}{\text{SMA}_{50}(\text{Close}_i / \text{NIFTY500})} - 1 \right) \times 100$$
    Compute cross-sectional percentile rank across the Top 500 universe ($\text{RS}_{\text{Percentile}}$).
  - [ ] Rolling 36-day OLS Beta-Neutral Residual Alpha Regression:
    $$R_i(t) = \alpha_i + \beta_i R_{\text{NIFTY50}}(t) + \epsilon_i(t)$$
    $$\text{iMOM}_i(t) = \frac{\sum_{k=0}^{20} \epsilon_i(t-k)}{\sigma_{36}(\epsilon_i)}$$
    Compute cross-sectional percentile rank across the Top 500 universe ($\text{iMOM}_{\text{Percentile}}$).
  - [ ] Rolling 90-day Base High/Low, Breakout Resistance, and Support Levels.

- [ ] **5.2 Setup 1: Delivery Absorption & Volatility Contraction (VCP + Parkinson Squeeze) (`src/nse_cash/setups/setup1_vcp_squeeze.py`)**
  - [ ] Secular Trend: $\text{Close}_t > \text{SMA}_{200}(\text{Close})$ AND $\text{SMA}_{50}(\text{Close}) > \text{SMA}_{200}(\text{Close})$.
  - [ ] Accumulation Footprint:
    - **Condition A (Single Shock):** In last 5 sessions, $\ge 1$ day with $\text{Delivery\_Qty} \ge 2.20 \times \text{SMA}_{20}(\text{Delivery\_Qty})$ AND $\text{Close} > \text{Open}$.
    - **OR Condition B (Iceberg):** $Z_{\text{Delivery}} \ge +1.50$ for $\ge 2$ of last 3 sessions.
  - [ ] Volume Dry-Up: Day $T$ volume $\text{Volume}_T \le 0.65 \times \text{SMA}_{20}(\text{Volume})$.
  - [ ] Volatility Squeeze: $(\text{High}_T - \text{Low}_T)/\text{Close}_T \le 0.015$ OR $\text{PV}_5 \le \text{Percentile}_{15}(\text{PV}_{60})$.
  - [ ] Orders & Risk:
    - Structural Stop: $\min(\text{Low}_T, \text{Low}_{T-1})$ (enforce $\le 2.20\%$).
    - Tranche 1 (50% Qty): Target $+2.00\%$.
    - Tranche 2 (50% Qty): Target $+5.00\%$ to $+6.00\%$ (or Trailing Low).

- [ ] **5.3 Setup 2: Secular Uptrend Rubber-Band Pullback (`src/nse_cash/setups/setup2_rubberband.py`)**
  - [ ] Secular Trend: $\text{Close}_t > \text{SMA}_{200}(\text{Close})$ AND $\text{Slope}(\text{SMA}_{200}) > 0$.
  - [ ] Consecutive Pullback: $\text{Close}_T < \text{Close}_{T-1} < \text{Close}_{T-2}$ (3 consecutive lower closes).
  - [ ] Subdued Volume: $\text{Delivery\_Qty}_T \le 1.15 \times \text{SMA}_{20}(\text{Delivery\_Qty})$.
  - [ ] Exhaustion: $\text{RSI}(2)_T \le 10.0$.
  - [ ] Orders & Risk:
    - Structural Stop: Day $T$ Low $- 0.2\%$ (enforce $\le 2.20\%$).
    - Tranche 1 (50% Qty): Target $+1.80\%$.
    - Tranche 2 (50% Qty): Target $+3.50\%$.

- [ ] **5.4 Setup 3: Relative Strength Base Consolidation (`src/nse_cash/setups/setup3_rs_base.py`)**
  - [ ] Macro: NIFTY 50 $\text{Close} > \text{EMA}_{20}$.
  - [ ] RS Leadership: $\text{RS}_{\text{Percentile}} \ge 0.95$ (Top 5th percentile of universe).
  - [ ] Base Consolidation: High/Low range over past 5 sessions $\le 3.0\%$ AND $\text{Close}_T \ge 0.985 \times \text{52-Week High}$.
  - [ ] Orders & Risk:
    - Structural Stop: Low of 5-day base (enforce $\le 2.20\%$).
    - Tranche 1 (50% Qty): Target $+2.00\%$.
    - Tranche 2 (50% Qty): Target $+6.00\%$ (or Trailing Low).

- [ ] **5.5 Setup 4: Multi-Month Base Breakout & Anchor Retest (`src/nse_cash/setups/setup4_anchor_retest.py`)**
  - [ ] Breakout Confirmation: Stock broke above 90-day base resistance within last 3 to 7 sessions on $\text{Delivery\_Qty} \ge 2.0 \times \text{SMA}_{20}$.
  - [ ] Support Retest: Day $T$ Low touches within $\pm 0.8\%$ of breakout level and holds above it.
  - [ ] Dry-Up: $\text{Volume}_T \le 0.55 \times \text{SMA}_{20}(\text{Volume})$.
  - [ ] Rejection Tail: $\text{Close}_T > \text{Open}_T$ AND lower shadow $\ge 40\%$ of total candle range $((\min(\text{Open}, \text{Close}) - \text{Low}) / (\text{High} - \text{Low}) \ge 0.40)$.
  - [ ] Orders & Risk:
    - Structural Stop: Breakout Support level $- 0.2\%$ (enforce $\le 2.00\%$).
    - Tranche 1 (50% Qty): Target $+2.00\%$.
    - Tranche 2 (50% Qty): Target $+6.00\%$ (or Trailing Low).

- [ ] **5.6 Setup 5: Cross-Sectional Residual / Idiosyncratic Momentum (`src/nse_cash/setups/setup5_residual_momentum.py`)**
  - [ ] Alpha Leadership: $\text{iMOM}_{\text{Percentile}} \ge 0.95$ (Top 5th percentile of universe).
  - [ ] Delivery Shock: $\text{Delivery\_Qty}_T \ge 2.0 \times \text{SMA}_{20}(\text{Delivery\_Qty})$ AND $\text{Close}_T > \text{Open}_T$.
  - [ ] Orders & Risk:
    - Structural Stop: Prior day low (enforce $\le 2.20\%$).
    - Tranche 1 (50% Qty): Target $+2.00\%$.
    - Tranche 2 (50% Qty): Target $+6.00\%$ (or Trailing Low).

- [ ] **5.7 Asymmetric Runner Skew Score ($S_{\text{runner}}$) & Priority Ranker (`src/nse_cash/setups/ranking.py`)**
  - [ ] Calculate composite score:
    $$S_{\text{runner}} = 0.35 \times Z_{\text{Delivery}} + 0.35 \times \text{iMOM}_{\text{Percentile}} + 0.30 \times (1 - \text{PV}_{\text{Percentile}})$$
  - [ ] Flag high-conviction runners ($S_{\text{runner}} \ge 0.70$).
  - [ ] Rank all qualifying candidates across the 5 setups and pick top candidates up to available portfolio slots (max 4 total).

---

### Phase 6: High-Fidelity Simulation & Backtesting Engine

#### Objective
Build a tick-accurate, point-in-time backtesting engine covering 13 years of in-sample calibration (2010–2022) and 3.5 years of out-of-sample walk-forward validation (2023–Present) with exact 2-tranche execution, friction, DP charges, and universal 20% STCG taxation.

#### To-Do List

- [ ] **6.1 Temporal Split & Portfolio Architecture (`src/nse_cash/backtest/engine.py`)**
  - [ ] Dataset partitioning:
    - **In-Sample Period:** 2010-01-01 to 2022-12-31 (13 Years)
    - **Out-of-Sample Walk-Forward:** 2023-01-01 to Present (~3.5 Years)
  - [ ] Maintain exact ₹5,00,000 capital ledger across 4 discrete slots (₹1,25,000 per slot; ₹62,500 per tranche).
  - [ ] Model Overnight Liquid Fund interest yield: 6.5% p.a. earned daily on unallocated cash balance.

- [ ] **6.2 Discrete 2-Tranche Trade Simulation Loop (`src/nse_cash/backtest/trade_manager.py`)**
  - [ ] **Day T+1 10:00 AM Entry:**
    - Entry simulated at 10:00 AM price.
    - Check gap rule: If $\text{Open}_{T+1} > \text{Close}_T \times 1.012 \implies$ Reject entry.
    - Check structural risk: If $\text{Structural\_Stop\_Pct} > 2.20\% \implies$ Reject entry.
  - [ ] **Tranche 1 Simulation (50% Qty / ₹62,500):**
    - First touch of $\text{Entry} \times 1.020$ ($+2.00\%$) $\implies$ Tranche 1 fills.
    - On fill, shift Tranche 2 Stop to Breakeven ($\text{Entry} \times 1.000$) at EOD.
  - [ ] **Tranche 2 Simulation (50% Qty / ₹62,500):**
    - Evaluated against Daily Low of $T-1$ trailing stop, runner target ($+5\%$ to $+8\%$), or hard Day 5 3:15 PM EOD close.
  - [ ] **48-Hour Stall Exit:**
    - On Day $T+2$ at 3:15 PM, if $\text{Close}_{T+2} < \text{Entry} \times 1.008 \implies$ Exit entire position at $\text{Close}_{T+2}$.
  - [ ] **Overnight Gap-Down Stop Realization:**
    - If $\text{Open}_t < \text{Stop\_Loss\_Price} \implies$ Exit price = $\text{Open}_t$ (realizes full gap-down slippage).

- [ ] **6.3 Exact Friction & Universal STCG Taxation Calculator (`src/nse_cash/backtest/tax_friction.py`)**
  - [ ] Entry Friction Breakdown (on ₹1,25,000 order):
    - STT: $0.10\%$ on buy side
    - Stamp Duty: $0.015\%$ on buy side
    - Exchange Transaction Fee: $0.00345\%$
    - SEBI Fee: $0.0001\%$
    - GST: $18\%$ on (Exchange Fee + SEBI Fee)
    - Slippage: $0.05\%$
  - [ ] Tranche Exit Friction Breakdown (on ₹62,500 tranche exit):
    - STT: $0.10\%$ on sell side
    - Exchange Transaction Fee: $0.00345\%$
    - SEBI Fee: $0.0001\%$
    - GST: $18\%$ on (Exchange Fee + SEBI Fee)
    - CDSL / NSDL DP Charge: **₹15.93 flat** per tranche sell day
    - Slippage: $0.05\%$
  - [ ] Universal STCG Tax Deduction:
    - Compute net realized P&L per financial year (April 1 to March 31).
    - Deduct **20.0% flat STCG** on net annual profits across all backtest years (2010 to Present).

- [ ] **6.4 Portfolio Drawdown Kill Switch (`src/nse_cash/backtest/risk_manager.py`)**
  - [ ] Track peak portfolio equity (High-Water Mark).
  - [ ] If drawdown from high-water mark reaches $-7.5\%$:
    - Liquidate all active positions at market.
    - Enforce mandatory 10 trading days cooling period (zero new entries).

- [ ] **6.5 Performance Analytics & Tear-Sheet Generator (`src/nse_cash/backtest/metrics.py`)**
  - [ ] Calculate key performance indicators:
    - Post-Tax CAGR (%)
    - Win Rate (%)
    - Profit Factor
    - Maximum Drawdown (%) & Drawdown Duration
    - Average Win $\bar{R}_{\text{win}}$ & Average Loss $\bar{R}_{\text{loss}}$
    - Net Trade Expectancy (%)
    - Annualized Sharpe & Sortino Ratios
    - Monthly / Annual Returns Matrix
  - [ ] Export tear-sheet to terminal (Rich table), JSON, and Parquet.

- [ ] **6.6 Backtest CLI Subcommand (`src/nse_cash/cli/backtest.py`)**
  - [ ] Implement `nse-cash backtest`:
    - `--in-sample` (Runs 2010–2022)
    - `--walk-forward` (Runs 2023–Present)
    - `--full` (Runs 2010–Present and outputs complete comparative report)

---

### Phase 7: Portfolio Ledger, State Machine & Daily 10:00 AM Action Sheet

#### Objective
Build the SQLite production ledger, trade state machine, and the visual 10:00 AM Daily Action Sheet generator providing exact Dual-GTT Kite orders.

#### To-Do List

- [ ] **7.1 Production SQLite Ledger & State Machine (`src/nse_cash/execution/ledger.py`)**
  - [ ] SQLite Database at `data/db/portfolio_ledger.sqlite3`.
  - [ ] Schema:
    - `slots` (`slot_id` (1..4), `status` (`EMPTY`, `OCCUPIED`), `current_trade_id`)
    - `trades` (`trade_id`, `symbol`, `setup_id`, `entry_date`, `entry_price`, `quantity`, `sector`, `status`)
    - `tranches` (`tranche_id`, `trade_id`, `tranche_num` (1 or 2), `quantity`, `target_price`, `stop_price`, `status`, `exit_price`, `exit_date`, `exit_reason`)
    - `cash_ledger` (`date`, `cash_balance`, `invested_capital`, `total_equity`, `realized_pnl`)
  - [ ] State transition validation enforcing deterministic lifecycle flow.

- [ ] **7.2 Daily Morning Scanner (`src/nse_cash/execution/scanner.py`)**
  - [ ] Runs at 9:55 AM IST via `nse-cash scan`.
  - [ ] Loads previous evening's data (after 6:45 PM sync).
  - [ ] Evaluates Macro Regime (Stage 1). If failed, outputs 100% Cash defensive notification.
  - [ ] Evaluates Liquidity, Governance & Circuit filters (Stage 2).
  - [ ] Runs the 5 quantitative setups and scores $S_{\text{runner}}$ (Stage 3).
  - [ ] Checks open slots in SQLite ledger (0 to 4 available).
  - [ ] Allocates position size: ₹1,25,000 per slot $\implies \text{Total Quantity} = \lfloor 125000 / \text{Entry\_Ref} \rfloor$.
  - [ ] Splits into Tranche 1 ($\lfloor \text{Qty} / 2 \rfloor$) and Tranche 2 ($\text{Qty} - \text{Tranche 1 Qty}$).
  - [ ] Computes exact Dual-GTT target and stop price levels.

- [ ] **7.3 Visual 10:00 AM Daily Action Sheet Renderer (`src/nse_cash/execution/action_sheet.py`)**
  - [ ] Render Rich terminal table matching BRD Section 9.1:
    - Capital & Open Slots Summary Header
    - Candidate Action Table: Symbol, Action (BUY), Entry Ref, Max Entry (+1.2% Cap), Tranche 1 Target (+2.0%), Tranche 2 Target (+6.0%/Trail), Structural Stop (-2.2% max)
    - Step-by-step Kite Manual Playbook (Limit Buy order + 2 independent GTT OCO Sell orders)
    - EOD 3:20 PM Routine (Stall check & Breakeven stop adjustment)

- [ ] **7.4 Ledger Management CLI Commands (`src/nse_cash/cli/ledger.py`)**
  - [ ] `nse-cash ledger`: Display active positions, unrealized P&L, slot allocation, and GTT trigger levels.
  - [ ] `nse-cash ledger --record-fill SYMBOL PRICE QTY`: Record 10:00 AM fill.
  - [ ] `nse-cash ledger --record-exit SYMBOL TRANCHE PRICE REASON`: Record tranche exit.
  - [ ] `nse-cash ledger --check-eod`: Automated 3:20 PM check for stall exit conditions ($< +0.80\%$) and Tranche 1 fills (prompting Breakeven modification on Tranche 2).

---

### Phase 8: Test Suite, Benchmarking, Production Runbook & Developer Guide

#### Objective
Establish a 100% deterministic test harness, validate backtest metrics against BRD targets, write the production operational runbook, and document the architecture for zero-intervention developer onboarding.

#### To-Do List

- [ ] **8.1 Mathematical & Unit Test Suite (`tests/unit/`)**
  - [ ] `tests/unit/test_adjuster.py`: Test corporate action multiplier calculation, 1:1 bonus, 5:1 split, chained splits.
  - [ ] `tests/unit/test_indicators.py`: Test Parkinson Volatility ($PV_5$) vs closed-form values, Wilder's RSI(2), Mansfield RS, OLS residual alpha.
  - [ ] `tests/unit/test_setups.py`: Test each of the 5 setups with synthetic deterministic OHLCV bars.
  - [ ] `tests/unit/test_tax_friction.py`: Test STT, DP charge (₹15.93), GST, slippage, and 20% STCG tax computation.
  - [ ] `tests/unit/test_ranking.py`: Test $S_{\text{runner}}$ calculation and sorting.

- [ ] **8.2 End-to-End & Integration Test Suite (`tests/integration/`)**
  - [ ] `tests/integration/test_pipeline_e2e.py`: Test Bhavcopy unzipping, MTO parsing, DuckDB insertion, and Action Sheet generation.
  - [ ] `tests/integration/test_backtest_reproducibility.py`: Verify deterministic backtest execution across multiple runs.
  - [ ] `tests/integration/test_state_machine.py`: Verify slot allocation (max 4 concurrent), sector constraints, and GTT transitions.

- [ ] **8.3 Performance Metric Benchmarking**
  - [ ] Execute out-of-sample backtest (2023–Present) and verify metrics meet BRD Section 10.1:
    - Post-Tax CAGR: $\ge +14.0\%$
    - Win Rate: $48.0\% - 56.0\%$
    - Profit Factor: $1.55 - 1.85$
    - Max Drawdown: $< 8.5\%$
    - Average Win: $+2.40\% - +2.70\%$ Net
    - Average Loss: $-1.80\% - -1.90\%$ Net
    - Expectancy: $+0.40\% - +0.75\%$ Net per trade

- [ ] **8.4 Production Operations Runbook (`docs/RUNBOOK.md`)**
  - [ ] Create detailed operational guide:
    - **6:45 PM IST Daily:** `nse-cash sync` (Download official Bhavcopy, MTO, and Corporate Actions).
    - **9:55 AM IST Morning:** `nse-cash scan` (Generate 10:00 AM Daily Action Sheet).
    - **10:00 AM IST Market Open:** Verify price $\le \text{Close}_T + 1.2\%$, place manual limit buy on Kite, place Dual-GTT OCO orders. Record fill via `nse-cash ledger --record-fill`.
    - **3:20 PM IST EOD Routine:** Run `nse-cash ledger --check-eod`. If Tranche 1 hit target, modify Tranche 2 stop to Breakeven. If Day $T+2$ price $< +0.80\%$, cancel GTTs and sell at market.

- [ ] **8.5 Developer Architecture Guide (`docs/ARCHITECTURE.md`)**
  - [ ] Document complete codebase structure, module dependencies, DuckDB/SQLite schemas, CLI commands, and maintenance procedures so any new developer can build, test, and maintain the system with zero external intervention.

---

## Verification & Acceptance Checklist

| Subsystem | Verification Command | Acceptance Criteria |
|---|---|---|
| **Data Ingestion** | `nse-cash sync --from 2026-08-01 --to 2026-09-06` | Ingests Bhavcopy + MTO, zero missing dates, parses `EQ` series cleanly into DuckDB. |
| **Corporate Actions** | `nse-cash status` | Verifies backward adjustment multiplier on historical splits/bonuses. |
| **Market Regime** | `nse-cash scan` | Correctly flags `OFFENSIVE_LONG` vs `DEFENSIVE_CASH` based on NIFTY 20EMA & 500 Breadth. |
| **Action Sheet** | `nse-cash scan` | Produces Rich 10:00 AM Action Sheet with exact Dual-GTT Kite orders and ₹1,25,000 slot sizing. |
| **Backtest Engine** | `nse-cash backtest --full` | Executes 2010–Present simulation with 2-tranche exits, 48h stall, 0.35% friction, ₹15.93 DP charge, 20% STCG. |
| **Test Suite** | `pytest tests/ -v --cov=src/nse_cash` | 100% tests passing with $\ge 90\%$ code coverage. |
