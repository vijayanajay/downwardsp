# Findings Report — Weekly Sweet-Spot Study (NSE 500)

**Study window:** Sep 2021 → Aug 2026 (6 calendar years) · **Universe:** 423 liquid NSE stocks (of 501 downloaded; 78 dropped for <80% bar coverage) · **Events:** 519,785 stock-day entries
**Companion artifacts:** `reports/sweet_spot_study.html` (visual report) · `data/results/*.csv` (all tables) · `scripts/` (reproducible pipeline)

---

## 1. Executive Summary

The question this study set out to answer: *how often can a liquid Indian stock hand me +X% within a week, and how often can the same stock do it — so I can plan a 1–2%-per-week system?*

The answer has two halves, and only one of them is good news.

1. **Touches are abundant.** 82.7% of all stock-day entries see +1% at some point within 5 sessions. 65.6% see +2%. Even +8% is touched 14.5% of the time. Opportunity, measured as "the move happens somewhere in the window," is everywhere.
2. **Capture is not.** Reaching +1% *before* touching −2.2% happens only 64.3% of the time. For +2% it is 50.4%. After 0.35% round-trip friction and 20% STCG, **every fixed target from 0.5% to 8% has negative net expectancy** for unconditional daily entries. The least-bad (8%) still loses −0.35% per attempt.

Three findings sharpen the verdict:

- **No free lunch anywhere in the cross-section.** Taking each of the 423 stocks at *its own best target*, not a single stock's unconditional first-passage win rate exceeds its breakeven rate. There is no magic stock that routinely hands out +2% before −2.2% often enough to break even.
- **The stop line is the killer.** The median 5-day adverse excursion is −2.66% — i.e., the typical stock-day *crosses* the −2.2% stop. 56.9% of all stock-days touch −2.2% at some point in the window. Upside and downside violence are near-symmetric (median MFE +3.10%), which is exactly why touch-frequency flatters and capture doesn't.
- **The cross-section is tight.** At the 2% target, per-stock win rates span only ~37%–57% (IQR 49%–53%). Stock *picking* alone cannot bridge a 25.5-percentage-point gap to breakeven.

**Bottom line:** the sweet spot is not a number to be read off this curve. Unconditional entry loses at every target; the profitable number is the one your *entry conditions* can push above the breakeven line. The study quantifies exactly how big that push must be (Section 6).

---

## 2. Study Design (recap)

- **Entry model:** buy at the close of every session T, for every stock, every day (no conditioning — this is the *baseline* every filter must beat).
- **Window:** next 5 trading sessions (a "week" of trading).
- **Outcomes per target T ∈ {0.5, 1, 1.5, 2, 2.5, 3, 4, 5, 6, 8}%:**
  - **Win** — daily high touches +T% before the daily low touches −2.2% (day-resolution; same-day tie counts as stop, conservative).
  - **Stop** — −2.2% touched first.
  - **Timeout** — neither touched: marked at the T+5 close (realistic; no fantasy "loss capped at stop").
- **Costs:** 0.35% round-trip friction (STT, stamp, DP, GST, slippage per BRD §2.2) + 20% STCG on net wins (July 2024 regime), applied to every event.
- **Data:** Yahoo Finance daily bars, auto-adjusted (splits/bonuses handled); NIFTY 50 for regime conditioning.

---

## 3. The Core Curve: Touches vs Capture

| Target | P(MFE ≥ T) — "touch at all" | P(win before −2.2%) | Breakeven P needed | Edge (p − p*) | Net exp/entry | Winning stock-days / yr (universe) |
|---:|---:|---:|---:|---:|---:|---:|
| 0.5% | 91.6% | 71.5% | 118.1% | −0.47 | −0.52% | 74,345 |
| **1.0%** | **82.7%** | **64.3%** | **99.6%** | **−0.35** | **−0.44%** | **66,793** |
| 1.5% | 73.9% | 57.0% | 86.2% | −0.29 | −0.42% | 59,301 |
| **2.0%** | **65.6%** | **50.4%** | **75.9%** | **−0.26** | **−0.41%** | **52,390** |
| 2.5% | 58.1% | 44.5% | 67.8% | −0.23 | −0.41% | 46,259 |
| 3.0% | 51.3% | 39.3% | 61.3% | −0.22 | −0.40% | 40,838 |
| 4.0% | 39.7% | 30.5% | 51.4% | −0.21 | −0.39% | 31,708 |
| 5.0% | 30.7% | 23.6% | 44.3% | −0.21 | −0.38% | 24,558 |
| **6.0%** | **23.9%** | **18.4%** | **38.9%** | **−0.20** | **−0.37%** | **19,146** |
| 8.0% | 14.5% | 11.3% | 31.3% | −0.20 | −0.35% | 11,721 |

How to read this table:

- **Columns 2→3 is the abundance illusion.** Dropping from "touched at all" to "touched before the stop" costs 18pp at +1%, 15pp at +2%, and 8–9pp at 4–8%. The wider the target, the more the stop dominates the race.
- **The breakeven line (col 4) is unbeatable at small targets by construction.** At +1%, you would need to win 99.6% of the time; the market gives 64.3%. The gap *narrows* as the target widens — the curve is flattest at 4–8% — but never crosses zero.
- **Net expectancy (col 6) is flattest at 4–8%** (−0.35% to −0.39%) and worst at tiny targets (−0.52% at 0.5%). Friction eats small targets alive; the stop eats large ones; the balance is least-bad around 4–6%.
- **Shots/yr (col 7) is universe-wide**, i.e., every winning stock-day in 423 stocks. A 4-slot portfolio with sector caps captures a few hundred per year at best. Raw opportunity was never the constraint — *positive-expectancy* opportunity is.

**The "1–2% per week" ambition, confronted with arithmetic.** At the 2% target, each unconditional attempt costs −0.41% net. Trading daily, that is ~250 attempts/year × −0.41% ≈ −100% arithmetic drift before compounding — ruin, faster the more you trade. Frequency cannot rescue negative expectancy; it accelerates it. This is the single most important sentence in this report.

---

## 4. Finding: No Stock Beats Breakeven — Anywhere

Per-stock analysis (`stock_summary.csv`), taking each stock at whichever tested target gives it its best win-rate edge:

- **0 of 423 stocks** have a positive unconditional edge at any tested target.
- At the 2% target, per-stock win rates span **37.3% (HINDUNILVR) to 58.9% (CHOICEIN)** with a median of 50.8% — an interquartile band of just 48.6%–52.7%. The dispersion band is *tight*.
- The best stock in the universe at the 2% target (CHOICEIN, 58.9%) still wins far below the 75.9% breakeven requirement. Even the most violent names (HEG 703 hits, BHEL 695, ENGINERSIN 692 of ~1,234 events) top out near 57%.

**Implication:** screening for "the right stocks" cannot, by itself, produce a profitable fixed-target weekly system. The cross-section simply does not contain a stock whose unconditional 5-day geometry clears friction + tax + a 2.2% stop. Whatever edge exists must come from **conditioning on state** (when to enter), not from **which stock** alone.

---

## 5. Finding: The Stop Line, Not the Target, Is the Enemy

Pooled excursion statistics over 5 sessions:

| Statistic | Value |
|---|---:|
| Median MFE (best upside touch) | **+3.10%** |
| MFE p75 / p90 | +5.81% / +9.54% |
| Median MAE (worst downside touch) | **−2.66%** |
| MAE p10 / p05 | −7.91% / −10.10% |
| **P(stock-day touches −2.2% at some point)** | **56.9%** |
| Median 5-day close-to-close return | +0.15% |
| P(5-day return > 0) | 51.6% |

The typical stock-day ventures +3.10% upside *and* −2.66% downside. More than half of all stock-days cross the −2.2% line. So while "the move happens" 83% of the time at +1%, the *sequence* — which level is touched first — is close to a coin flip once friction and tax are loaded onto the losses.

This is the statistical justification for three BRD design choices, now empirically grounded:

1. **A structural stop ≤2.2% is a heavy tax** in this universe — 57% of stock-days would trigger it. Any system carrying this stop must win the first-passage race well above 50%, i.e., must have real timing edge.
2. **The 48-hour stall rule matters.** Since the median stock-day *does* hit −2.2% eventually within a week, exiting dead trades at T+2 (before the stop is reached) converts part of that 57% from −2.55% net losses into scratches. This study's fixed-stop accounting is what a system *without* a stall rule experiences.
3. **Small fixed targets are friction-dominated.** At 0.5–1%, breakeven requires >99% accuracy. If you want small targets, they must be *partial* exits (the BRD's Tranche-1 at +2%), not the whole position.

---

## 6. What the Filter Must Achieve (the quantified bar)

The gap between measured win rate and breakeven, per target, expressed two ways:

| Target | Measured p | Breakeven p* | Absolute lift needed | Relative lift needed |
|---:|---:|---:|---:|---:|
| 1.0% | 64.3% | 99.6% | +35.4 pp | 1.55× |
| **2.0%** | **50.4%** | **75.9%** | **+25.5 pp** | **1.51×** |
| 3.0% | 39.3% | 61.3% | +22.0 pp | 1.56× |
| 4.0% | 30.5% | 51.4% | +20.9 pp | 1.69× |
| 6.0% | 18.4% | 38.9% | +20.5 pp | 2.11× |
| 8.0% | 11.3% | 31.3% | +20.0 pp | 2.77× |

- In **absolute** terms the bar is flattest at 4–8% (~20pp). In **relative** terms it is cheapest at **2% (1.51×)**: a filter that makes wins 1.5× more likely than the unconditional baseline makes +2% viable.
- That alignment is not a coincidence — it is the same mathematics that produced the BRD's asymmetric 2-tranche design with Tranche-1 at +2%. This study is the empirical case for it: **the 2% target has the most achievable filter bar of any tested target.**
- For calibration: the regime filter (NIFTY above 20-EMA) is worth only +3.5pp at the 1% target and +4pp at 2% — real, but a tenth of what's needed. Day-of-week is worth ~4.4pp (Tue best, Fri worst). Useful tie-breakers, not edges.

---

## 7. Stability: Years, Regimes, Weekdays

**By entry year — P(win) at the 2% target / net expectancy per attempt:**

| Year | P(win) @2% | Net exp @2% | Net exp @6% |
|---|---:|---:|---:|
| 2021 | 53.0% | −0.35% | −0.27% |
| 2022 | 50.3% | −0.44% | −0.42% |
| 2023 | 54.1% | −0.21% | −0.07% |
| 2024 | 51.0% | −0.38% | −0.34% |
| 2025 | 47.7% | −0.51% | −0.51% |
| 2026 (Jan–Aug) | 47.0% | −0.58% | −0.64% |

- **The sign never flips in any year, at any target.** 2023 was the least hostile; 2025–26 the most. The recent deterioration (win rate down ~7pp from the 2023 peak) is consistent with a choppier, mean-reverting tape — and it is a warning against calibrating to the friendly 2021–23 numbers.
- **Regime (NIFTY vs 20-EMA):** risk-on lifts P(win) by +3.5–7pp at 1–3% targets (e.g., 52.0% vs 48.1% at 2%) and improves net expectancy by ~0.14pp per attempt — but never flips the sign. The BRD's regime gate is directionally right and insufficient alone. (At 6%, regime stops mattering entirely: 18.4% both ways — wide targets don't care about the index trend within a week.)
- **Day-of-week:** Tue (52.5%) > Mon (52.0%) > Wed (50.4%) > Thu (49.0%) > Fri (48.2%) at the 2% target. A ~4.4pp spread — real enough to prefer early-week entries, far too small to trade alone.

---

## 8. The Repeaters: Who Hits Big Targets Repeatedly

Durable repeater = first-passage win at the sweet-spot target in ≥4 of 6 years, with ≥20 wins in each qualifying year. **200 of 423 stocks qualify** at the 8% target — high-target repeaters are common as *events*, even though none of them is profitable *unconditionally*.

Top 20 by consistency (`repeats.csv` for the full list):

| Stock | Years hit | P(win) @8% | Hits @2% | Median ATR% | ADTV |
|---|---:|---:|---:|---:|---:|
| IFCI | 6/6 | 24.2% | 657 | 4.8% | ₹62 cr |
| TARIL | 6/6 | 23.3% | 597 | 5.4% | ₹21 cr |
| JWL | 6/6 | 22.7% | 619 | 4.8% | ₹55 cr |
| HBLENGINE | 6/6 | 22.4% | 670 | 4.4% | ₹85 cr |
| ANANTRAJ | 6/6 | 21.6% | 659 | 4.3% | ₹64 cr |
| GRAVITA | 6/6 | 21.3% | 639 | 4.4% | ₹22 cr |
| JPPOWER | 6/6 | 21.3% | 598 | 4.8% | ₹94 cr |
| BSE | 6/6 | 20.7% | 660 | 3.8% | ₹311 cr |
| ZENTEC | 6/6 | 20.6% | 637 | 4.2% | ₹59 cr |
| ANGELONE | 6/6 | 20.5% | 628 | 4.1% | ₹195 cr |
| SUZLON | 6/6 | 20.4% | 572 | 4.4% | ₹324 cr |
| … | | | | | |

Three honest observations about this list:

1. **It is a volatility list, not an edge list.** Repeater median ATR is 3.76% vs 3.18% universe-wide (+18%). These stocks clear wide targets *because they move*, and they also stop out more. Their P(win)@8% of ~20–24% sits below the 31.3% breakeven, exactly as Finding §4 predicts.
2. **High-ADTV names appear (BSE, SUZLON, ANGELONE, IDEA)** — so the list is tradeable at size for a retail account, unlike illiquid smallcaps.
3. **The correct use of this table is as the *candidate pool* for conditional studies** — stocks with enough weekly range for a 4–8% tranche-2 runner to exist — never as a buy list.

---

## 9. Implications for the BRD System (Phase 0 → Phase 5 bridge)

1. **The BRD's +2% Tranche-1 target is validated as the right *location*** — it has the cheapest relative filter bar (1.51×) of all tested targets, and its economics are exactly what the 2-tranche model assumes.
2. **The setups catalog (Phase 5) is not optional — it is the entire game.** The unconditional baseline at +2% is 50.4% against a 75.9% breakeven. The 5 setups must collectively add ~25pp of first-passage win rate. This study provides the baseline every setup must be measured against.
3. **The 48-hour stall rule is empirically load-bearing.** With 57% of stock-days touching −2.2% within a week, time-decay exits on dead trades are the difference between −2.55% net losses and scratches. (Follow-up study: rerun first-passage with a T+2 stall exit to quantify the improvement.)
4. **The regime gate (+3.5–4pp) and weekday tilt (+4.4pp) are tie-breakers**, worth stacking but worth ~8pp combined — a third of the required lift.
5. **Stock selection narrows the pool, not the odds.** Use the repeater table to pick *where* range exists (tranche-2 runners need 4%+ weekly range), and conditions to pick *when*.
6. **Do not chase the flattest part of the curve (6–8%) with full positions.** Its relative filter bar (2.1–2.8×) is the steepest; it only makes sense as the *runner half* of a 2-tranche position whose base half already banked +2%.

---

## 10. Limitations (read before trading on any number above)

- **Close-of-day entry model.** The BRD enters at 10:00 AM T+1. Intraday touches between open and 10 AM are invisible here; results are the clean close-anchored baseline, not a simulation of the actual execution plan.
- **Day-resolution first passage.** When both levels are touched on the same day, the stop is assumed to win (conservative). Intraday sequencing would soften this slightly — and daily-high-touch targets slightly overstate reachable wins. The two biases partially offset; net direction is conservative.
- **Survivorship bias.** Today's NIFTY 500 list measured backward inflates absolute levels modestly (dead/delisted names absent). Year-by-year tables bound the effect; the *sign* of the expectancy result is far too large to be an artifact. The clean fix is the BRD Phase 3 point-in-time universe from NSE bhavcopy.
- **yfinance adjustments.** Auto-adjust handles splits/bonuses; exotic corporate actions can still distort single names. 78 low-coverage symbols were excluded.
- **No conditioning.** Every number here is the *unconditional* baseline. The study's purpose is to size the bar (Section 6), not to claim entries are this bad in practice.
- **2026 is a partial year** (Jan–Aug) and its weakness may partly reflect an unfavorable tape rather than a regime change.

---

## 11. Where a Real Edge Could Live — Next Studies

Ranked by expected information per hour of work:

1. **Conditional first-passage (the decisive one).** Recompute P(win | condition) for: prior-day red close, delivery/volume spike, proximity to 52-week high, top-decile relative strength, Parkinson squeeze. Each condition needs to add its share of the ~25pp. This directly tests the BRD's 5 setups before any code is written for them.
2. **Stop-width sweep.** Rerun at stops of 1.5/3/4%. The breakeven curve shifts with the stop; the optimum (target, stop) pair is a surface, not a point. Validates or amends the 2.2% gate.
3. **ATR-normalized targets.** ~~Express targets as multiples of each stock's 14-day ATR.~~ **Done — see Addendum B.** Verdict: volatility units do *not* stabilize the curve; the sweet spot is a property of move size and the fixed stop, not of per-stock volatility.
4. **Stall-exit simulation.** Add the T+2 3:15 PM scratch exit to the outcome model and measure how much of the −0.41%/attempt it recovers.
5. **Point-in-time universe** (bhavcopy-based) to remove survivorship bias and enable 10+ year history.

---

## 12. Reproducibility

```bash
python scripts/download_data.py      # universe + 5y bars (~30s)
python scripts/analyze.py            # event study → data/results/*.csv
python scripts/analyze_atr.py        # ATR-normalized follow-up → atr_*.csv, screen_half_atr.csv
python scripts/build_report.py       # → reports/sweet_spot_study.html
```

All levers (horizon, stop, targets, friction, tax, durability thresholds) live in `scripts/study_config.yaml`. Raw events: `data/results/events.parquet` (519,785 rows).

---

## Addendum B — ATR-Normalized Targets (Detailed Follow-Up Report)

*Script: `scripts/analyze_atr.py` · Tables: `data/results/atr_targets.csv`, `atr_by_year.csv`, `atr_by_regime.csv`, `atr_stock_rates.csv`, `screen_half_atr.csv` · Raw events: `events_atr.parquet`*

### B.0 Motivation and Design

The main study expressed every target in fixed percent and found the sweet-spot curve net-negative everywhere. A natural objection follows: **fixed percent is the wrong unit.** A +2% target is a violent move for HINDUNILVR (median ATR 1.9%) and a quiet hour for SUZLON (4.4%). If the "sweet spot" is really a property of *volatility-normalized move size*, then expressing targets as multiples of each stock's own ATR should:

1. shift the curve itself (different stocks contribute different realized target sizes), and
2. — the real hypothesis — **stabilize** it: tighter cross-stock dispersion, smaller year-to-year drift, a consistent optimum.

**Method.** Every target is restated as K × ATR%, where ATR% = (14-day mean true range)/close, computed strictly from data before the entry day. K ∈ {0.25, 0.50, 0.75, 1.00, 1.50, 2.00, 3.00}. Everything else is identical to the main study: same 5-session window, same day-resolution first-passage with same-day tie → stop, same timeout marking at T+5 close, same 0.35% friction + 20% STCG, same entry-at-close baseline. One deliberate exception: **the stop stays fixed at −2.2%** for all stocks — this is not an oversight but the operationally correct framing, because the BRD's structural stop is defined in percent (a portfolio-level risk gate), not per-stock volatility. Events with missing or degenerate ATR (first 13 sessions, dead tapes) are excluded: 512,131 usable events vs 519,785 in the base study.

### B.1 Result 1: The Two Curves Are the Same Curve

| ATR target | Median realized target | P(win) @ −2.2% stop | Breakeven P (mean) | Net exp/entry | Fixed-% twin (nearest size) | Fixed-% P(win) |
|---|---:|---:|---:|---:|---:|---:|
| K=0.25 | 0.79% | 68.7% | ~100% | −0.43% | 1.0% (82.7% touch) | — |
| **K=0.50** | **1.58%** | **57.3%** | 99.4% | −0.40% | **1.5%** | **57.0%** |
| K=0.75 | 2.37% | 47.5% | 94.7% | −0.40% | 2.5% | 44.5% |
| K=1.00 | 3.16% | 39.3% | 85.1% | −0.39% | **3.0%** | **39.3%** |
| K=1.50 | 4.74% | 26.2% | 64.6% | −0.37% | 5.0% | 23.6% |
| K=2.00 | 6.32% | 17.1% | 50.1% | −0.35% | **6.0%** | **18.4%** |
| K=3.00 | 9.48% | 7.3% | 34.1% | −0.33% | 8.0% | 11.3% |

Read the two bolded twin rows: at K=1.00 the ATR curve and the fixed-3% curve produce an **identical** 39.3% win rate; at K=0.50 and K=2.00 they agree within ~1pp. Mapping targets through each stock's own volatility lands the points exactly on the fixed-percent curve.

**Interpretation.** P(win) is a function of *move size*, not of the unit system. There is no separate "volatility regime" in which high-ATR stocks behave differently at scaled targets. The universe behaves like one homogeneous statistical object: give it a finish line at distance d (in %) and a wall at −2.2%, and it wins the race at a rate determined by d alone. This is itself a valuable negative result — it says the NIFTY-500 cross-section is *already* effectively volatility-normalized by the liquidity ranking (today's most-traded stocks cluster tightly in ATR: universe median 3.18%, repeaters 3.76%, half-ATR screen ≥ 4.0%).

The regime slice confirms the pattern carries through conditioning: risk-on lifts P(win) at every K (K=0.5: 59.9% vs 53.6%; K=1.0: 41.2% vs 36.7%; K=2.0: 18.2% vs 15.5%) — the same +3–6pp tilt the fixed-% study found, never enough to flip the expectancy sign (−0.30% best, risk-on, K=3).

### B.2 Result 2: The Stability Hypothesis Fails on All Three Measurements

This was the decisive test, and the answer is unambiguous.

**Test 1 — Cross-stock dispersion (should shrink in ATR units; it widens).**

| Target | Pooled P(win) | Per-stock IQR | Std dev |
|---|---:|---:|---:|
| fixed 1.5% | 57.1% | 3.4pp | 2.7pp |
| fixed 2% | 50.4% | 4.1pp | 3.3pp |
| fixed 2.5% | 44.5% | 4.9pp | 4.1pp |
| **0.50 ATR** | 57.3% | **7.4pp** | **5.2pp** |
| 0.75 ATR | 47.5% | 6.3pp | 4.4pp |
| 1.00 ATR | 39.3% | 4.8pp | 3.7pp |

At comparable move sizes, per-stock dispersion in ATR units is roughly **double** the fixed-% dispersion (7.4pp vs 3.4pp IQR). Two effects inject noise instead of removing it: (a) ATR is *estimated* from 14 trailing sessions and its errors propagate directly into the target size; (b) volatility is regime-dependent, so a stock's ATR in a crash month sets targets very differently than in a calm month — per-stock, per-year, not cross-sectionally.

**Test 2 — Year-to-year drift (should shrink; it widens).** P(win) range across the 6 calendar years at comparable targets: fixed 2% = **7.0pp** (47.0%–54.1%); 0.50 ATR = **9.1pp** (54.6%–63.7%). The ATR version is *more* regime-sensitive, not less.

**Test 3 — Consistency of the optimum (should be stable; it wanders).** Best net-expectancy K by year: 2021 → K=2.0, 2022 → K=3.0, 2023 → K=3.0, 2024 → K=3.0, 2025 → K=3.0, **2026 → K=0.5**. Six years, three different optima, including a sharp reversal in the most recent year. The fixed-% study's argmax (4–8%, monotone) is at least as stable.

**Why this happens — the wall and the finish line.** The race geometry is governed by the *stop*, and the stop is fixed at −2.2% for every stock. Rescaling the target into each stock's volatility units moves the finish line; it does not move the wall. The probability of reaching any finish line before hitting a fixed wall is essentially a function of the distance between them — hence Result 1 (the curves coincide) and Result 2 (no added stability, only added estimation noise). A genuinely fair volatility test would normalize **both** legs: e.g., a 1×ATR target against a 1×ATR stop, with position size adjusted so rupee risk stays constant. That test remains open (Section 11) but is of academic interest only for the BRD, whose stop is deliberately fixed in percent — and whose economics (friction per rupee of notional) are percent-denominated by construction.

**Verdict on the hypothesis: rejected.** The sweet spot is not denominated in volatility units. It is a property of move size versus a fixed stop, full stop. Practically: **the BRD's percent-denominated targets need no recalibration**, and "normalize targets by ATR" should be crossed off as a promised source of improvement.

### B.3 Result 3: The Half-ATR Screen — Where +2% Is a Small Move

The original question survives in operational form: *for which stocks is a 2% target only half of a normal week's movement?* Answer: median ATR% ≥ 4.0%. **61 of 423 stocks qualify; 35 of them with ADTV ≥ ₹50 cr** (`screen_half_atr.csv`, sorted by liquidity).

Most liquid qualifiers:

| Stock | Median ATR% | P(win) @0.5 ATR | P(win) @2% | ADTV |
|---|---:|---:|---:|---:|
| IDEA | 4.5% | 46.4% | 49.5% | ₹362 cr |
| MAZDOCK | 4.0% | **55.6%** | 54.5% | ₹355 cr |
| SUZLON | 4.4% | 47.4% | 46.4% | ₹324 cr |
| POLICYBZR | 4.2% | 47.3% | 47.3% | ₹234 cr |
| COCHINSHIP | 4.0% | **55.3%** | 52.0% | ₹204 cr |
| ANGELONE | 4.1% | 50.4% | 50.9% | ₹195 cr |
| HFCL | 4.3% | **53.9%** | 53.4% | ₹127 cr |
| RPOWER | 5.0% | 45.3% | 48.1% | ₹125 cr |
| CHENNPETRO | 4.3% | **53.9%** | **55.4%** | ₹104 cr |
| TITAGARH | 4.4% | 52.7% | 53.2% | ₹103 cr |

Best hit rates within the screen: SYRMA (56.2% @0.5 ATR), MAZDOCK (55.6%), COCHINSHIP (55.3%), NAVA (55.1%), MRPL (54.7%).

**The counterintuitive property that matters most.** The screen's stocks do **not** win more often — at 0.5 ATR their median win rate is **51.2%, below the universe's pooled 57.3%**. This is not a paradox but arithmetic: for a 4.2%-ATR stock, 0.5 ATR is a +2.1% move, while for a 2.8%-ATR stock it is only +1.4% — the screen's finish lines are absolutely farther away. What the screen buys is **bigger wins per win**, not more wins: the same 51% of occasions pay ~2.1% instead of ~1.4%. Conversely, at a *fixed* 2% target the screen is mildly favored (median 51.9% vs 50.8% universe median) precisely because 2% is a smaller fraction of their ATR (≈0.48× vs ≈0.63×).

**How to use this list honestly:**

1. **As runner habitat, not a buy list.** The BRD's Tranche-2 runner targets (+4% to +8%) sit at ≈1.0–2.0 ATR for this cohort — normal weekly behavior — while for the median universe stock they are 1.3–2.5 ATR, tail events. If the runner half of the position is meant to catch institutional impulses, this is the pond where impulses are ordinary.
2. **It does not create edge.** Best-in-screen P(win) @0.5 ATR (~56%, SYRMA) stands against a >99% breakeven at that size. Finding §4 — *no stock beats breakeven unconditionally, at any target, in any units* — is untouched by normalization.
3. **It is the natural intersection set for the conditional study.** High-ATR stocks **when a setup fires** is the BRD's runner hunting ground; the conditional first-passage study (Section 11, item 1) should run its conditions on this cohort first, where each conditional win pays the most.

### B.4 Limitations Specific to This Follow-Up

- **ATR is estimated, not known.** The 14-day window introduces estimation error that propagates into target size — this is precisely why the dispersion test (B.2, Test 1) penalizes ATR units. A longer ATR (e.g., 50-day) would trade responsiveness for stability; the conclusion is unlikely to flip.
- **The fixed stop is a framing choice.** It is the correct one for the BRD (percent-denominated risk gate, percent-denominated friction), but it caps what "fair" volatility normalization can show. The both-normalized variant (target and stop in ATR, rupee risk held constant) is the open follow-up.
- **~7,600 events excluded** for missing/degenerate ATR (first 13 sessions per stock, dead tapes) — concentrated in recent IPOs; no material effect on pooled rates.
- All limitations of the main study carry over: close-of-day entries, day-resolution ties scored as stops, survivorship bias from today's constituent list, yfinance adjustment quirks.

### B.5 Bottom Line of the ATR Study

1. **P(win) depends on move size, not units.** The ATR curve and the fixed-% curve are the same curve (B.1) — the cross-section is already volatility-homogeneous through its liquidity ranking.
2. **The stability hypothesis is rejected on all three tests** — dispersion, drift, and optimum consistency all worsen in ATR units (B.2). The stop, fixed in percent, is the wall that governs the race.
3. **The percent-denominated BRD needs no recalibration.** +2% Tranche-1 / +4–8% Tranche-2 remain correctly placed; ATR normalization is now a *screening* tool (half-ATR habitat list), not a *targeting* tool.
4. **The half-ATR screen (61 stocks, 35 liquid) is the runner pond** — bigger wins per win, slightly better fixed-2% hit rates, and the natural first cohort for the conditional-entry studies that must now find the ~25pp of lift.

---

*Findings report for the NSE High-Conviction Cash Swing System — Phase 0 feasibility study. Generated 2026-09-08; Addendum B (ATR-normalized targets) added same day.*
