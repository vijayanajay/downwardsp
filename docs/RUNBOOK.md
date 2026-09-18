# Production Runbook — NSE High-Conviction Cash Swing System

Daily operating procedure for the manual Dual-GTT execution loop. The system
is a deterministic intelligence engine; you are the hands. Everything here
runs locally — no broker APIs, no paid data.

> **The playbook rendered at the bottom of every `nse-cash scan` is the
> canonical order-entry procedure.** This runbook covers the schedule, the
> failure cases, and the recovery paths. If this document and the sheet ever
> disagree, fix whichever is wrong — both are generated from the same config.

---

## 1. The Four Clock Anchors

| Time (IST) | Command | What happens |
|---|---|---|
| **6:45 PM** | `nse-cash sync` | Downloads Bhavcopy + MTO + corporate actions; reconciles the book against real bars (see §4). |
| **9:55 AM** | `nse-cash scan` | 4-stage funnel on yesterday's data; renders the 10:00 AM Action Sheet. |
| **10:00 AM** | (Kite, manual) | Verify LTP ≤ Max Entry, place limit buy, place two GTT OCO sells; record the fill. |
| **3:20 PM** | `nse-cash ledger SYMBOL=PRICE ...` | EOD routine: stall check, breakeven reminder, kill-switch equity check. |

### 9:55 AM — scan

```bash
nse-cash scan
```

- **ENTRY HALT banner** (kill-switch cooldown active): the sheet is
  informational only. Do NOT place orders.
- **DEFENSIVE_CASH regime**: no candidates; stay flat.
- Otherwise: commit the sheet so tonight's re-scan can't double-allocate:

```bash
nse-cash ledger --record-signal
```

This stores each accepted candidate (symbol, slot, sector, levels, qty) as a
pending signal that holds its slot until it fills or is released.

### 10:00 AM — entry

1. From the sheet: LTP ≤ **Max Entry** column? If not → the signal dies:

   ```bash
   nse-cash ledger --record-gap-rejected SYMBOL
   ```

2. Place the Buy Limit for the full qty (T1/T2 split is printed).
3. On fill — one command, the exact broker print:

   ```bash
   nse-cash ledger --record-fill SYMBOL PRICE
   ```

4. Place **two independent GTT OCO sells** per the sheet:
   GTT 1 (T1 qty): target = T1 Target, trigger = Structural Stop.
   GTT 2 (T2 qty): target = T2 Target, trigger = Structural Stop.

### 3:20 PM — EOD routine

Pass every open position's last traded price, one `SYMBOL=PRICE` token each
(unknown/malformed tokens hard-fail — a swapped pair can't silently mis-prompt):

```bash
nse-cash ledger SHOCK=101.25 BANKX=502.10
```

For each position the routine prints:

- **T1 filled** → GTT 2 stop must be **breakeven (your entry price)**. Modify
  it on Kite if not done. (Rule: arming happens at EOD after the T1 fill.)
- **Day T+2 gain < +0.80%** → STALL: cancel both GTTs, sell at market, record:
  `nse-cash ledger --record-exit SYMBOL PRICE STALL_EXITED`
- **Day ≥ 5** → hard time exit: same flow with `TIME_EXITED`.
- Portfolio equity vs high-water mark → **KILL SWITCH** at −7.5% (see §5).

On Kite, GTT fills show as completed orders; record them as they happen:

```bash
nse-cash ledger --record-exit SYMBOL PRICE T1_TARGET    # tranche 1 filled
nse-cash ledger --record-exit SYMBOL PRICE T2_TARGET    # trade closed
nse-cash ledger --record-exit SYMBOL PRICE STOP_HIT     # stop trigger
```

Valid reasons: `T1_TARGET`, `T2_TARGET`, `STOP_HIT`, `STALL_EXITED`,
`TIME_EXITED`, `KILL_SWITCH`.

---

## 2. Corporate Actions (bonus/split) While Holding

`nse-cash ledger` prints a **CORP-ACTION** warning when a held symbol's
ex-date is within 3 days. The ex-date overnight, every level and quantity
changes:

1. The warning prints the exact modified triggers: `levels × factor`,
   `qty ÷ factor` (e.g. 1:1 bonus → targets and stop halve, qty doubles).
2. On the ex-date morning, **modify both GTTs on Kite** to the printed
   triggers before market open.
3. The fill model applies the same factor mid-trade; reconciliation skips
   action-affected holds with an explicit verify-manually note rather than
   replaying wrong levels. Trust the warning's arithmetic, verify on Kite.

---

## 3. Missed Sync / Stale Data

- **Scan warns about stale data**: if yesterday's sync never ran, `scan` and
  `ledger` operate on the latest available session. Do not place orders on
  data older than yesterday's close — run `nse-cash sync` first, then
  re-scan.
- **Sync failed mid-day**: re-run it; it is idempotent (already-ingested
  dates are skipped unless `--force`).
- **`nse-cash status`** shows ingestion coverage (dates, symbols, PIT
  universe size) when in doubt.

---

## 4. Reconciliation — the DRIFT decision table

After every `sync`, `nse-cash ledger` replays each open trade's real bars
through the fill model and diffs against your recorded receipts. The operator
is the receipt book; the model audits the operator — never the reverse.

| DRIFT message | Meaning | Fix |
|---|---|---|
| `model exited on DATE (REASON) but ledger shows the trade open` | A GTT triggered and you didn't record it. | `ledger --record-exit SYMBOL PRICE REASON` (price from Kite's fill). |
| `ledger closed the trade but bars say still open` | You recorded an exit that didn't fill (or bad data). | Verify on Kite: if no fill, delete the wrong exit trade row and re-record correctly (§4.1). |
| `recorded fill ₹X vs model ₹Y` | Fill price ≠ model's min(Open, Max Entry). Usually you filled at a better/worse limit — **your broker print wins**. | No action if Kite confirms ₹X. If it's a typo, re-record (§4.1). |
| `T1 fill state drifted (model …, ledger …)` | T1 GTT fired unrecorded (or vice versa). | Same as row 1 / row 2. |
| `corporate action during hold — verify levels manually` | Replay skipped by design. | Verify the modified GTT triggers per §2. |
| `held N sessions, past the Day 5 time exit` (LEDGER line) | Trade should have time-exited. | Exit at market, record `TIME_EXITED`. |
| `T1 filled but T2 stop not at breakeven` (LEDGER line) | The breakeven modification was missed. | Modify GTT 2 trigger to entry price on Kite now. |

### 4.1 Receipt correction

A wrong receipt cannot be edited — facts are append-only and auditable:

```bash
nse-cash ledger --delete TRADE_ID "wrong fill price, re-recording"
nse-cash ledger --record-fill SYMBOL CORRECT_PRICE
```

`--delete` tombstones the row (`deleted = 1`, reason noted); nothing is
destroyed. Re-record the trade correctly afterwards.

---

## 5. Kill Switch Day

The 3:20 PM equity check tracks the high-water mark in the ledger. On a
drawdown ≥ 7.5% (config `risk.kill_switch_drawdown`):

1. The console prints **KILL SWITCH**: liquidate all open positions at
   **next open** — cancel GTTs, market-sell at open on Kite.
2. Record each: `nse-cash ledger --record-exit SYMBOL OPEN_PRICE KILL_SWITCH`.
3. A **14-calendar-day cooldown** arms automatically (≈ the engine's
   10-trading-day rule; `ponytail:` calendar approximation). `scan` shows
   **ENTRY HALT** for the whole window — informational only.
4. Partial fills / circuit-locked symbols: sell what has a counterparty; if a
   symbol opens locked, it sells the next session with a counterparty (same
   rule the backtest enforces). Record each sale as it executes.

---

## 6. Known Ceilings (honest approximations, marked `ponytail:` in code)

| Ceiling | Reality | Upgrade path |
|---|---|---|
| Kill-switch cooldown = 14 **calendar** days | The backtest uses 10 **trading** days. Over long weekends the live halt is slightly longer. | Count sessions from `daily_bars`. |
| 3:20 PM LTPs are **hand-typed** `SYMBOL=PRICE` tokens | Typo-safe (unknown symbol/bad price hard-fail) but manual. | A quote feed — explicitly out of scope (zero paid APIs). |
| Kill-switch equity marks use each symbol's **latest close** | Intraday at 3:20 the marks are the previous close if bars lag; equity is honest at EOD. | None needed — the check runs at EOD. |
| Entry sizing is **net of buy friction** (`slot_quantity`) | The sheet's qty is exactly what the engine buys; ₹1.25L minus friction is the gross value. | — (this is the fix, not a ceiling). |
| Interest accrues on **wall-clock** day gaps in the ledger | The engine accrues per trading session. Difference is noise-grade for idle cash. | Accrue from the trading calendar. |

---

## 7. Verification

| Concern | Command |
|---|---|
| Full test suite (202 tests) | `.venv/Scripts/python.exe -m pytest tests/ -q` |
| Live-vs-backtest money equivalence | `pytest tests/integration/test_live_backtest_equivalence.py` |
| Book health | `nse-cash ledger` (LEDGER / CORP-ACTION / ENTRY HALT lines) |
| Data health | `nse-cash status` |
