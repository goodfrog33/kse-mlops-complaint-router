"""Profile the CFPB complaint data for the Lab 1 data justification.

Reads two local copies of the public CFPB Consumer Complaint Database:

* ``data/raw/hf_snapshot/*.parquet`` - a July 2026 Parquet mirror that still carries the
  ``consumer_complaint_narrative`` column (source of the training text).
* ``data/raw/complaints.csv`` - the current official export, which no longer has that column.

Writes ``docs/data/profile.json`` (numbers used in the design doc and the deck) and
``data/sample/complaints_sample.csv`` (a small stratified sample of real rows).

Usage: uv run python scripts/profile_data.py
"""

import glob
import json
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "data/raw/hf_snapshot"
OFFICIAL_CSV = ROOT / "data/raw/complaints.csv"
TAXONOMY = ROOT / "configs/taxonomy_map.csv"
OUT_JSON = ROOT / "docs/data/profile.json"
OUT_SAMPLE = ROOT / "data/sample/complaints_sample.csv"

TRAIN = ("2023-04", "2025-03")
VAL = ("2025-04", "2025-06")
TEST = ("2025-07", "2025-09")
SAMPLE_WINDOW = ("2025-01", "2025-06")
SAMPLE_PER_CLASS = 20
COLUMNS = [
    "complaint_id",
    "date_received",
    "product",
    "sub_product",
    "issue",
    "company",
    "state",
    "submitted_via",
    "date_sent_to_company",
    "company_response_to_consumer",
    "timely_response",
    "consumer_complaint_narrative",
]


def load_snapshot() -> pl.DataFrame:
    # Files were converted in chunks: all-null columns come back as Float64 and some
    # chunks carry a time zone on the dates, so every file is normalised before concat.
    parts = [
        pl.read_parquet(f, columns=COLUMNS).with_columns(
            pl.col("date_received", "date_sent_to_company").dt.date(),
            pl.exclude("date_received", "date_sent_to_company").cast(pl.Utf8),
        )
        for f in sorted(glob.glob(str(SNAPSHOT / "*.parquet")))
    ]
    taxonomy = pl.read_csv(TAXONOMY).select("cfpb_product", "product_class")
    return (
        pl.concat(parts)
        .with_columns(
            pl.col("date_received").dt.strftime("%Y-%m").alias("month"),
            pl.col("consumer_complaint_narrative").is_not_null().alias("has_text"),
        )
        .join(taxonomy, left_on="product", right_on="cfpb_product", how="left")
    )


def in_window(window: tuple[str, str]) -> pl.Expr:
    return pl.col("month").is_between(pl.lit(window[0]), pl.lit(window[1]))


def main() -> None:
    df = load_snapshot()
    text = df.filter(pl.col("has_text"))

    monthly = (
        df.group_by("month")
        .agg(pl.len().alias("rows"), pl.col("has_text").sum().alias("with_text"))
        .drop_nulls("month")
        .sort("month")
    )
    yearly = (
        monthly.with_columns(pl.col("month").str.slice(0, 4).alias("year"))
        .group_by("year")
        .agg(pl.col("rows").sum(), pl.col("with_text").sum())
        .sort("year")
    )
    products = (
        df.group_by("product", "product_class")
        .agg(
            pl.len().alias("rows"),
            pl.col("has_text").sum().alias("with_text"),
            pl.col("month").min().alias("first_month"),
            pl.col("month").max().alias("last_month"),
        )
        .sort("rows", descending=True)
    )
    train_text = text.filter(in_window(TRAIN))
    classes = (
        train_text.group_by("product_class")
        .agg(pl.len().alias("rows"))
        .with_columns((pl.col("rows") / pl.col("rows").sum()).round(4).alias("share"))
        .sort("rows", descending=True)
    )
    lengths = train_text.select(
        pl.col("consumer_complaint_narrative").str.len_chars().alias("chars"),
        pl.col("consumer_complaint_narrative").str.split(" ").list.len().alias("words"),
    )
    masked = train_text.select(
        pl.col("consumer_complaint_narrative").str.contains("XXXX").mean()
    ).item()
    sent_delay = (
        df.filter(in_window(TRAIN))
        .select(
            (pl.col("date_sent_to_company") - pl.col("date_received"))
            .dt.total_days()
            .alias("days")
        )
        .drop_nulls()
    )
    responses = (
        df.filter(in_window(TRAIN))
        .group_by("company_response_to_consumer")
        .agg(pl.len().alias("rows"))
        .sort("rows", descending=True)
    )
    split_sizes = {
        name: text.filter(in_window(w)).height
        for name, w in {"train": TRAIN, "validation": VAL, "test": TEST}.items()
    }
    unmapped = df.filter(pl.col("product_class").is_null()).height

    official_header = pl.read_csv(OFFICIAL_CSV, n_rows=0).columns
    official_monthly = (
        pl.scan_csv(OFFICIAL_CSV, infer_schema=False)
        .select(pl.col("Date received").str.slice(0, 7).alias("month"))
        .group_by("month")
        .agg(pl.len().alias("rows"))
        .sort("month")
        .collect()
    )

    pct = [0.1, 0.5, 0.9, 0.99]
    profile = {
        "snapshot": {
            "rows": df.height,
            "rows_with_text": text.height,
            "date_min": str(df["date_received"].min())[:10],
            "date_max": str(df["date_received"].max())[:10],
            "monthly": monthly.to_dicts(),
            "yearly": yearly.to_dicts(),
        },
        "products": products.to_dicts(),
        "unmapped_rows": unmapped,
        "splits": {"windows": {"train": TRAIN, "validation": VAL, "test": TEST}, **split_sizes},
        "train_class_mix": classes.to_dicts(),
        "train_text_length": {
            "chars": {f"p{int(p * 100)}": lengths["chars"].quantile(p) for p in pct},
            "words": {f"p{int(p * 100)}": lengths["words"].quantile(p) for p in pct},
            "share_with_XXXX_mask": round(masked, 4),
        },
        "days_received_to_sent": {
            f"p{int(p * 100)}": sent_delay["days"].quantile(p) for p in [0.5, 0.9, 0.99]
        },
        "company_response_train_window": responses.to_dicts(),
        "official_export": {
            "columns": official_header,
            "has_narrative_column": any("narrative" in c.lower() for c in official_header),
            "rows": int(official_monthly["rows"].sum()),
            "monthly_recent": official_monthly.filter(pl.col("month") >= "2025-07").to_dicts(),
        },
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(profile, indent=1, default=str))

    sample = (
        text.filter(in_window(SAMPLE_WINDOW) & pl.col("product_class").is_not_null())
        .with_columns(pl.int_range(pl.len()).shuffle(seed=42).over("product_class").alias("r"))
        .filter(pl.col("r") < SAMPLE_PER_CLASS)
        .sort("product_class", "date_received")
        .select(
            "complaint_id",
            "date_received",
            "product_class",
            "product",
            "sub_product",
            "issue",
            "company",
            "state",
            "submitted_via",
            "date_sent_to_company",
            "company_response_to_consumer",
            "consumer_complaint_narrative",
        )
    )
    OUT_SAMPLE.parent.mkdir(parents=True, exist_ok=True)
    sample.write_csv(OUT_SAMPLE)
    written = f"{OUT_JSON.relative_to(ROOT)} and {OUT_SAMPLE.relative_to(ROOT)}"
    print(f"wrote {written} ({sample.height} sample rows)")


if __name__ == "__main__":
    main()
