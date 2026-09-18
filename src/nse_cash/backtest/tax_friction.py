"""Exact friction & universal STCG taxation (Phase 6.3 / R3).

Pure math, no I/O. Every charge is first-class — the difference between a
tear sheet and a fantasy. Per-side charges scale with trade value; GST is
18% of (exchange transaction fee + SEBI fee); the CDSL/NSDL DP charge is a
flat ₹15.93 per sell day (not per tranche — two tranches sold the same day
pay it once); slippage is 0.05% per side (0.10% round trip). STCG is a flat
20% of net realized P&L per financial year (April 1 – March 31).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date

from nse_cash.core.config import FrictionConfig


@dataclass(frozen=True)
class SideFriction:
    """Itemized friction for one order side."""

    gross_value: float       # qty * price
    stt: float
    exchange_fee: float
    sebi_fee: float
    gst: float
    stamp_duty: float        # buy side only
    dp_charge: float         # flat, sell days only
    slippage: float
    total: float

    @property
    def net_value(self) -> float:
        """Cash actually received (sell) or paid (buy) including all friction."""
        return self.gross_value - self.total


def buy_side_friction(value: float, config: FrictionConfig) -> SideFriction:
    """Buy-side charges: STT, stamp duty, exchange, SEBI, GST, slippage."""
    stt = value * config.stt_delivery
    stamp = value * config.stamp_duty
    exch = value * config.nse_turnover
    sebi = value * config.sebi_fee
    gst = (exch + sebi) * config.gst_rate
    slip = value * config.slippage_per_side
    total = stt + stamp + exch + sebi + gst + slip
    return SideFriction(value, stt, exch, sebi, gst, stamp, 0.0, slip, total)


def sell_side_friction(value: float, config: FrictionConfig,
                       dp_charge: float | None = None) -> SideFriction:
    """Sell-side charges: STT, exchange, SEBI, GST, slippage, flat DP charge.

    `dp_charge`: pass 0.0 when another tranche was already sold the same day
    (the flat charge is per sell day, not per order).
    """
    stt = value * config.stt_delivery
    exch = value * config.nse_turnover
    sebi = value * config.sebi_fee
    gst = (exch + sebi) * config.gst_rate
    slip = value * config.slippage_per_side
    dp = config.dp_charge_per_sell if dp_charge is None else float(dp_charge)
    total = stt + exch + sebi + gst + slip + dp
    return SideFriction(value, stt, exch, sebi, gst, 0.0, dp, slip, total)


def net_pnl(entry_value: float, exit_value: float,
            config: FrictionConfig, sell_days: int = 1) -> float:
    """Realized P&L for one trade: exit proceeds - entry cost - all friction.

    `sell_days`: number of distinct days on which tranches were sold; the flat
    DP charge applies once per sell day (two tranches sold the same day pay
    it once; sold on two days, twice).
    """
    buy = buy_side_friction(entry_value, config)
    dp = config.dp_charge_per_sell * max(1, int(sell_days))
    sell = sell_side_friction(exit_value, config, dp_charge=dp)
    return (exit_value - sell.total) - (entry_value + buy.total)


# ---------------------------------------------------------------------------
# Overnight liquid-fund interest on unallocated cash (plan §6.1 / R3 note)
# ---------------------------------------------------------------------------

def liquid_fund_interest(cash_balance: float, days: int,
                         annual_rate: float = 0.065) -> float:
    """Daily accrual at annual_rate/365 on unallocated cash. Trivial, and the
    difference between a tear sheet and a fantasy."""
    if cash_balance <= 0 or days <= 0:
        return 0.0
    return cash_balance * annual_rate / 365.0 * days


# ---------------------------------------------------------------------------
# STCG — flat 20% on net annual profit, FY = April 1 to March 31
# ---------------------------------------------------------------------------

def financial_year(d: Date) -> str:
    """Indian financial year label for a date, e.g. FY2022-23."""
    start_year = d.year if d.month >= 4 else d.year - 1
    return f"FY{start_year}-{(start_year + 1) % 100:02d}"


def stcg_tax(annual_net_pnl: float, config: FrictionConfig) -> float:
    """Flat STCG on net annual profit; no rebate on loss years (kept
    conservative — losses simply carry nothing forward)."""
    if annual_net_pnl <= 0:
        return 0.0
    return annual_net_pnl * config.stcg_tax_rate


class STCGAccount:
    """Flat 20% STCG on net annual profits with loss carry-forward.

    The plan's flat rule (`stcg_tax`) taxes each FY in isolation and drops
    loss years — but Indian law allows business losses to be carried forward
    for 8 assessment years (set-off against the same head), and a system with
    losing years is materially over-taxed without it. Feed realized P&L day
    by day; read `.tax_paid` for cumulative STCG and `.fy_summary` for the
    per-FY breakdown. `carry_forward` is a config knob, not a surprise:
    default ON (the legal behavior), False reproduces the plan's flat rule.

    Losses are tracked as FIFO lots with their ORIGIN financial year, so an
    old loss expires honestly instead of decaying by some invented formula:
    a lot from FY Y can offset profits in FYs Y+1 .. Y+8, then it dies.
    """

    CARRY_FORWARD_FYS = 8

    def __init__(self, config: FrictionConfig, carry_forward: bool = True) -> None:
        self.config = config
        self.carry_forward = carry_forward
        self._loss_lots: list[tuple[int, float]] = []   # (fy_start_year, amount < 0)
        self._current_fy: str | None = None
        self._fy_start_year: int | None = None
        self._fy_pnl = 0.0
        self.tax_paid = 0.0
        self.fy_summary: list[dict] = []

    def add(self, pnl: float, d: Date) -> None:
        """Accumulate one realized trade P&L into its financial year."""
        fy = financial_year(d)
        start_year = d.year if d.month >= 4 else d.year - 1
        if self._current_fy is None:
            self._current_fy = fy
            self._fy_start_year = start_year
            self._fy_pnl = 0.0   # fresh FY after finalize()/construction
        elif fy != self._current_fy:
            self._close_fy()
            self._current_fy = fy
            self._fy_start_year = start_year
            self._fy_pnl = 0.0
        self._fy_pnl += float(pnl)

    def _close_fy(self) -> None:
        """Compute the closed FY's tax; update loss lots."""
        y = self._fy_start_year
        # 1. Expire: a lot from FY Y (e.g. FY2010-11, first computed AY
        #    2011-12) offsets income of the 8 following FYs Y+1 .. Y+8
        #    (AYs +2 .. +9), then dies. At the close of FY Y+8 it is still
        #    usable; from FY Y+9 it is not.
        if self.carry_forward and y is not None:
            self._loss_lots = [lot for lot in self._loss_lots
                               if y - lot[0] <= self.CARRY_FORWARD_FYS]
        brought_in = sum(a for _, a in self._loss_lots) if self.carry_forward else 0.0
        net = self._fy_pnl + brought_in
        tax = max(0.0, net) * self.config.stcg_tax_rate
        self.tax_paid += tax
        # 2. Set-off consumes the OLDEST lots first (FIFO), only up to this
        #    year's profit; leftovers keep their original expiry.
        if self.carry_forward and self._fy_pnl > 0 and self._loss_lots:
            to_offset = min(self._fy_pnl, -brought_in)
            new_lots: list[tuple[int, float]] = []
            for lot_year, amount in self._loss_lots:
                if to_offset <= 0:
                    new_lots.append((lot_year, amount))
                    continue
                take = min(-amount, to_offset)
                remaining = amount + take
                to_offset -= take
                if remaining < -1e-9:
                    new_lots.append((lot_year, remaining))
            self._loss_lots = new_lots
        # 3. This year's own loss becomes a new lot.
        if self.carry_forward and self._fy_pnl < 0 and y is not None:
            self._loss_lots.append((y, self._fy_pnl))
        self.fy_summary.append({
            "fy": self._current_fy,
            "trade_pnl": round(self._fy_pnl, 2),
            "brought_forward": round(brought_in, 2),
            "taxable": round(net, 2),
            "tax": round(tax, 2),
        })

    def finalize(self) -> float:
        """Close the open FY and return cumulative post-tax net P&L.

        Equity in the engine is pre-tax; tax is a year-boundary adjustment,
        reported per FY in the tear sheet (matching how it would actually be
        paid). Post-tax net = realized P&L - tax_paid. Safe to call repeatedly:
        a later add() starts a fresh FY.
        """
        if self._current_fy is not None:
            self._close_fy()
            self._current_fy = None
            self._fy_start_year = None
            self._fy_pnl = 0.0
        return self.total_realized - self.tax_paid

    @property
    def total_realized(self) -> float:
        return sum(r["trade_pnl"] for r in self.fy_summary)
