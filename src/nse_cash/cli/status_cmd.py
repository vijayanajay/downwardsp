"""`nse-cash status` implementation (Phase 3.4).

Displays: total dates ingested + range, missing-date anomalies, total symbols,
PIT Top-500 universe count for the latest trading date, and active corporate
action adjustments.
"""

from __future__ import annotations

import logging
from pathlib import Path

import click
import pandas as pd
from rich.table import Table

from nse_cash.core.constants import UNIVERSE_SIZE
from nse_cash.core.logger import console

log = logging.getLogger("nse_cash.status")


def run_status(ctx: click.Context) -> None:
    config = ctx.obj["config"]
    db_path = Path(config.paths.duckdb_path)
    if not db_path.exists():
        console.print(f"[red]No market database at {db_path}. Run `nse-cash sync` first.[/]")
        raise SystemExit(1)

    from nse_cash.data.storage import MarketStore
    store = MarketStore(config.paths.duckdb_path, read_only=False)
    try:
        _render_status(store, config)
    finally:
        store.close()


def _render_status(store, config) -> None:
    counts = store.date_row_counts()
    if counts.empty:
        console.print("[red]daily_bars is empty — run `nse-cash sync`.[/]")
        raise SystemExit(1)

    dates = pd.to_datetime(counts["date"])
    first, last = dates.min(), dates.max()
    span = (last - first).days
    # Expected weekdays in span (holidays legitimately missing are OK; large
    # gaps are the anomaly worth surfacing)
    expected_weekdays = pd.bdate_range(first, last)

    table = Table(title="NSE Cash — Data Health", title_style="bold")
    table.add_column("Metric")
    table.add_column("Value")
    table.add_column("Note")

    table.add_row("Database", str(store.db_path))
    table.add_row("Dates ingested", f"{len(counts)}")
    table.add_row("Date range", f"{first.date()} -> {last.date()}")
    table.add_row("Total symbols", f"{store.symbol_count():,}")
    missing = sorted(set(expected_weekdays.date) - set(dates.dt.date))
    note = "OK" if len(missing) < 10 else f"[yellow]{len(missing)} missing weekdays[/]"
    table.add_row("Missing weekdays", f"{len(missing)}", note)
    table.add_row("Median rows/day", f"{int(counts['rows'].median()):,}")

    # Corporate actions
    ca = store.con.execute("""
        SELECT action_type, count(*) AS n FROM corporate_actions
        GROUP BY action_type ORDER BY n DESC
    """).df()
    total_ca = int(ca["n"].sum()) if not ca.empty else 0
    applied = store.corporate_actions_applied_count()
    table.add_row("Corporate actions", f"{total_ca}", f"{applied} adjustments applied")

    # Governance snapshot
    gov = store.con.execute("""
        SELECT list_type, count(DISTINCT symbol) AS n FROM governance
        GROUP BY list_type
    """).df()
    gov_str = ", ".join(f"{r.list_type}: {int(r.n)}" for r in gov.itertuples()) or "none"
    table.add_row("Governance flags", gov_str)

    console.print(table)

    # PIT universe for latest date
    latest = store.latest_date()
    if latest is not None:
        pit = store.con.execute(
            "SELECT count(*) FROM pit_universe WHERE date = ?",
            [pd.Timestamp(latest).date()]).fetchone()[0]
        color = "green" if pit >= UNIVERSE_SIZE else "yellow"
        console.print(f"PIT universe {pd.Timestamp(latest).date()}: "
                      f"[{color}]{pit} / {UNIVERSE_SIZE}[/] symbols "
                      "(run `nse-cash scan` to rebuild if stale)")

    # Low-row anomaly listing
    low = counts[counts["rows"] < counts["rows"].median() * 0.5]
    if not low.empty:
        console.print(f"[yellow]Anomalous low-row dates "
                      f"(< 50% of median): {len(low)} — "
                      f"{', '.join(str(pd.Timestamp(d).date()) for d in low['date'].head(5))}...[/]")
