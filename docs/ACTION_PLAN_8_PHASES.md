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

- [x] **4.1 Macro Market Regime Engine (`src/nse_cash/funnel/market_regime.py`)**
  - [x] Compute 20-day Exponential Moving Average ($\text{EMA}_{20}$) of NIFTY 50 Close.
  - [x] Macro Condition 1: Check if NIFTY 50 $\text{Close}_t > \text{EMA}_{20}(\text{Close})$.
  - [x] Compute NIFTY 500 50-day Simple Moving Average ($\text{SMA}_{50}$) for all constituent stocks.
  - [x] Compute Market Breadth:
    $$\text{Breadth}(t) = \frac{\text{Count of NIFTY 500 stocks with } \text{Close}_t > \text{SMA}_{50}(\text{Close})}{\text{Total Active NIFTY 500 stocks}} \times 100\%$$
  - [x] Macro Condition 2: Check if $\text{Breadth}(t) > 50.0\%$.
  - [x] Regime Decision Logic:
    - If NIFTY 50 $\le \text{EMA}_{20}$ OR $\text{Breadth} \le 50.0\% \implies$ `DEFENSIVE_CASH` (100% Cash switch: zero new swing entries generated; existing trades managed to their stop/target/stall exits).
    - If both pass $\implies$ `OFFENSIVE_LONG` (Allow Stage 3 setup evaluations).

- [x] **4.2 Sector Classification & Diversification Gate (`src/nse_cash/funnel/sector_gate.py`)**
  - [x] Ingest and maintain standard NSE Sector / Industry classification mapping (`data/nse_sectors.json`): e.g., Auto, Banking, FMCG, IT, Metals, Pharma, Energy, etc.
  - [x] Sector Constraint Rule: Maximum **1 open position per Sector** across the 4 concurrent portfolio slots.
  - [x] When evaluating candidates, reject any candidate whose sector is already occupied by an active trade.

- [x] **4.3 Pre-Entry Structural Risk & Capacity Controller (`src/nse_cash/funnel/stage4_gate.py`)**
  - [x] Portfolio Capacity Check: Verify number of open slots $< 4$. If all 4 slots are occupied, abstain from new entries.
  - [x] Pre-Entry Max-Risk Gate:
    $$\text{Structural\_Risk\_Pct} = \frac{\text{Entry\_Ref} - \text{Structural\_Stop}}{\text{Entry\_Ref}} \times 100\%$$
    - If $\text{Structural\_Risk\_Pct} > 2.20\% \implies$ Candidate is **strictly disqualified**.
  - [x] 10:00 AM Gap Invalidation Rule:
    $$\text{If } \text{Price}_{\text{10:00 AM}} > \text{Close}_T \times 1.012 \implies \text{Signal Cancelled (Reject trade)}$$

---

### Phase 5: Quantitative Setups Catalog & Asymmetric Runner Skew Scoring ($S_{\text{runner}}$)

#### Objective
Implement the 5 deterministic quantitative trading setups from `algos.md`, the vectorized indicator feature engine, and the composite Asymmetric Runner Skew Scorer ($S_{\text{runner}}$).

#### To-Do List

- [x] **5.1 Vectorized Indicator & Microstructure Features Engine (`src/nse_cash/setups/features.py`)**
  - [x] Trend Indicators: $\text{SMA}_{200}(\text{Close})$, $\text{SMA}_{50}(\text{Close})$, $\text{SMA}_{20}(\text{Close})$, $\text{Slope}(\text{SMA}_{200})$.
  - [x] Volume & Delivery Moving Averages: $\text{SMA}_{20}(\text{Volume})$, $\text{SMA}_{20}(\text{Delivery\_Qty})$.
  - [x] Standardized Delivery Volume Z-Score:
    $$Z_{\text{Delivery}}(t) = \frac{\text{Delivery\_Qty}_t - \text{SMA}_{20}(\text{Delivery\_Qty})}{\sigma_{20}(\text{Delivery\_Qty})}$$
  - [x] 5-day Parkinson Volatility ($PV_5$):
    $$\text{PV}_5(t) = \sqrt{ \frac{1}{4 \ln 2 \cdot 5} \sum_{k=0}^{4} \left( \ln \frac{\text{High}_{t-k}}{\text{Low}_{t-k}} \right)^2 }$$
  - [x] Parkinson Percentile Rank ($\text{PV}_{\text{Percentile}}$): Rank of $\text{PV}_5(t)$ within its rolling 60-day historical distribution ($0.0 \text{ to } 1.0$).
  - [x] 2-Period RSI ($\text{RSI}(2)$) using Wilder's smoothing.
  - [x] Mansfield Relative Strength ($\text{RS}_{\text{Mansfield}}$) against NIFTY 500:
    $$\text{RS}_i(t) = \left( \frac{\text{Close}_i(t) / \text{NIFTY500}(t)}{\text{SMA}_{50}(\text{Close}_i / \text{NIFTY500})} - 1 \right) \times 100$$
    Compute cross-sectional percentile rank across the Top 500 universe ($\text{RS}_{\text{Percentile}}$).
  - [x] Rolling 36-day OLS Beta-Neutral Residual Alpha Regression:
    $$R_i(t) = \alpha_i + \beta_i R_{\text{NIFTY50}}(t) + \epsilon_i(t)$$
    $$\text{iMOM}_i(t) = \frac{\sum_{k=0}^{20} \epsilon_i(t-k)}{\sigma_{36}(\epsilon_i)}$$
    Compute cross-sectional percentile rank across the Top 500 universe ($\text{iMOM}_{\text{Percentile}}$).
  - [x] Rolling 90-day Base High/Low, Breakout Resistance, and Support Levels. (+ prior-session low `prev_low` for Setup 1/5 stops.)

- [x] **5.2 Setup 1: Delivery Absorption & Volatility Contraction (VCP + Parkinson Squeeze) (`src/nse_cash/setups/catalog.py`, pure predicates)**
  - [x] Secular Trend: $\text{Close}_t > \text{SMA}_{200}(\text{Close})$ AND $\text{SMA}_{50}(\text{Close}) > \text{SMA}_{200}(\text{Close})$.
  - [x] Accumulation Footprint:
    - **Condition A (Single Shock):** In last 5 sessions, $\ge 1$ day with $\text{Delivery\_Qty} \ge 2.20 \times \text{SMA}_{20}(\text{Delivery\_Qty})$ AND $\text{Close} > \text{Open}$.
    - **OR Condition B (Iceberg):** $Z_{\text{Delivery}} \ge +1.50$ for $\ge 2$ of last 3 sessions.
  - [x] Volume Dry-Up: Day $T$ volume $\text{Volume}_T \le 0.65 \times \text{SMA}_{20}(\text{Volume})$.
  - [x] Volatility Squeeze: $(\text{High}_T - \text{Low}_T)/\text{Close}_T \le 0.015$ OR $\text{PV}_5 \le \text{Percentile}_{15}(\text{PV}_{60})$.
  - [x] Orders & Risk:
    - Structural Stop: $\min(\text{Low}_T, \text{Low}_{T-1})$ (enforce $\le 2.20\%$).
    - Tranche 1 (50% Qty): Target $+2.00\%$.
    - Tranche 2 (50% Qty): Target $+5.00\%$ to $+6.00\%$ (or Trailing Low).

- [x] **5.3 Setup 2: Secular Uptrend Rubber-Band Pullback (`src/nse_cash/setups/catalog.py`)**
  - [x] Secular Trend: $\text{Close}_t > \text{SMA}_{200}(\text{Close})$ AND $\text{Slope}(\text{SMA}_{200}) > 0$.
  - [x] Consecutive Pullback: $\text{Close}_T < \text{Close}_{T-1} < \text{Close}_{T-2}$ (3 consecutive lower closes).
  - [x] Subdued Volume: $\text{Delivery\_Qty}_T \le 1.15 \times \text{SMA}_{20}(\text{Delivery\_Qty})$.
  - [x] Exhaustion: $\text{RSI}(2)_T \le 10.0$.
  - [x] Orders & Risk:
    - Structural Stop: Day $T$ Low $- 0.2\%$ (enforce $\le 2.20\%$).
    - Tranche 1 (50% Qty): Target $+1.80\%$.
    - Tranche 2 (50% Qty): Target $+3.50\%$.

- [x] **5.4 Setup 3: Relative Strength Base Consolidation (`src/nse_cash/setups/catalog.py`)**
  - [x] Macro: NIFTY 50 $\text{Close} > \text{EMA}_{20}$.
  - [x] RS Leadership: $\text{RS}_{\text{Percentile}} \ge 0.95$ (Top 5th percentile of universe).
  - [x] Base Consolidation: High/Low range over past 5 sessions $\le 3.0\%$ AND $\text{Close}_T \ge 0.985 \times \text{52-Week High}$.
  - [x] Orders & Risk:
    - Structural Stop: Low of 5-day base (enforce $\le 2.20\%$).
    - Tranche 1 (50% Qty): Target $+2.00\%$.
    - Tranche 2 (50% Qty): Target $+6.00\%$ (or Trailing Low).

- [x] **5.5 Setup 4: Multi-Month Base Breakout & Anchor Retest (`src/nse_cash/setups/catalog.py`)**
  - [x] Breakout Confirmation: Stock broke above 90-day base resistance within last 3 to 7 sessions on $\text{Delivery\_Qty} \ge 2.0 \times \text{SMA}_{20}$.
  - [x] Support Retest: Day $T$ Low touches within $\pm 0.8\%$ of breakout level and holds above it.
  - [x] Dry-Up: $\text{Volume}_T \le 0.55 \times \text{SMA}_{20}(\text{Volume})$.
  - [x] Rejection Tail: $\text{Close}_T > \text{Open}_T$ AND lower shadow $\ge 40\%$ of total candle range $((\min(\text{Open}, \text{Close}) - \text{Low}) / (\text{High} - \text{Low}) \ge 0.40)$.
  - [x] Orders & Risk:
    - Structural Stop: Breakout Support level $- 0.2\%$ (enforce $\le 2.00\%$).
    - Tranche 1 (50% Qty): Target $+2.00\%$.
    - Tranche 2 (50% Qty): Target $+6.00\%$ (or Trailing Low).

- [x] **5.6 Setup 5: Cross-Sectional Residual / Idiosyncratic Momentum (`src/nse_cash/setups/catalog.py`)**
  - [x] Alpha Leadership: $\text{iMOM}_{\text{Percentile}} \ge 0.95$ (Top 5th percentile of universe).
  - [x] Delivery Shock: $\text{Delivery\_Qty}_T \ge 2.0 \times \text{SMA}_{20}(\text{Delivery\_Qty})$ AND $\text{Close}_T > \text{Open}_T$.
  - [x] Orders & Risk:
    - Structural Stop: Prior day low (enforce $\le 2.20\%$).
    - Tranche 1 (50% Qty): Target $+2.00\%$.
    - Tranche 2 (50% Qty): Target $+6.00\%$ (or Trailing Low).

- [x] **5.7 Asymmetric Runner Skew Score ($S_{\text{runner}}$) & Priority Ranker (`src/nse_cash/setups/ranking.py`)**
  - [x] Calculate composite score:
    $$S_{\text{runner}} = 0.35 \times Z_{\text{Delivery}} + 0.35 \times \text{iMOM}_{\text{Percentile}} + 0.30 \times (1 - \text{PV}_{\text{Percentile}})$$
  - [x] Flag high-conviction runners ($S_{\text{runner}} \ge 0.70$).
  - [x] Rank all qualifying candidates across the 5 setups and pick top candidates up to available portfolio slots (max 4 total).

#### Phase 5 Implementation Notes (deliberate deviations, all marked `ponytail:` in code)

- **File layout:** all 5 setups are pure predicates in `setups/catalog.py` (~180 lines) instead of six per-setup modules. One features table (`features` in DuckDB, `setups/features.py`), one ranker (`setups/ranking.py`).
- **Entry proxy (Plan 7.x pre-committed):** free daily data has no 10:00 AM print. Backtest/scan uses `Open_{T+1}` with a gap-check vs `Close_T x 1.012`; Setup 2's 9:15-9:30 low rule degrades to the same gap-check.
- **Setup 1 accumulation:** the features table stores day-T values only, so Condition A is proxied by today's delivery vs SMA20 and Condition B by today's Z >= 1.5. Upgrade path: persist shock-history columns (e.g. `shock_a_5d`, `z15_count_3d`) in the features table.
- **Setup 1 trend gate:** SMA50 > SMA200 replaced by SMA200 5-session slope > 0 (same persistence intuition, one fewer rolling series; slope column `sma200_slope5` already in the table).
- **Setup 4 breakout age:** "broke out 3-7 sessions ago" proxied by close holding above the 1.02x base level with dry retest volume. Upgrade path: persist `breakout_age` in the features table.
- **Setup 1/5 stops (spec-faithful):** `prev_low` is persisted in the features table; Setup 1 stops at $\min(\text{Low}_T, \text{Low}_{T-1})$ and Setup 5 at the prior day low, exactly as `algos.md` specifies.
- **Warm-up vs PIT:** features are computed over the full lookback window for any symbol that *ever* appears in the PIT universe during it; PIT membership is enforced at read time (`load_features`).
- **Degenerate math:** zero delivery dispersion -> Z = 0 (not NULL); zero down-EMA with positive up-EMA -> RSI(2) = 100. NULL features never match a predicate.
- **Live-data caveat:** current DB holds ~30 sessions, so SMA200/52w/90d-base features are NULL for all symbols and Stage 3 correctly yields zero candidates. Run a 2010+ backfill (`nse-cash sync --from 2010-01-01`) for full signal coverage.

---

### Phase 6: High-Fidelity Simulation & Backtesting Engine

#### Objective
Build a tick-accurate, point-in-time backtesting engine covering 13 years of in-sample calibration (2010–2022) and 3.5 years of out-of-sample walk-forward validation (2023–Present) with exact 2-tranche execution, friction, DP charges, and universal 20% STCG taxation.

#### To-Do List

- [x] **6.1 Temporal Split & Portfolio Architecture (`src/nse_cash/backtest/engine.py`)**
  - [x] Dataset partitioning (CLI presets; the engine itself takes any `--start/--end`):
    - **In-Sample Period:** 2010-01-01 to 2022-12-31 (13 Years)
    - **Out-of-Sample Walk-Forward:** 2023-01-01 to Present (~3.5 Years)
  - [x] Maintain exact ₹5,00,000 capital ledger across 4 discrete slots (₹1,25,000 per slot; ₹62,500 per tranche).
  - [x] Model Overnight Liquid Fund interest yield: 6.5% p.a. earned daily on unallocated cash balance (`liquid_fund_interest` in `tax_friction.py`; accrues in the day loop before decisions).

- [x] **6.2 Discrete 2-Tranche Trade Simulation Loop (built in `backtest/fill_model.py` + the `engine.py` day loop; no separate `trade_manager.py` — the fill model is pure, the loop is ~200 dumb lines)**
    - [x] **Day T+1 10:00 AM Entry:**
    - Entry simulated at 10:00 AM price.
    - Check gap rule: If $\text{Open}_{T+1} > \text{Close}_T \times 1.012 \implies$ Reject entry.
    - Check structural risk: If $\text{Structural\_Stop\_Pct} > 2.20\% \implies$ Reject entry.
  - [x] **Tranche 1 Simulation (50% Qty / ₹62,500):**
    - First touch of $\text{Entry} \times 1.020$ ($+2.00\%$) $\implies$ Tranche 1 fills.
    - On fill, shift Tranche 2 Stop to Breakeven ($\text{Entry} \times 1.000$) at EOD.
  - [x] **Tranche 2 Simulation (50% Qty / ₹62,500):**
    - Evaluated against Daily Low of $T-1$ trailing stop, runner target ($+5\%$ to $+8\%$), or hard Day 5 3:15 PM EOD close.
  - [x] **48-Hour Stall Exit:**
    - On Day $T+2$ at 3:15 PM, if $\text{Close}_{T+2} < \text{Entry} \times 1.008 \implies$ Exit entire position at $\text{Close}_{T+2}$.
  - [x] **Overnight Gap-Down Stop Realization:**
    - If $\text{Open}_t < \text{Stop\_Loss\_Price} \implies$ Exit price = $\text{Open}_t$ (realizes full gap-down slippage).

- [x] **6.3 Exact Friction & Universal STCG Taxation Calculator (`src/nse_cash/backtest/tax_friction.py`)**
  - [x] Entry Friction Breakdown (on ₹1,25,000 order):
    - STT: $0.10\%$ on buy side
    - Stamp Duty: $0.015\%$ on buy side
    - Exchange Transaction Fee: $0.00345\%$
    - SEBI Fee: $0.0001\%$
    - GST: $18\%$ on (Exchange Fee + SEBI Fee)
    - Slippage: $0.05\%$
  - [x] Tranche Exit Friction Breakdown (on ₹62,500 tranche exit):
    - STT: $0.10\%$ on sell side
    - Exchange Transaction Fee: $0.00345\%$
    - SEBI Fee: $0.0001\%$
    - GST: $18\%$ on (Exchange Fee + SEBI Fee)
    - CDSL / NSDL DP Charge: **₹15.93 flat** per tranche sell day
    - Slippage: $0.05\%$
  - [x] Universal STCG Tax Deduction:
    - Compute net realized P&L per financial year (April 1 to March 31).
    - Deduct **20.0% flat STCG** on net annual profits across all backtest years (2010 to Present).
    - Optional STCG **loss carry-forward** (`stcg_carry_forward` config flag, default on): explicit loss lots expire after 8 assessment-year offsets, matching §74(3) rather than a decay fudge. `run_backtest(carry_forward_stcg=...)` can A/B it.

- [x] **6.4 Portfolio Drawdown Kill Switch (in the `engine.py` day loop; no separate `risk_manager.py`)**
  - [x] Track peak portfolio equity (High-Water Mark).
  - [x] If drawdown from high-water mark reaches $-7.5\%$:
    - Liquidate all active positions at market (next open; circuit-frozen positions deferred to the next session — no fictional exit prices).
    - Enforce mandatory 10 trading days cooling period (zero new entries; `risk.kill_cooldown_days`).

- [x] **6.5 Performance Analytics & Tear-Sheet Generator (`src/nse_cash/backtest/metrics.py`)**
  - [x] Calculate key performance indicators:
    - Post-Tax CAGR (%)
    - Win Rate (%)
    - Profit Factor
    - Maximum Drawdown (%) & Drawdown Duration
    - Average Win $\bar{R}_{\text{win}}$ & Average Loss $\bar{R}_{\text{loss}}$
    - Net Trade Expectancy (%)
    - Annualized Sharpe & Sortino Ratios
    - Monthly / Annual Returns Matrix
  - [x] Export tear-sheet to terminal (Rich table), JSON (`tear_sheet.json`), and Parquet (`events`, `equity`, `trades`). The **event log is the only artifact**; every metric is a pure aggregation over it, with per-setup and per-regime attribution tables.
  - [x] Degenerate-window guards: flat/near-empty equity curves report "n/a" instead of a 480,000 "Sharpe".

- [x] **6.6 Backtest CLI Subcommand (`src/nse_cash/cli/backtest_cmd.py`, wired into `cli/main.py`)**
  - [x] Implement `nse-cash backtest`:
    - `--in-sample` (Runs 2010–2022)
    - `--walk-forward` (Runs 2023–Present)
    - `--full` (Runs 2010–Present and outputs complete comparative report)
    - `--start/--end` explicit replay (R5: March 2020 kill-switch stress, any window)
    - `--carry-forward-stcg/--no-carry-forward-stcg` A/B of the loss carry-forward

---

### Phase 7: Portfolio Ledger, State Machine & Daily 10:00 AM Action Sheet

#### Objective
Build the SQLite production ledger, trade state machine, and the visual 10:00 AM Daily Action Sheet generator providing exact Dual-GTT Kite orders.

#### To-Do List

- [x] **7.1 Production SQLite Ledger & State Machine (`src/nse_cash/execution/ledger.py`)**
  - [x] SQLite Database at `data/db/portfolio_ledger.sqlite3`.
  - [x] Schema — **[DEVIATION, event-sourced]**: `trades` (identity rows) + append-only `events` (fill-model `TradeEvent`s) + `notes` (HWM, cooldown, deletions). The planned `slots`/`cash_ledger` tables and hand-rolled `TrancheState` machine are superseded — see Phase 7 Implementation Notes below.
  - [x] State transition validation: fold guards raise on double entry, exit-before-entry, double T1, exit-after-close (a corrupt book fails loudly, never lies).

- [x] **7.2 Daily Morning Scanner (`nse-cash scan`; Phase 7.2's `scanner.py` was never needed — `funnel/pipeline.decide_entries` already is the scanner)**
  - [x] Runs at 9:55 AM IST via `nse-cash scan`.
  - [x] Loads previous evening's data (after 6:45 PM sync).
  - [x] Evaluates Macro Regime (Stage 1). If failed, outputs 100% Cash defensive notification.
  - [x] Evaluates Liquidity, Governance & Circuit filters (Stage 2).
  - [x] Runs the 5 quantitative setups and scores $S_{\text{runner}}$ (Stage 3).
  - [x] Checks open slots in SQLite ledger (0 to 4 available) — filled trades AND recorded pending signals.
  - [x] Allocates position size via `engine.slot_quantity`: slot capital **net of exact buy friction** (one shared sizing rule with the backtest).
  - [x] Splits into Tranche 1 ($\lfloor \text{Qty} / 2 \rfloor$) and Tranche 2 ($\text{Qty} - \text{Tranche 1 Qty}$).
  - [x] Computes exact Dual-GTT target and stop price levels (NSE ₹0.05 tick).

- [x] **7.3 Visual 10:00 AM Daily Action Sheet Renderer (`src/nse_cash/execution/action_sheet.py`)**
  - [x] Render Rich terminal table matching BRD Section 9.1:
    - Capital & Open Slots Summary Header
    - Candidate Action Table: Symbol, Sector, Action (BUY), Entry Ref, Max Entry (+1.2% Cap), Tranche 1 Target (+2.0%), Tranche 2 Target (+6.0%/Trail), Structural Stop (-2.2% max), Qty (T1/T2)
    - Step-by-step Kite Manual Playbook (Limit Buy order + 2 independent GTT OCO Sell orders)
    - EOD 3:20 PM Routine (Stall check & Breakeven stop adjustment)

- [x] **7.4 Ledger Management CLI Commands (`src/nse_cash/cli/main.py` + `cli/ledger_cmd.py`)**
  - [x] `nse-cash ledger`: Display active positions, unrealized P&L, slot allocation, GTT trigger levels, integrity problems and corporate-action warnings.
  - [x] `nse-cash ledger --record-fill SYMBOL PRICE`: Record 10:00 AM fill.
  - [x] `nse-cash ledger --record-exit SYMBOL PRICE REASON`: Record tranche exit (T1_TARGET, T2_TARGET, STOP_HIT, STALL_EXITED, TIME_EXITED, KILL_SWITCH).
  - [x] `nse-cash ledger SYMBOL=PRICE ...`: 3:20 PM check — stall conditions, breakeven reminder, kill-switch equity. Named tokens (not positional prices) so a swapped pair hard-fails instead of mis-prompting.
  - [x] Plus: `--record-signal` (pre-commit tonight's candidates), `--record-gap-rejected` (release a signal killed at the ceiling), `--delete TRADE_ID REASON` (tombstone a wrong receipt; facts kept), `--skip-reconcile`.

---

### Phase 8: Test Suite, Benchmarking, Production Runbook & Developer Guide

#### Objective
Establish a 100% deterministic test harness, validate backtest metrics against BRD targets, write the production operational runbook, and document the architecture for zero-intervention developer onboarding.

#### To-Do List

- [x] **8.1 Mathematical & Unit Test Suite (`tests/unit/`)** — **[DEVIATION]** shipped under the files listed; the per-plan filenames were not created (see Phase 8 Implementation Notes).
  - [x] Corporate action multipliers (1:1 bonus, 5:1 split, chained) → **`tests/unit/test_adjuster_universe.py`**.
  - [x] Parkinson $PV_5$ closed-form, Wilder RSI(2), Mansfield RS, OLS residual alpha → **`tests/unit/test_setups.py`** (feature-engine class).
  - [x] 5 setups on deterministic synthetic OHLCV bars → **`tests/unit/test_setups.py`**.
  - [x] STT, DP ₹15.93, GST, slippage, 20% STCG + carry-forward → **`tests/unit/test_tax_friction.py`** (13).
  - [x] $S_{\text{runner}}$ calculation & sorting → **`tests/unit/test_setups.py`** (ranking class).
  - [x] Fill-model golden scenarios (27) → `tests/unit/test_fill_model.py`; engine money/kill-switch/STCG scenarios (20) → `tests/unit/test_engine.py`; entry path (7) → `tests/unit/test_engine_entry_path.py`.
  - [x] BRD §10.1 verdict computation & LTP token parser → **`tests/unit/test_phase8_verdicts.py`** (10).

- [x] **8.2 End-to-End & Integration Test Suite (`tests/integration/`)** — **[DEVIATION]** as mapped below.
  - [x] Bhavcopy/MTO → DuckDB → features → funnel → action sheet → **`tests/integration/test_storage_pipeline.py`**, **`tests/integration/test_funnel_pipeline.py`**, `tests/unit/test_bhavcopy_mto.py`.
  - [x] Deterministic backtest across runs → **`tests/unit/test_engine.py::TestReproducibility`** (byte-identical equity curve).
  - [x] Slot/sector capacity & GTT lifecycle → **`tests/unit/test_stage4_gate.py`**, **`tests/unit/test_ledger.py`** (23), `tests/unit/test_pipeline.py`.
  - [x] **[ADDED] Live-vs-backtest equivalence** → **`tests/integration/test_live_backtest_equivalence.py`** — the Phase 7 thesis as a test: the engine's event log mirrored into a SQLite `Ledger` as operator receipts, then invested/proceeds/P&L/STCG asserted paisa-identical and engine cash = ledger cash + interest. Also pins `engine.slot_quantity` as the shared (net-of-friction) sizing rule.

- [x] **8.3 Performance Metric Benchmarking** — **[DEVIATION, verdict not gate]**
  - [x] BRD §10.1 target-vs-actual **PASS/FAIL/MISSING verdict table** rendered in every backtest tear sheet (`metrics.brd_targets_verdict` + `backtest_cmd`); `verify_brd_targets` for tooling. Targets pinned to the BRD by unit test:
    - Post-Tax CAGR: $\ge +14.0\%$ · Win Rate: $48.0\% - 56.0\%$ · Profit Factor: $1.55 - 1.85$ · Max Drawdown: $< 8.5\%$ · Average Win: $+2.40\% - +2.70\%$ Net · Average Loss: $-1.80\% - -1.90\%$ Net · Expectancy: $+0.40\% - +0.75\%$ Net per trade.
  - [x] A MISSING row (incl. zero filled trades) is rendered distinctly and fails verification — no data never impersonates bad performance, and neither ever reads as PASS.
  - [x] No pytest asserts these ratios against the live walk-forward endpoint: the endpoint is "Present" (a moving dataset); a flaky CI gate trains people to ignore red. The operator reads the verdict every run; the verdict *computation* is what is unit-tested.

- [x] **8.4 Production Operations Runbook (`docs/RUNBOOK.md`)**
  - [x] Created — see the doc for the full procedure:
    - **6:45 PM IST Daily:** `nse-cash sync` + after-sync reconciliation (the DRIFT decision table).
    - **9:55 AM IST Morning:** `nse-cash scan` → `nse-cash ledger --record-signal`.
    - **10:00 AM IST Market Open:** Verify price $\le \text{Close}_T + 1.2\%$, place manual limit buy on Kite, place Dual-GTT OCO orders. Record fill via `nse-cash ledger --record-fill SYMBOL PRICE`.
    - **3:20 PM IST EOD Routine:** `nse-cash ledger SYMBOL=PRICE ...` (named LTP tokens; malformed/unknown hard-fail). If Tranche 1 hit target, modify Tranche 2 stop to Breakeven. If Day $T+2$ price $< +0.80\%$, cancel GTTs and sell at market.
    - Plus: corporate-action GTT modification, missed-sync recovery, kill-switch day, receipt correction (`--delete`), known `ponytail:` ceilings.

- [x] **8.5 Developer Architecture Guide (`docs/ARCHITECTURE.md`)**
  - [x] Created: the one-brain/two-consumers diagram, a where-every-rule-lives table, module map, storage split (DuckDB market facts vs SQLite event-sourced book), ledger fold/reconciliation explanation, and conventions. Schemas are NOT duplicated into the doc — they live in code, golden-tested; a second copy would be a second source of truth.

- [x] **Phase 8 complete** — 8.4/8.5 delivered as `docs/RUNBOOK.md` and `docs/ARCHITECTURE.md` (see above).

#### Phase 8 Implementation Notes (Brainstorm Decisions, 2026-09-18)

- **The 8.1/8.2 checklist was a re-index, not a gap.** Most planned test files already existed under other names (mapped above); creating duplicate files to satisfy a checklist is the opposite of lazy. The plan now maps each planned item to the file that actually ships it.
- **[ADDED] Live-vs-backtest equivalence test — the one test Phase 8 must add and didn't have.** Phase 7's thesis ("live and backtest cannot drift by construction") was prose until `tests/integration/test_live_backtest_equivalence.py`: the engine runs over synthetic fixtures while every event is mirrored into a SQLite `Ledger` exactly as operator receipts (`--record-signal`/`--record-fill`/`--record-exit`/`check-eod`), then invested, proceeds, realized P&L, STCG and cash are asserted paisa-identical (1-paise tolerance, because the trades frame rounds money to 2dp while the ledger settles unrounded). Writing it caught **three real divergences**: (1) live sizing was gross while the engine sizes net of buy friction — fixed by making `engine.slot_quantity` the single shared sizing rule for action sheet and `--record-signal`; (2) the ledger's full-position-exit split duplicated the full quantity instead of splitting it like `fill_model._exit_all` — the most common exit path (stop-out before T1) would have settled the wrong money; (3) the plan's operational text referenced a `--check-eod` flag and positional prices that never existed — the runbook now matches the CLI.
- **[DEVIATION] BRD §10.1 is a verdict, not a pytest gate.** The benchmark endpoint is "Present" — a moving dataset — so a hard CI assertion would flake and train everyone to ignore red. Instead every tear sheet renders a target-vs-actual PASS/FAIL/MISSING table (`metrics.brd_targets_verdict`, pure and unit-tested with synthetic numbers); `verify_brd_targets` fails tooling on FAIL/MISSING. Missing data never impersonates bad performance, and neither ever reads as PASS.
- **[ADDED] `--delete TRADE_ID REASON` receipt tombstoning.** Reconciliation says "fix the book", but a wrong receipt can't be un-recorded; facts are kept, only the trade is voided — the receipt book stays append-only.
- **Deliverables:** `docs/RUNBOOK.md` (four clock anchors, DRIFT decision table, failure procedures, `ponytail:` ceilings) and `docs/ARCHITECTURE.md` (one-brain/two-consumers, where-every-rule-lives table, storage split, ledger fold explanation). Neither duplicates schemas or playbook text that live in code — they reference it.
- **Suite state at close: 202 tests, all passing** — 191 pre-existing, plus 1 equivalence test (`tests/integration/test_live_backtest_equivalence.py`) and 10 new unit tests (`tests/unit/test_phase8_verdicts.py`: BRD verdict computation + LTP token parser).

---

## Phase 6 Implementation Notes (Brainstorm Decisions, 2026-09-13)

Agreed recommendations to carry into Phase 6 (marked `Rx` below):

- **[DONE] R1 — One brain, two consumers.** The 4-stage funnel (regime → governance → rank → Stage-4 gates) is extracted into `nse_cash.funnel.pipeline.decide_entries(store, config, trade_date, occupied_slots, active_sectors)`. `nse-cash scan` renders its output; the Phase 6 backtest engine replays it day by day. Scan/backtest divergence is structurally impossible. Tests: `tests/unit/test_pipeline.py`.
- **[DONE] R2 — Historical corporate-action coverage.** The live NSE corporate-actions API retains only ~365 days, so a 2010–2022 backtest on it is fiction. Added:
  - `nse-cash corporate-history`: one-time seed of decades of split/bonus history from Yahoo Finance into `corporate_actions` (yfinance lazy-imported; data prep only, not a runtime dependency), then `refresh_adjustments` recomputes adjusted columns.
  - `detect_unexplained_gaps`: audits raw overnight close-to-close moves beyond ±25% (impossible within NSE 20% circuit bands) against recorded actions; unexplained gaps = missing action or suspect raw data. JSON report at `reports/corporate_action_audit.json`.
  - Tests: `tests/unit/test_corporate_history.py`.
- **[DONE] R3 — Codify the fill model before `backtest/trade_manager.py`** (each rule gets a golden test). Implemented in `src/nse_cash/backtest/fill_model.py` (pure functions: `simulate_entry_day`, `simulate_open_day`, `refresh_stops_eod`) + `src/nse_cash/backtest/tax_friction.py` (exact ₹-level friction). `trade_manager.py` is now a thin loop over these. Rules as built:
  - Pessimistic intraday order: if a day's low touches the stop AND high touches the target, the **stop wins**.
  - Limit-buy semantics: entry fills at `Open_{T+1}` if `Open_{T+1} <= max_entry` (gap-down accepted); gap-up opens beyond the band reject for the day.
  - Gap-through stop exits at `Open_t`, never at the stop price.
  - Breakeven arming is EOD: T1 fill on day D protects T2 only from day D+1.
  - Spec conflict resolved via `RiskConfig.runner_trail: breakeven | prev_low` (default `breakeven`, env-overridable `NSE_CASH_RISK__RUNNER_TRAIL`); A/B both in-sample.
  - Raw-price simulation after one conversion of adjusted levels at entry (friction/GTT/ledger live in rupees); corporate-action splits/bonuses tracked on `SimPosition` and applied to stop/target/qty mid-trade.
  - Tests: `tests/unit/test_fill_model.py` (27 golden scenarios, hand-computed numbers), `tests/unit/test_tax_friction.py` (13 tests).
- **[DONE — resolved differently] R4 — Performance policy.** No `compute_features_range` was added: the engine's warm-up calls the existing `compute_features(chunk_end)` in ~1-year chunks (13 calls instead of ~4,000 per-day recomputes) and precomputes the regime table once via `evaluate_market_regime_range` (which existed unused) plus historical circuit-hit governance rows in one pass; `decide_entries` accepts an optional precomputed regime row so the single-brain funnel is preserved. The day loop stays row-wise and simple; if a full 13-year run ever hurts, the vectorized pre-filter idea remains on the shelf.
- **[DONE] R5 — Validation gates**:
  - Golden-scenario ledger tests: `tests/unit/test_engine.py` (20 scenarios — gap-reject, same-day stop+target with stop-wins, stall at T+2, breakeven save, kill-switch + 10-day cooldown, END_OF_RUN liquidation, STCG FY math incl. loss lots/expiry) and `tests/unit/test_engine_entry_path.py` (7 scenarios — entry sizing net of exact buy friction, same-day T1 fill, sector sentinel/collision, entry-day corporate actions, capital never negative).
  - Backtest CLI `--start/--end` flags beyond the three presets: done (replay March 2020 for the kill switch).
  - Reproducibility: the S_runner sort carries a symbol tiebreak so set-iteration order never leaks into allocation; the engine is a deterministic calendar loop over sorted trading dates.
  - Known hypothesis to check in-sample: Setups 2/3/4 can never clear S_runner ≥ 0.70 except by luck (their primary factors — RSI, RS percentile — do not enter the score). Expect heavy S1/S5 skew; if confirmed, consider per-setup conviction thresholds (in-sample only). The per-setup attribution table in the tear sheet is the instrument for this check.
- **[DONE] Overnight liquid-fund interest**: 6.5% p.a. accrues daily on unallocated cash in the engine loop (`liquid_fund_interest` in `tax_friction.py`), before decisions, so equity is honest.
- **[DONE — with an honest caveat] Governance coverage**: circuit-hit exclusions replay historically from bars (one precomputed pass). ASM/GSM stage-2 lists cannot be reconstructed historically from free data, so pre-sync-availability years run with those two filters effectively off. The tear sheet states the coverage window rather than pretending the filter ran. Honest beats aspirational.

---

### Phase 7 Implementation Notes (Brainstorm Decisions, 2026-09-18)

- **[DEVIATION] Event-sourced ledger, not a second state machine (§7.1).** The plan's `slots`/`cash_ledger` tables and hand-rolled `TrancheState` transitions are replaced by: `trades` (identity row: symbol, signal date, slot, sector, signal levels, T1/T2 qty) + append-only `events` (the fill model's `TradeEvent` rows) + `notes` (operator state: HWM, cooldown, deletions). Position state is a fold of events over `SimPosition`; cash is settled by the engine's own `_settle_position_money`. Live and backtest cannot drift by construction — the same principle as Phase 6's R1, applied to the ledger. Guards in the fold (double entry, exit-before-entry, double T1, exit-after-close) make a corrupt book raise instead of lie. Golden tests: `tests/unit/test_ledger.py` (23).
- **[ADDED] The live kill switch (plan §7.4 omitted it).** `--check-eod` computes equity from close marks, tracks HWM in notes, and on a ≥7.5% drawdown prints the liquidate-at-next-open order and arms a 14-calendar-day (≈10-trading-day) cooldown that `scan` enforces as an ENTRY HALT. `ponytail:` calendar-day approximation of trading days.
- **[ADDED] After-sync reconciliation.** `reconcile()` replays each open trade's real bars through `simulate_entry_day`/`simulate_open_day` and diffs against the recorded events: a model exit the ledger doesn't have = forgotten `--record-exit`; a fill-price mismatch = a wrong receipt. The operator is a receipt book, the model audits the operator — never the reverse.
- **[ADDED] Corporate-action GTT warnings.** Bonus/split ex-dates within 3 days of an open trade print exact modified triggers (levels × factor, qty ÷ factor) — the live counterpart of the fill model's mid-trade `_apply_corporate_action`. Reconciliation skips action-affected holds with an explicit verify-manually note instead of replaying wrong levels.
- **[ADDED] NSE tick rounding (₹0.05).** All sheet/GTT prices rounded (`round_to_tick`); Kite rejects other levels. Raw math shown alongside so nothing is hidden.
- **Signal pre-commitment.** `ledger --record-signal` (and scan's ledger-aware capacity) records tonight's accepted candidates before the fill exists, so two scans on consecutive evenings cannot over-allocate slots; `--record-gap-rejected` tombstones a signal killed at the 10:00 AM ceiling, releasing slot + sector. Trade IDs follow the engine's `{SYMBOL}-{signal_date}` convention.
- **Scan = renderer only.** `scan_cmd` lost its private table renderer; `execution/action_sheet.py` renders the BRD §9.1 sheet (capital/slots header + tick-rounded Dual-GTT table + playbook) from `decide_entries` output with live slot/sector state passed through. Phase 7.2's `scanner.py` module was never needed — `funnel/pipeline.decide_entries` already is the scanner.

## Verification & Acceptance Checklist

| Subsystem | Verification Command | Acceptance Criteria |
|---|---|---|
| **Data Ingestion** | `nse-cash sync --from 2026-08-01 --to 2026-09-06` | Ingests Bhavcopy + MTO, zero missing dates, parses `EQ` series cleanly into DuckDB. |
| **Corporate Actions** | `nse-cash status` | Verifies backward adjustment multiplier on historical splits/bonuses. |
| **Market Regime** | `nse-cash scan` | Correctly flags `OFFENSIVE_LONG` vs `DEFENSIVE_CASH` based on NIFTY 20EMA & 500 Breadth. |
| **Action Sheet** | `nse-cash scan` | Produces Rich 10:00 AM Action Sheet with exact Dual-GTT Kite orders and ₹1,25,000 slot sizing. |
| **Backtest Engine** | `nse-cash backtest --full` | Executes 2010–Present simulation with 2-tranche exits, 48h stall, 0.35% friction, ₹15.93 DP charge, 20% STCG. |
| **Test Suite** | `pytest tests/ -v --cov=src/nse_cash` | 100% tests passing with $\ge 90\%$ code coverage. |
