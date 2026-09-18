# Architecture — NSE High-Conviction Cash Swing System

A 10-minute guide to how the code fits together. For operating procedure see
[RUNBOOK.md](RUNBOOK.md); for the roadmap and per-phase decisions see
[ACTION_PLAN_8_PHASES.md](ACTION_PLAN_8_PHASES.md).

## The one diagram

```
                    ┌──────────────────────────────┐
                    │   funnel/pipeline.decide_entries   │
                    │   (regime → governance → setups → stage-4)  │
                    └──────────────┬───────────────┘
                    decisions (one brain, two consumers)
             ┌─────────────────────┴──────────────────────┐
             ▼                                            ▼
 ┌──────────────────────────┐              ┌──────────────────────────────┐
 │ backtest/engine.run_backtest │              │ execution/action_sheet.render │
 │ (SimBook, day loop)          │              │ + operator receipts           │
 └──────────────┬───────────┘              └──────────────┬───────────────┘
                │ events                                  │ events (receipts)
                ▼                                          ▼
 ┌───────────────────────────────────────────────────────────────────────┐
 │        backtest/fill_model  (the ONLY fill rules)                     │
 │   simulate_entry_day · simulate_open_day · force_exit · _exit_all     │
 └──────────────────────────────┬────────────────────────────────────────┘
                                │ SimPosition + TradeEvent fold
                                ▼
 ┌───────────────────────────────────────────────────────────────────────┐
 │  backtest/engine._settle_position_money  (the ONLY money path)        │
 │   buy/sell friction · flat DP-per-sell-day · STCGAccount              │
 └───────────────────────────────────────────────────────────────────────┘

Live and backtest cannot drift by construction: the same brain decides, the
same fill model fills, the same settlement folds the rupees. Pinned by
tests/integration/test_live_backtest_equivalence.py.
```

## Where a rule lives

| Rule | Code | Pinned by |
|---|---|---|
| Regime (NIFTY 20EMA + breadth>50%) | `funnel/market_regime.py` | `test_market_regime.py` |
| Governance exclusions (ASM/GSM/circuit/board) | `core/governance.py` | `test_storage_pipeline.py` |
| 5 setups (pure predicates) | `setups/catalog.py` | `test_setups.py` |
| S_runner ranking + symbol tiebreak | `setups/ranking.py` | `test_setups.py` |
| Stage-4 gates (capacity, sector, gap, risk) | `funnel/stage4_gate.py` | `test_stage4_gate.py` |
| Entry day (gap ceiling, gap-down fill, same-day stop) | `fill_model.simulate_entry_day` | `test_fill_model.py`, `test_engine.py` |
| Continuation days (stops, targets, stall, day-5) | `fill_model.simulate_open_day` | `test_fill_model.py` |
| Intrabar pessimism (stop wins), breakeven arming | `fill_model._exit_all`, `refresh_stops_eod` | `test_fill_model.py` |
| Mid-trade corporate actions | `fill_model._apply_corporate_action` | `test_fill_model.py` |
| Position sizing (net of buy friction) | `engine.slot_quantity` | `test_live_backtest_equivalence.py` |
| Friction & STCG math | `backtest/tax_friction.py` | `test_tax_friction.py` |
| Kill switch (backtest) | `engine.py` day loop (§4) | `test_engine.py` |
| Kill switch (live) | `cli/ledger_cmd._kill_switch_check` + `Ledger.arm_cooldown` | `test_ledger.py` |
| Ledger fold & guards (double entry, exit-before-entry, …) | `execution/ledger.py._apply` | `test_ledger.py` |
| Reconciliation (bars vs receipts) | `execution/ledger.reconcile` | `test_ledger.py` |
| BRD §10.1 verdict table | `backtest/metrics.brd_targets_verdict` | `test_phase8_verdicts.py` |

## Module map

```
src/nse_cash/
├── core/        config (Pydantic), types, math, adjuster, universe, governance
├── data/        NSE fetchers (bhavcopy, MTO, corp actions), DuckDB/Parquet store
├── funnel/      decide_entries — the single decision brain; regime, sector, stage-4
├── setups/      features (DuckDB-backed), catalog (5 pure predicates), ranking
├── backtest/    engine (day loop), fill_model (pure), tax_friction (pure),
│                metrics (pure aggregation), artifacts
├── execution/   ledger (SQLite event-sourced book), action_sheet (Rich renderer)
└── cli/         main (Click wiring): sync · scan · ledger · backtest · status
```

## Storage

Two databases, two purposes (schemas are in code, golden-tested — this doc
deliberately does not duplicate them):

- **DuckDB** (`data/db/nse_market.duckdb`) — market facts and derived frames:
  `daily_bars` (+ adjusted columns), `corporate_actions`, `market_indices`,
  `pit_universe`, `features`, `governance`. Parquet exports per year.
- **SQLite** (`data/db/portfolio_ledger.sqlite3`) — the production book:
  `trades` (identity rows incl. signal levels/qty), append-only `events`
  (the fill model's `TradeEvent`s), `notes` (HWM, cooldown, deletions).
  Position state is a fold of events over `SimPosition`; the plan's
  `slots`/`cash_ledger` tables are views over open trades, not tables.

## The event-sourced ledger

`trades` records what was *decided* (signal levels, quantities, slot,
sector); `events` record what *happened* (fills, exits, gaps, breakeven
arms). The fold (`Ledger.position`) replays events through the fill model's
`SimPosition` with hard guards — double entry, exit-before-entry, double T1,
exit-after-close raise instead of lying. Money is settled by the engine's own
`_settle_position_money`, so a replayed backtest of the same bars and
receipts produces the same cash. `reconcile()` replays real bars over open
trades after every sync and diffs against the recorded events: the operator
is audited by the model, never the reverse.

## Conventions

- **Raw price space** everywhere below the signal layer: friction, GTT
  levels, the ledger all live in rupees. Adjusted prices exist only inside
  the features/ranking layer (converted once at signal time).
- **PIT everywhere**: universe membership and features are point-in-time;
  membership is enforced at read time.
- **`ponytail:` comments** mark deliberate simplifications with their known
  ceiling and upgrade path (see RUNBOOK §6 for the operator-facing ones).
- **Every metric is a pure aggregation** over the event log / equity curve —
  no hidden loop state, byte-identical tear sheets for identical inputs.
