"""Convert raw CSVs to parquet for faster loading.

One-shot script. Run from repo root:
    python scripts/convert_to_parquet.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def convert_nq_1min(src: Path, dst: Path) -> None:
    """Load NQ 1-min CSV and write to parquet.

    Expected columns: Symbol, Date, Time, Open, High, Low, Close, TotalVolume
    Date format: DD/MM/YYYY
    """
    print(f"Loading NQ 1-min data from {src} ...")
    df = pd.read_csv(src)

    # Combine Date + Time into a single timestamp column
    df["timestamp"] = pd.to_datetime(
        df["Date"] + " " + df["Time"], format="%d/%m/%Y %H:%M:%S"
    )

    # Rename and select columns
    df = df.rename(columns={
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "TotalVolume": "volume",
    })

    # Drop Symbol and original Date/Time columns
    cols_to_drop = [c for c in ["Symbol", "Date", "Time"] if c in df.columns]
    df = df.drop(columns=cols_to_drop)

    # Reorder and sort
    df = df[["timestamp", "open", "high", "low", "close", "volume"]]
    df = df.sort_values("timestamp").reset_index(drop=True)

    dst.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(dst, engine="pyarrow", compression="snappy", index=False)

    size_mb = dst.stat().st_size / 1_048_576
    print(f"  Rows:       {len(df):,}")
    print(f"  Date range: {df['timestamp'].min()} -> {df['timestamp'].max()}")
    print(f"  Output:     {dst}  ({size_mb:.1f} MB)")


def convert_qqq_daily(src: Path, dst: Path) -> None:
    """Load QQQ daily CSV and write to parquet.

    The first row is a metadata row: 'Ticker,,QQQ' - skip it.
    After skipping, columns are: Price, date, close (or similar).
    Dates are YYYY-MM-DD.
    """
    print(f"Loading QQQ daily data from {src} ...")

    # Read skipping the metadata row (row 0 is 'Ticker,,QQQ')
    df = pd.read_csv(src, skiprows=1)

    # Normalize column names to lowercase
    df.columns = [c.strip().lower() for c in df.columns]

    # Identify the date column - may be named 'date' or similar
    date_col = next((c for c in df.columns if "date" in c), None)
    if date_col is None:
        raise ValueError(f"Cannot find date column in {src}. Columns: {list(df.columns)}")

    # Parse dates
    df["timestamp"] = pd.to_datetime(df[date_col], format="%Y-%m-%d")

    # Keep only the columns we care about
    price_cols = [c for c in df.columns if c in ("close", "open", "high", "low", "volume", "adj close", "adj_close")]
    keep_cols = ["timestamp"] + price_cols
    df = df[keep_cols].sort_values("timestamp").reset_index(drop=True)

    dst.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(dst, engine="pyarrow", compression="snappy", index=False)

    size_mb = dst.stat().st_size / 1_048_576
    print(f"  Rows:       {len(df):,}")
    print(f"  Date range: {df['timestamp'].min().date()} -> {df['timestamp'].max().date()}")
    print(f"  Output:     {dst}  ({size_mb:.1f} MB)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=Path("Data"))
    parser.add_argument("--out-dir", type=Path, default=Path("data"))
    args = parser.parse_args()

    raw_dir: Path = args.raw_dir
    out_dir: Path = args.out_dir

    errors = 0

    # NQ 1-min
    nq_src = raw_dir / "nq-10y-1min.csv"
    nq_dst = out_dir / "nq_1min.parquet"
    if not nq_src.exists():
        print(f"[SKIP] NQ 1-min source not found: {nq_src}")
        errors += 1
    else:
        try:
            convert_nq_1min(nq_src, nq_dst)
        except Exception as exc:
            print(f"[ERROR] NQ 1-min conversion failed: {exc}")
            errors += 1

    print()

    # QQQ daily
    qqq_src = raw_dir / "QQQ_daily.csv"
    qqq_dst = out_dir / "qqq_daily.parquet"
    if not qqq_src.exists():
        print(f"[SKIP] QQQ daily source not found: {qqq_src}")
        errors += 1
    else:
        try:
            convert_qqq_daily(qqq_src, qqq_dst)
        except Exception as exc:
            print(f"[ERROR] QQQ daily conversion failed: {exc}")
            errors += 1

    if errors:
        print(f"\nDone with {errors} error(s). Check messages above.")
        return 1

    print("\nAll conversions complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
