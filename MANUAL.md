# nse-cash Manual

## 1. Overview

**The Simple Explanation**
`nse-cash` is a command-line tool that tells you exactly which Indian stocks to buy and sell for short-term swing trading. Instead of staring at charts all day or paying for expensive software, you run this tool once a day. It downloads free daily market data from the NSE, runs it through strict statistical filters, and spits out a 10:00 AM action sheet. This sheet tells you exactly what to buy, the target prices to sell at, and where to put your stop-loss. You place these orders on your broker (like Zerodha Kite), close the app, and walk away.

**The Detailed Explanation**
At its core, `nse-cash` is a pragmatic, zero-derivatives swing trading system targeting the top 500 liquid stocks on the National Stock Exchange of India (NSE Cash / `EQ` series). It operates by ingesting official, free NSE data (Bhavcopy, Security-wise Delivery/MTO, and Corporate Actions) into a local DuckDB database. The system processes this data, backward-adjusts prices for corporate actions (splits/bonuses), and runs a 4-stage quantitative funnel: evaluating the macro market regime, liquidity/governance rules, 5 orthogonal institutional-grade trading setups, and a strict structural risk gate. It identifies stocks with asymmetric right-tail skew—meaning they have a high probability of outsized multi-day moves. Finally, it mitigates the common "win-rate trap" via a 2-tranche exit architecture, locking in base profits early and leaving a free trailing runner.

## 2. Why & When to Use It

**What problem does this solve?**
Most retail systematic trading fails due to an inverted risk-reward fallacy, ignoring real-world transaction friction (taxes, DP charges, slippage), and relying on fragile black-box machine learning indicators that overfit historical data. Furthermore, retail traders often chase volatile 9:15 AM opening gaps where spreads are wide and institutional algorithms hunt for liquidity. `nse-cash` solves this by hardcoding a 0.35% round-trip friction and 20% STCG tax into all its models, avoiding the 9:15 AM open entirely in favor of a 10:00 AM execution, and relying strictly on volume/delivery absorption mechanics rather than lagging indicators.

**When to use it?**
This tool shines as an end-of-day or pre-market routine. A standard workflow looks like this:
1. **Evening (Post 6:45 PM IST):** Run the sync command to pull the day's NSE data.
2. **Morning (Pre 10:00 AM IST):** Run the scan command to generate the daily action sheet.
3. **Execution (10:00 AM IST):** Open your broker, place the limit buy orders, and immediately configure Dual-GTT (Good Till Triggered) OCO sell orders based on the sheet. No intraday monitoring is required.

**Who is this for?**
Pragmatic retail traders, software engineers, and quantitative developers who want a robust, deterministic, transparent, and low-maintenance execution engine for Indian equities. If you prefer unpretentious math over deep learning buzzwords, this is for you.

## 3. How It Works

**System Architecture & Data Flow**
The lifecycle of a run is entirely local and offline once the daily ZIP files are fetched:
1. **Ingestion (`nse-cash sync`):** Fetches Bhavcopy and Delivery (MTO) files directly from official NSE endpoints. Parses and loads them into a fast, embedded DuckDB instance.
2. **Analysis (`nse-cash scan`):** Evaluates the database through the `nse_cash.funnel.pipeline.decide_entries` pipeline.
3. **Action:** If the macro market regime is positive and setups trigger, it outputs the 10:00 AM Action Sheet to your terminal.

**Key Modules & Components**
- **`src/nse_cash/cli/`**: The command-line entry points (`main.py`, `sync_cmd.py`, `scan_cmd.py`, `status_cmd.py`, `backtest_cmd.py`). This is the only way a user interacts with the system.
- **`src/nse_cash/data/`**: Handles all network requests to the NSE (`bhavcopy.py`, `delivery.py`, `corporate_actions.py`) and manages the local `duckdb` connection and schema (`storage.py`).
- **`src/nse_cash/funnel/` & `src/nse_cash/setups/`**: The heart of the logic. The funnel filters the universe (Regime $\rightarrow$ Governance $\rightarrow$ Setups $\rightarrow$ Risk Gate). The `setups/` directory holds the mathematical definitions for the 5 orthogonal trading strategies (e.g., Float Squeeze, Anchor Retest).
- **`src/nse_cash/backtest/`**: A high-fidelity, point-in-time simulation engine that replays the exact same funnel logic over historical data to generate realistic tear sheets.

**Key Algorithms/Mechanics**
- **Corporate Action Adjustment:** A backward calibration engine mathematically adjusts historical prices and volumes using official split/bonus ratios so indicators aren't corrupted by overnight ex-dates.
- **48-Hour Time-Decay Stall Protection:** Swing impulses must confirm quickly. If a stock fails to gain at least $+0.8\%$ by 3:15 PM on Day T+2, the system cuts the trade at market price.
- **Composite Conviction Score ($S_{runner}$):** A mathematical score that ranks candidates based on delivery volume shocks, Parkinson volatility compression, and idiosyncratic momentum.

## 4. The Engineering Philosophy

**Simplicity and Determinism**
Following Kailash Nadh's engineering mindset, this project rejects complex neural networks, heavy frameworks, and over-engineered ORMs. If a simple delivery volume check mixed with a moving average beats a Graph Neural Network post-tax, the simple check is used.

**Deliberate Simplifications**
- **Zero API Boilerplate:** Broker API automated order placement is strictly out of scope. APIs disconnect, tokens expire, and webhooks fail. A simple CLI generating a manual sheet for Dual-GTT execution is vastly more reliable and auditable for retail swing trading.
- **Flat Data Stack:** No PostgreSQL, no Redis. Standard file formats (Parquet) and an embedded, heavily optimized OLAP database (`duckdb`) mixed with `polars` for vectorized transformations.
- **Pragmatic Trade-offs:** The backtester deliberately deducts a flat 20.0% STCG tax and fixed DP charges across all historical years, trading slight historical tax-code inaccuracy for brutal, worst-case real-world margin-of-safety.

## 5. Practical "How-To" Guide

**Prerequisites & Installation**
- **Requirements:** Python 3.11+
- **Setup:** Clone the repository and install the package with dependencies:
  ```bash
  pip install -e .[dev]
  ```
  This installs required packages like `duckdb`, `polars`, `click`, and `rich`.

**Execution & Running**
All commands are executed via the `nse-cash` binary.

- **`nse-cash sync`**
  *What it does:* Downloads and ingests the official daily Bhavcopy, Delivery (MTO), and Corporate Actions.
  *Why use it:* Run this every evening after 6:45 PM IST to update your local database.
  *Example (Latest day):* `nse-cash sync`
  *Example (Backfill):* `nse-cash sync --from 2023-01-01 --to 2024-01-01`

- **`nse-cash status`**
  *What it does:* Verifies data integrity, date ranges, and corporate action adjustments.
  *Why use it:* Run this to ensure your database isn't missing trading days or to check how many symbols are in your dynamic Top 500 universe.
  *Example:* `nse-cash status`

- **`nse-cash scan`**
  *What it does:* Runs the 4-stage funnel and generates the 10:00 AM Manual Action Sheet.
  *Why use it:* Run this every morning before the market opens (or the night before) to get your exact trading instructions for the day.
  *Example:* `nse-cash scan` (defaults to latest synced date) or `nse-cash scan --date 2024-05-10`

- **`nse-cash backtest`**
  *What it does:* Runs the high-fidelity point-in-time backtest engine.
  *Why use it:* Use this to verify the system's edge over time, or test how the system behaved during a specific crisis.
  *Example (Walk-forward):* `nse-cash backtest --walk-forward`
  *Example (Specific range):* `nse-cash backtest --start 2020-02-01 --end 2020-05-31`

**Testing & Verification**
The project relies on standard `pytest`. The test suite verifies the core mathematical logic, pipeline decision-making, and data ingestion robustness.
- Run tests: `pytest tests/`
- Check coverage: `pytest --cov=src tests/`
*When writing new tests:* Ensure they verify the deterministic logic without needing live network access. Use fixtures for mock duckdb databases.

**Troubleshooting & Edge Cases**
- *Error: "No market database found"* -> You haven't initialized your local DuckDB. Run `nse-cash sync` to pull down the latest data.
- *Missing Days / Stale Data* -> If `nse-cash status` flags missing weekdays, run `nse-cash sync --force` for the affected date range to overwrite and repair the database.
- *Stage 1 Halt* -> If `nse-cash scan` prints a red `DEFENSIVE_CASH` alert, this is normal. The market breadth is poor, and the system is protecting your capital by halting new entries.