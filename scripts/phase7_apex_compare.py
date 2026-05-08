"""Compare baseline vs long-only Apex pass rates across tiers."""
import pandas as pd
from pathlib import Path


def load_summary(d: Path) -> pd.DataFrame:
    return pd.read_csv(d / 'summary.csv')


def main():
    base = load_summary(Path('reports/phase7_mc/baseline'))
    long_ = load_summary(Path('reports/phase7_mc/long_only'))

    merged = base.merge(long_, on='account', suffixes=('_baseline', '_long_only'))
    merged['pass_rate_delta'] = merged['pass_rate_long_only'] - merged['pass_rate_baseline']
    merged['median_days_delta'] = merged['median_days_to_pass_long_only'] - merged['median_days_to_pass_baseline']

    out_path = Path('reports/phase7_apex_comparison.csv')
    merged.to_csv(out_path, index=False)
    print(merged[['account', 'pass_rate_baseline', 'pass_rate_long_only', 'pass_rate_delta',
                  'median_days_to_pass_baseline', 'median_days_to_pass_long_only',
                  'drawdown_violated_pct_baseline', 'drawdown_violated_pct_long_only']].to_string(index=False))


if __name__ == '__main__':
    main()
