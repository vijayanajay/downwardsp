"""`nse-cash sync` implementation (Phase 2.7).

- No args: sync the latest trading day (run after 6:45 PM IST).
- --from/--to: historical multi-year backfill with multi-threaded downloads
  and a Rich progress bar.
- Idempotency: dates already fully present in DuckDB are skipped unless
  --force; dates with bars but missing delivery data get an MTO backfill.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date as Date
from datetime import datetime, timedelta
from pathlib import Path

import click
import pandas as pd
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from nse_cash.data.bhavcopy import fetch_bhavcopy, join_delivery
from nse_cash.data.corporate_actions import fetch_corporate_actions, load_seed_corporate_actions
from nse_cash.data.delivery import fetch_delivery
from nse_cash.data.fetcher import NSEHttpClient
from nse_cash.data.indices import fetch_indices_for_date, ingest_from_local_cache
from nse_cash.data.storage import MarketStore
from nse_cash.core.logger import console, log_execution_time

log = logging.getLogger("nse_cash.sync")

MAX_HOLIDAY_LOOKBACK = 12   # business days to walk back for the latest trading day
DOWNLOAD_WORKERS = 6


def _bars_count(store: MarketStore, d: Date) -> int:
    row = store.con.execute(
        "SELECT count(*) FROM daily_bars WHERE date = ?", [d]).fetchone()
    return int(row[0])


def _delivery_present(store: MarketStore, d: Date) -> bool:
    row = store.con.execute(
        "SELECT count(*) FROM daily_bars WHERE date = ? AND deliverable_qty IS NOT NULL",
        [d]).fetchone()
    return int(row[0]) > 0


def _fetch_day(client: NSEHttpClient, d: Date, raw_dir: Path) -> dict:
    """Network + parse work for one day (thread-safe: no DB access here)."""
    bhav = fetch_bhavcopy(client, d, raw_dir)
    if bhav is None:
        return {"date": d, "bhav": None, "mto": None, "idx": None}
    mto = fetch_delivery(client, d, raw_dir)
    idx = fetch_indices_for_date(client, d, raw_dir)
    return {"date": d, "bhav": bhav, "mto": mto, "idx": idx}


def _persist_day(store: MarketStore, result: dict) -> str:
    """Write one day's parsed data into DuckDB. Returns a status label."""
    d: Date = result["date"]
    bhav = result["bhav"]
    if bhav is None:
        return "holiday"
    df = bhav.df
    if result["mto"] is not None:
        df = join_delivery(df, result["mto"])
    store.upsert_daily_bars(df)
    if result["idx"] is not None:
        store.upsert_market_indices(result["idx"])
    return "ingested"


def _sync_corporate_actions(client: NSEHttpClient, store: MarketStore,
                            from_date=None, to_date=None) -> int:
    """Load offline seed actions and fetch live actions (capping historical range to avoid API spam)."""
    total = 0
    seed_df = load_seed_corporate_actions()
    if seed_df is not None and not seed_df.empty:
        total += store.upsert_corporate_actions(seed_df)

    if from_date is None and to_date is None:
        df = fetch_corporate_actions(client)
        if df is not None and not df.empty:
            total += store.upsert_corporate_actions(df)
        return total

    # Historical backfill: live NSE JSON API only retains the last ~365 days of events.
    # We query the live API only within the valid window to prevent hundreds of 403/404s.
    start = from_date.date() if hasattr(from_date, "date") else from_date
    end = to_date.date() if hasattr(to_date, "date") else to_date
    today = datetime.now().date()
    api_cutoff = today - timedelta(days=365)
    api_start = max(start, api_cutoff)
    if api_start > end:
        return total

    month = api_start.replace(day=1)
    while month <= end:
        if month.month == 12:
            nxt = month.replace(year=month.year + 1, month=1)
        else:
            nxt = month.replace(month=month.month + 1)
        m_start, m_end = max(api_start, month), min(end, nxt - timedelta(days=1))
        df = fetch_corporate_actions(client, m_start, m_end)
        if df is not None and not df.empty:
            total += store.upsert_corporate_actions(df)
        month = nxt
    return total


def _latest_trading_day(client: NSEHttpClient, raw_dir: Path) -> Date | None:
    """Walk back from today until a bhavcopy is found."""
    day = datetime.now().date()
    for _ in range(MAX_HOLIDAY_LOOKBACK):
        result = _fetch_day(client, day, raw_dir)
        if result["bhav"] is not None:
            return day
        day -= timedelta(days=1)
    return None


def run_sync(ctx: click.Context, from_date: datetime | None,
             to_date: datetime | None, force: bool) -> None:
    config = ctx.obj["config"]
    raw_dir = Path(config.paths.raw_dir)
    client = NSEHttpClient(cache_dir=raw_dir)
    store = MarketStore(config.paths.duckdb_path)
    try:
        with log_execution_time("sync"):
            if from_date is not None or to_date is not None:
                _run_backfill(client, store, raw_dir, from_date, to_date, force)
            else:
                _run_latest(client, store, raw_dir, force)
            # Corporate actions (upcoming snapshot, or monthly pages for backfills)
            n = _sync_corporate_actions(client, store, from_date, to_date)
            if n:
                console.print(f"[green]corporate actions upserted: {n}[/]")
            from nse_cash.core.adjuster import refresh_adjustments
            adjusted = refresh_adjustments(store)
            if adjusted:
                console.print(f"[green]corporate-action adjustments applied "
                              f"to {adjusted} symbol(s)[/]")
            # PIT universe + governance flags
            latest = store.latest_date()
            if latest is not None:
                from nse_cash.core.governance import apply_governance_filters
                from nse_cash.core.universe import build_pit_universe, build_pit_universe_range
                if from_date is not None or to_date is not None:
                    start_d = (from_date or to_date).date()
                    end_d = (to_date or datetime.now()).date()
                    if start_d > end_d:
                        start_d, end_d = end_d, start_d
                    n_pit = build_pit_universe_range(store, start_d, end_d)
                    console.print(f"[green]PIT universe backfilled for {n_pit} dates[/]")
                else:
                    membership = build_pit_universe(store, latest)
                    console.print(f"[green]PIT universe {latest}: {len(membership)} symbols[/]")
                excluded = apply_governance_filters(store, latest, client)
                console.print(f"[green]governance exclusions for {latest}: {len(excluded)}[/]")
                # Phase 5: keep the shared features table warm for scan/backtest
                from nse_cash.setups.features import refresh_features
                n_feat = refresh_features(store, latest)
                console.print(f"[green]features refreshed for {latest}: {n_feat} rows[/]")
            # Parquet export (selective current year on daily sync, all years on backfill)
            if from_date is not None or to_date is not None:
                written = store.parquet_export(Path(config.paths.parquet_dir))
            else:
                exp_year = latest.year if latest is not None else datetime.now().year
                written = store.parquet_export(Path(config.paths.parquet_dir), year=exp_year)
            console.print(f"[green]parquet export: {len(written)} year file(s) -> "
                          f"{config.paths.parquet_dir}[/]")
            _seed_indices_from_cache(store, config)
    finally:
        client.close()
        store.close()


def _run_latest(client: NSEHttpClient, store: MarketStore, raw_dir: Path,
                force: bool) -> None:
    latest_db = store.latest_date()
    day = _latest_trading_day(client, raw_dir)
    if day is None:
        console.print("[red]No bhavcopy available in the last "
                      f"{MAX_HOLIDAY_LOOKBACK} business days.[/]")
        raise SystemExit(1)

    if latest_db is not None and latest_db >= day and not force:
        console.print(f"[green]{latest_db}: already up to date with latest NSE trading day "
                      f"(use --force to re-download, or --from/--to to backfill)[/]")
        return

    # If DB is behind by multiple days, catch up the missing range
    if latest_db is not None and latest_db < day and not force:
        delta_start = latest_db + timedelta(days=1)
        console.print(f"Catching up missing daily bars from {delta_start} to {day} ...")
        _run_backfill(client, store, raw_dir,
                      datetime.combine(delta_start, datetime.min.time()),
                      datetime.combine(day, datetime.min.time()),
                      force=False)
        return

    with Progress(SpinnerColumn(), TextColumn("{task.description}"),
                  console=console) as progress:
        task = progress.add_task(f"Syncing {day} ...", total=None)
        result = _fetch_day(client, day, raw_dir)
        progress.update(task, completed=1)
    status = _persist_day(store, result)
    console.print(f"[green]{day}: {status} "
                  f"({_bars_count(store, day)} rows in daily_bars)[/]")


def _run_backfill(client: NSEHttpClient, store: MarketStore, raw_dir: Path,
                  from_date: datetime | None, to_date: datetime | None,
                  force: bool) -> None:
    start = (from_date or to_date or datetime.now()).date()
    end = (to_date or datetime.now()).date()
    if start > end:
        start, end = end, start

    # Build the business-day list honoring idempotency (skip fully-present dates)
    all_days: list[Date] = []
    pending: list[Date] = []
    day = start
    while day <= end:
        if day.weekday() < 5:  # Mon-Fri; holidays 404 and are skipped naturally
            all_days.append(day)
            if force or _bars_count(store, day) == 0:
                pending.append(day)
        day += timedelta(days=1)
    skipped = len(all_days) - len(pending)
    console.print(f"Backfill {start} -> {end}: {len(all_days)} business days, "
                  f"{len(pending)} to download, {skipped} already present (skipped).")

    if pending:
        statuses: dict[Date, str] = {}
        with Progress(SpinnerColumn(), BarColumn(),
                      TextColumn("{task.percentage:>3.0f}% {task.description}"),
                      TimeElapsedColumn(), console=console) as progress:
            task = progress.add_task("Downloading", total=len(pending))
            with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:
                futures = {pool.submit(_fetch_day, client, d, raw_dir): d
                           for d in pending}
                for fut in as_completed(futures):
                    d = futures[fut]
                    try:
                        result = fut.result()
                    except Exception as exc:  # noqa: BLE001
                        log.warning("download failed for %s: %s", d, exc)
                        statuses[d] = "failed"
                    else:
                        statuses[d] = _persist_day(store, result)
                    progress.advance(task)
        ingested = sum(1 for s in statuses.values() if s == "ingested")
        holidays = sum(1 for s in statuses.values() if s == "holiday")
        failed = sum(1 for s in statuses.values() if s == "failed")
        console.print(f"[green]backfill done: {ingested} ingested, "
                      f"{holidays} holidays, {failed} failed[/]")

    # Self-healing: backfill MTO for dates with bars but no delivery data
    if not force:
        mto_pending = [d for d in all_days
                       if _bars_count(store, d) > 0 and not _delivery_present(store, d)]
        if mto_pending:
            console.print(f"MTO backfill for {len(mto_pending)} day(s) with missing "
                          "delivery data ...")
            mto_frames: list[pd.DataFrame] = []
            with Progress(SpinnerColumn(), BarColumn(),
                          TextColumn("{task.percentage:>3.0f}%"),
                          console=console) as progress:
                task = progress.add_task("MTO", total=len(mto_pending))
                with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:
                    futures = {pool.submit(fetch_delivery, client, d, raw_dir): d
                               for d in mto_pending}
                    for fut in as_completed(futures):
                        d = futures[fut]
                        try:
                            mto = fut.result()
                        except Exception:  # noqa: BLE001
                            mto = None
                        if mto is not None:
                            mto_frames.append(mto[["symbol", "date", "deliverable_qty", "delivery_pct"]])
                        progress.advance(task)
            if mto_frames:
                combined_mto = pd.concat(mto_frames, ignore_index=True)
                store.con.register("_mto_join", combined_mto)
                store.con.execute("""
                    UPDATE daily_bars b SET deliverable_qty = m.deliverable_qty,
                           delivery_pct = m.delivery_pct
                    FROM _mto_join m
                    WHERE b.symbol = m.symbol AND b.date = m.date""")
                store.con.unregister("_mto_join")


def _seed_indices_from_cache(store: MarketStore, config) -> None:
    """If a local yfinance cache exists (scripts/download_data.py), seed indices."""
    n = ingest_from_local_cache(store, Path("data/ohlcv"))
    if n:
        console.print(f"[green]market_indices seeded from local cache: {n} rows[/]")
