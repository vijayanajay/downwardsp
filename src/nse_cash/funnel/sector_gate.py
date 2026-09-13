"""Sector Classification & Diversification Gate (Phase 4.2).

Enforces portfolio sector diversification constraints:
  1. Ingests and maintains standard NSE Sector / Industry classification mapping (data/nse_sectors.json).
  2. Enforces the strict BRD constraint: Maximum 1 open position per Sector across the 4 concurrent portfolio slots.
  3. When evaluating candidates, rejects any candidate whose sector is already occupied by an active trade.
  4. Disqualifies intra-batch collisions so no two new trades share the same sector.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

log = logging.getLogger("nse_cash.sector_gate")

UNKNOWN_SECTOR = "Unknown"  # sentinel for symbols absent from the sector map

DEFAULT_SECTORS_FILE = Path("data/nse_sectors.json")
CONFIG_SECTORS_FILE = Path("config/nse_sectors.json")

# Fallback core mapping for top NSE tickers in case external JSON is missing
BUILTIN_SECTOR_FALLBACK: Dict[str, str] = {
    "HDFCBANK": "Financial Services",
    "ICICIBANK": "Financial Services",
    "SBIN": "Financial Services",
    "AXISBANK": "Financial Services",
    "KOTAKBANK": "Financial Services",
    "BAJFINANCE": "Financial Services",
    "BAJAJFINSV": "Financial Services",
    "INFY": "IT",
    "TCS": "IT",
    "HCLTECH": "IT",
    "WIPRO": "IT",
    "TECHM": "IT",
    "LTIM": "IT",
    "RELIANCE": "Energy",
    "ONGC": "Energy",
    "BPCL": "Energy",
    "IOC": "Energy",
    "TATAMOTORS": "Auto",
    "M&M": "Auto",
    "MARUTI": "Auto",
    "BAJAJ-AUTO": "Auto",
    "EICHERMOT": "Auto",
    "HEROMOTOCO": "Auto",
    "SUNPHARMA": "Healthcare",
    "CIPLA": "Healthcare",
    "DRREDDY": "Healthcare",
    "APOLLOHOSP": "Healthcare",
    "DIVISLAB": "Healthcare",
    "ITC": "FMCG",
    "HINDUNILVR": "FMCG",
    "NESTLEIND": "FMCG",
    "BRITANNIA": "FMCG",
    "TATACONSUM": "FMCG",
    "TATASTEEL": "Metals",
    "JSWSTEEL": "Metals",
    "HINDALCO": "Metals",
    "VEDL": "Metals",
    "COALINDIA": "Metals",
    "NTPC": "Power",
    "POWERGRID": "Power",
    "ADANIPOWER": "Power",
    "ADANIGREEN": "Power",
    "LT": "Construction",
    "ULTRACEMCO": "Construction",
    "GRASIM": "Construction",
    "BHARTIARTL": "Telecommunication",
    "TITAN": "Consumer Durables",
    "BEL": "Capital Goods",
    "SIEMENS": "Capital Goods",
    "ABB": "Capital Goods",
    "HAL": "Capital Goods",
}


class SectorGate:
    """Sector Diversification Gate enforcing max 1 position per sector."""

    def __init__(self, mapping_path: Optional[Path] = None) -> None:
        self.mapping_path = Path(mapping_path or DEFAULT_SECTORS_FILE)
        self._sectors: Dict[str, Dict[str, str]] = {}
        self.load()

    def load(self) -> None:
        """Load sector mapping from JSON file or fall back to builtin map."""
        for p in (self.mapping_path, CONFIG_SECTORS_FILE):
            if p.exists():
                try:
                    content = p.read_text(encoding="utf-8")
                    raw = json.loads(content)
                    self._sectors = {k.strip().upper(): v for k, v in raw.items()}
                    log.debug("Loaded %d sector records from %s", len(self._sectors), p)
                    return
                except Exception as exc:  # noqa: BLE001
                    log.warning("Failed loading sector map from %s: %s; trying next", p, exc)

        # Fallback to builtin
        self._sectors = {
            sym: {"symbol": sym, "sector": sec, "industry": sec, "company_name": sym}
            for sym, sec in BUILTIN_SECTOR_FALLBACK.items()
        }

    def save(self) -> None:
        """Save current in-memory sector mapping to JSON."""
        self.mapping_path.parent.mkdir(parents=True, exist_ok=True)
        self.mapping_path.write_text(json.dumps(self._sectors, indent=2), encoding="utf-8")

    def get_record(self, symbol: str) -> Optional[Dict[str, str]]:
        """Return the complete metadata dict for a symbol if present."""
        clean_sym = symbol.strip().upper().removesuffix(".NS")
        return self._sectors.get(clean_sym)

    def get_sector(self, symbol: str) -> str:
        """Return the standard sector name for a symbol."""
        clean_sym = symbol.strip().upper().removesuffix(".NS")
        rec = self._sectors.get(clean_sym)
        if rec and isinstance(rec, dict):
            return rec.get("sector") or rec.get("industry") or UNKNOWN_SECTOR
        if rec and isinstance(rec, str):
            return rec
        return BUILTIN_SECTOR_FALLBACK.get(clean_sym, UNKNOWN_SECTOR)

    def is_sector_available(self, symbol: str, active_sectors: Set[str]) -> bool:
        """Check if symbol's sector is unoccupied by active positions."""
        sector = self.get_sector(symbol)
        if sector == UNKNOWN_SECTOR:
            # An unknown sector cannot match an existing known sector
            return True
        return sector not in active_sectors

    def filter_candidates(
        self,
        candidates: List[Any],
        active_sectors: Set[str],
        max_per_sector: int = 1,
    ) -> Tuple[List[Any], List[Tuple[Any, str]]]:
        """Filter candidates enforcing sector limits across active and new trades.

        Args:
            candidates: List of CandidateSignal or objects with .symbol attribute.
            active_sectors: Sectors currently held in active portfolio slots.
            max_per_sector: Maximum allowed positions per sector (default: 1).

        Returns:
            Tuple of (accepted_candidates, list_of_rejected_tuples(candidate, reason)).
        """
        accepted: List[Any] = []
        rejected: List[Tuple[Any, str]] = []

        # Track sectors claimed by active trades + newly accepted candidates
        claimed_sectors: Dict[str, List[str]] = {}
        for s in active_sectors:
            claimed_sectors.setdefault(s, []).append("ACTIVE_TRADE")

        for cand in candidates:
            sym = getattr(cand, "symbol", None)
            if sym is None and isinstance(cand, dict):
                sym = cand.get("symbol")
            if not sym:
                rejected.append((cand, "Missing symbol"))
                continue

            sector = self.get_sector(str(sym))
            existing = claimed_sectors.get(sector, [])

            if sector != UNKNOWN_SECTOR and len(existing) >= max_per_sector:
                first_occupant = existing[0]
                if first_occupant == "ACTIVE_TRADE":
                    reason = f"Sector '{sector}' already occupied by an active portfolio trade"
                else:
                    reason = f"Sector '{sector}' already claimed by candidate '{first_occupant}'"
                rejected.append((cand, reason))
            else:
                accepted.append(cand)
                if sector != UNKNOWN_SECTOR:
                    claimed_sectors.setdefault(sector, []).append(str(sym))

        return accepted, rejected
