"""Golden tests for exact friction, DP charge, liquid-fund interest, STCG.

Numbers below are hand-computed from config/config.yaml defaults:
STT 0.10% both sides, stamp 0.015% buy, exchange 0.00345% both, SEBI
0.0001% both, GST 18% on (exchange+SEBI), slippage 0.05% per side,
DP ₹15.93 flat per sell day, STCG 20% flat.
"""

from __future__ import annotations

from datetime import date

from nse_cash.backtest.tax_friction import (buy_side_friction,
                                            financial_year,
                                            liquid_fund_interest, net_pnl,
                                            sell_side_friction, stcg_tax)
from nse_cash.core.config import FrictionConfig

CFG = FrictionConfig()


def approx(a: float, b: float, tol: float = 1e-6) -> None:
    assert abs(a - b) < tol, f"{a} != {b}"


class TestBuySide:
    def test_full_breakdown_on_125000(self):
        f = buy_side_friction(125_000.0, CFG)
        approx(f.stt, 125.0)
        approx(f.stamp_duty, 18.75)
        approx(f.exchange_fee, 4.3125)
        approx(f.sebi_fee, 0.125)
        approx(f.gst, (4.3125 + 0.125) * 0.18)   # 0.79875
        approx(f.slippage, 62.5)
        approx(f.total, 211.48625)
        approx(f.dp_charge, 0.0)
        # Buy cost = gross + total.
        approx(f.net_value, 125_000.0 - 211.48625)


class TestSellSide:
    def test_full_breakdown_on_62500_with_dp(self):
        f = sell_side_friction(62_500.0, CFG)
        approx(f.stt, 62.5)
        approx(f.stamp_duty, 0.0)
        approx(f.exchange_fee, 2.15625)
        approx(f.sebi_fee, 0.0625)
        approx(f.gst, (2.15625 + 0.0625) * 0.18)  # 0.399375
        approx(f.slippage, 31.25)
        approx(f.dp_charge, 15.93)
        approx(f.total, 112.298125)

    def test_same_day_second_sell_pays_no_dp(self):
        f = sell_side_friction(62_500.0, CFG, dp_charge=0.0)
        approx(f.dp_charge, 0.0)
        approx(f.total, 96.368125)


class TestNetPnl:
    def test_winner_two_tranche_day_exits(self):
        # 74 shares @ 100.5 in; T1 37 @ 102.5 (day D), T2 37 @ 106.0 (day D+3).
        entry_value = 74 * 100.5
        buy = buy_side_friction(entry_value, CFG)
        v1, v2 = 37 * 102.5, 37 * 106.0
        s1 = sell_side_friction(v1, CFG)
        s2 = sell_side_friction(v2, CFG)  # different day -> DP charged again
        expected = (v1 - s1.total) + (v2 - s2.total) - (entry_value + buy.total)
        approx(net_pnl(entry_value, v1 + v2, CFG, sell_days=2), expected)

    def test_same_day_both_tranches_pay_dp_once(self):
        entry_value = 74 * 100.5
        v = 74 * 103.0  # both tranches exit same day at one price
        manual = ((v - sell_side_friction(v, CFG).total)
                  - (entry_value + buy_side_friction(entry_value, CFG).total))
        approx(net_pnl(entry_value, v, CFG, sell_days=1), manual)

    def test_two_sell_days_pay_dp_twice(self):
        entry_value = 74 * 100.5
        v1, v2 = 37 * 102.5, 37 * 106.0
        one_day = net_pnl(entry_value, v1 + v2, CFG, sell_days=1)
        two_days = net_pnl(entry_value, v1 + v2, CFG, sell_days=2)
        approx(one_day - two_days, 15.93)

    def test_split_tranches_match_manual_math(self):
        # T1 sold day 1, T2 sold day 3: DP once per day -> same as sell_days=2.
        entry_value = 74 * 100.5
        v1, v2 = 37 * 102.5, 37 * 106.0
        manual = ((v1 - sell_side_friction(v1, CFG).total)
                  + (v2 - sell_side_friction(v2, CFG).total)
                  - (entry_value + buy_side_friction(entry_value, CFG).total))
        approx(net_pnl(entry_value, v1 + v2, CFG, sell_days=2), manual)

    def test_loser_is_negative(self):
        # 74 @ 100.5, gap-down exit 96.0.
        pnl = net_pnl(74 * 100.5, 74 * 96.0, CFG)
        assert pnl < 0


class TestLiquidFundInterest:
    def test_daily_accrual(self):
        # 200_000 idle for 1 day at 6.5% p.a. -> 200_000 * 0.065 / 365.
        approx(liquid_fund_interest(200_000.0, 1), 200_000.0 * 0.065 / 365.0)
        approx(liquid_fund_interest(200_000.0, 5), 200_000.0 * 0.065 / 365.0 * 5)

    def test_no_interest_on_zero_or_negative_cash(self):
        assert liquid_fund_interest(0.0, 10) == 0.0
        assert liquid_fund_interest(-5_000.0, 10) == 0.0


class TestSTCG:
    def test_flat_20pct_on_profit(self):
        approx(stcg_tax(100_000.0, CFG), 20_000.0)

    def test_no_tax_on_loss_year(self):
        assert stcg_tax(-50_000.0, CFG) == 0.0
        assert stcg_tax(0.0, CFG) == 0.0


class TestFinancialYear:
    def test_fy_boundary(self):
        assert financial_year(date(2022, 3, 31)) == "FY2021-22"
        assert financial_year(date(2022, 4, 1)) == "FY2022-23"
        assert financial_year(date(2026, 9, 13)) == "FY2026-27"
