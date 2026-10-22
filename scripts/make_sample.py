"""Draw the raw monthly sample that DVC versions (data/raw/ccdb_sample/).

Source: the July 2026 Parquet mirror of the CFPB export (data/raw/hf_snapshot/, see
docs/data-profile.md for where it comes from). The full export is 5.6 GB, so the
pipeline works on a sample:

* months 2023-04 .. 2026-06 (39 monthly batches),
* per month, the 3,000 complaints with the smallest value of a fixed integer hash of
  ``complaint_id`` (Knuth multiplicative hash). This is a uniform random sample that is
  deterministic, independent of row order and stable across library versions,
* all complaints, with or without narrative, so the share of text per month is the real one,
* rows written as CSV, one file per month, sorted by ``complaint_id``, columns as published.

Usage: uv run python scripts/make_sample.py
"""

import glob
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "data/raw/hf_snapshot"
OUT = ROOT / "data/raw/ccdb_sample"
FIRST, LAST = "2023-04", "2026-06"
PER_MONTH = 3000
COLUMNS = [
    "complaint_id",
    "date_received",
    "product",
    "sub_product",
    "issue",
    "sub_issue",
    "consumer_complaint_narrative",
    "company_public_response",
    "company",
    "state",
    "zip_code",
    "tags",
    "submitted_via",
    "date_sent_to_company",
    "company_response_to_consumer",
    "timely_response",
]


def load() -> pl.DataFrame:
    # Chunks of the mirror differ in types (all-null columns are Float64, some dates
    # carry a time zone), so every file is normalised before the concat.
    dates = ["date_received", "date_sent_to_company"]
    parts = [
        pl.read_parquet(f, columns=COLUMNS).with_columns(
            pl.col(dates).dt.date(), pl.exclude(dates).cast(pl.Utf8)
        )
        for f in sorted(glob.glob(str(SNAPSHOT / "*.parquet")))
    ]
    return pl.concat(parts)


def main() -> None:
    df = (
        load()
        .with_columns(month=pl.col("date_received").dt.strftime("%Y-%m"))
        .filter(pl.col("month").is_between(pl.lit(FIRST), pl.lit(LAST)))
        .with_columns(key=(pl.col("complaint_id").cast(pl.UInt64) * 2654435761) % (2**32))
    )
    OUT.mkdir(parents=True, exist_ok=True)
    for f in OUT.glob("*.csv"):
        f.unlink()
    for (month,), part in df.group_by("month"):
        sample = (
            part.sort("key", "complaint_id")
            .head(PER_MONTH)
            .sort(pl.col("complaint_id").cast(pl.UInt64))
        )
        sample.select(COLUMNS).write_csv(OUT / f"{month}.csv")
    files = sorted(OUT.glob("*.csv"))
    print(f"wrote {len(files)} monthly files to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
