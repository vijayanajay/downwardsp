import sys

import duckdb

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
con = duckdb.connect("data/db/nse_market.duckdb", read_only=True)

for symbol, ex in [("TCC", "2026-09-04"), ("PGIL", "2026-09-11")]:
    print(f"=== {symbol} around ex-date {ex}: raw vs adjusted ===")
    print(con.execute(f"""
        SELECT date, round(close, 1) AS close_raw, round(close_adj, 1) AS close_adj,
               volume, round(volume_adj, 0) AS vol_adj
        FROM daily_bars WHERE symbol = '{symbol}'
          AND date BETWEEN date '{ex}' - INTERVAL 3 DAY AND date '{ex}' + INTERVAL 1 DAY
        ORDER BY date
    """).df())
    print(con.execute(f"""
        SELECT date, round(close_adj / lag(close_adj) OVER (ORDER BY date) - 1, 4) AS adj_pct_chg,
               round(close / lag(close) OVER (ORDER BY date) - 1, 4) AS raw_pct_chg
        FROM daily_bars WHERE symbol = '{symbol}'
          AND date BETWEEN date '{ex}' - INTERVAL 3 DAY AND date '{ex}' + INTERVAL 1 DAY
        ORDER BY date
    """).df())
    print()

print("=== corporate actions with applied factors ===")
print(con.execute("""
    SELECT symbol, ex_date, action_type, ratio_a, ratio_b,
           round(adjustment_factor, 4) AS af
    FROM corporate_actions WHERE adjustment_factor IS NOT NULL ORDER BY ex_date
""").df())
con.close()
