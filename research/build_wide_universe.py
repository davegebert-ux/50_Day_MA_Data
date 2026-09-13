
from tradingview_screener import Query, Column
import pandas as pd

pd.set_option('display.max_rows', 10)
pd.set_option('display.width', 200)

# Wide universe pull: NASDAQ + NYSE primary-listed common stocks,
# basic liquidity floor only (no trend/momentum filters here --
# this is just the starting universe list for historical backtesting)
query = (
    Query()
    .select(
        'name', 'close', 'market_cap_basic', 'volume',
        'average_volume_10d_calc', 'is_symbol_primary_listing',
    )
    .where(
        Column('market_cap_basic') > 100_000_000,
        Column('average_volume_10d_calc') > 1_000_000,
        Column('is_symbol_primary_listing') == True,
    )
    .limit(5000)
)

count, df = query.get_scanner_data()
print(f"Total matches: {count}")
print(df.head(20))

# Save locally so we only ever do this pull once
df.to_csv('wide_universe_snapshot.csv', index=False)
print(f"\nSaved {len(df)} tickers to wide_universe_snapshot.csv")
