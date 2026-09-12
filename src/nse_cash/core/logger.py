"""High-performance structured logging (Phase 1.4).

Rich console formatting + rotating file handler (10 MB x 5 backups) +
`log_execution_time` context manager for subsystem latency measurement.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterator

from rich.console import Console
from rich.logging import RichHandler

console = Console()
_LOG_FILE = Path("logs/nse_cash.log")
_configured = False

LEVEL_STYLES = {
    "DEBUG": "dim",
    "INFO": "cyan",
    "SUCCESS": "bold green",
    "WARNING": "bold yellow",
    "ERROR": "bold red",
    "CRITICAL": "bold red",
}


class SuccessLogger(logging.Logger):
    """Logger with a SUCCESS level alias mapped onto INFO for Rich green styling."""

    def success(self, msg: object, *args: object, **kwargs) -> None:
        self.info("[green]%s[/]", msg if not args else msg % args, **kwargs)


def setup_logging(level: int = logging.INFO, log_file: Path = _LOG_FILE) -> None:
    """Idempotent logging setup: Rich console + rotating file (10MB, 5 backups)."""
    global _configured
    if _configured:
        return
    _configured = True

    logging.setLoggerClass(SuccessLogger)
    root = logging.getLogger("nse_cash")
    root.setLevel(level)
    root.propagate = False

    rich = RichHandler(console=console, rich_tracebacks=True, show_path=False,
                       markup=True, level=level)
    rich.setFormatter(logging.Formatter("%(message)s", datefmt="[%X]"))
    root.addHandler(rich)

    log_file.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024,
                                       backupCount=5, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"))
    root.addHandler(file_handler)


def get_logger(name: str = "nse_cash") -> SuccessLogger:
    setup_logging()
    return logging.getLogger(name)  # type: ignore[return-value]


log = get_logger("nse_cash")


@contextmanager
def log_execution_time(task_name: str) -> Iterator[None]:
    """Measure subsystem latency: `with log_execution_time("sync"):`"""
    start = time.perf_counter()
    log.info("[bold]%s[/] started", task_name)
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        log.info("[bold]%s[/] finished in %.2fs", task_name, elapsed)


# Legacy alias used by early phases of the plan
logger = log
