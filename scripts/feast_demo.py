"""Optional Lab 2 part: serve company features from Feast and prove point-in-time correctness.

1. Build feature_repo/data/company_monthly.parquet from dwh.complaints: one row per company
   per month that passed the quality gate, stamped with the first day of the next month.
2. feast apply the definitions in feature_repo/definitions.py.
3. Offline retrieval with an entity dataframe whose timestamps fall between two feature rows:
   each must get the earlier row. The script fails if any does not.
4. Materialize to the SQLite online store and read one entity online (what Lab 4 serving uses).

Usage: make feast-demo
"""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import polars as pl
from feast import FeatureStore

from complaint_router import pipeline

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT / "feature_repo"
FEATURES = [
    "company_monthly_stats:complaints_last_month",
    "company_monthly_stats:credit_reporting_share_last_month",
    "company_monthly_stats:text_share_last_month",
]


def build_source() -> pd.DataFrame:
    with pipeline.connect() as conn:
        df = pipeline.read_sql(
            conn,
            """
            select d.company, d.batch_month, d.product_class, d.narrative is not null as has_text
            from dwh.complaints d
            where d.company is not null and d.batch_month not in (
                select batch_month from ops.quality_results where not passed)
            """,
        )
    stats = (
        df.with_columns(pl.col("batch_month").str.to_date())
        .group_by("company", "batch_month")
        .agg(
            pl.len().cast(pl.Int64).alias("complaints_last_month"),
            (pl.col("product_class") == "Credit reporting")
            .mean()
            .cast(pl.Float32)
            .alias("credit_reporting_share_last_month"),
            (pl.col("has_text") == "t").mean().cast(pl.Float32).alias("text_share_last_month"),
        )
        # Known only once the month is over: stamp it with the first day of the next month.
        .with_columns(
            pl.col("batch_month")
            .dt.offset_by("1mo")
            .cast(pl.Datetime("us", "UTC"))
            .alias("event_timestamp")
        )
        .drop("batch_month")
        .sort("company", "event_timestamp")
    )
    out = REPO / "data/company_monthly.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    pdf = stats.to_pandas()
    pdf.to_parquet(out, index=False)
    print(
        f"source: {len(pdf)} rows, {pdf['company'].nunique()} companies -> {out.relative_to(ROOT)}"
    )
    return pdf


def main() -> int:
    source = build_source()
    store = FeatureStore(repo_path=str(REPO))
    from definitions import company, company_monthly_stats  # noqa: E402  (repo on sys.path)

    store.apply([company, company_monthly_stats])

    # Three companies with rows for 2024-07-01 (June stats) and 2024-08-01 (July stats).
    june, july = pd.Timestamp("2024-07-01", tz="UTC"), pd.Timestamp("2024-08-01", tz="UTC")
    both = source.groupby("company")["event_timestamp"].apply(lambda s: {june, july} <= set(s))
    companies = sorted(both[both].index)[:3]
    entity_df = pd.DataFrame(
        {
            "company": companies,
            # Between the two rows: a complaint received mid-July.
            "event_timestamp": [pd.Timestamp("2024-07-15 12:00", tz="UTC")] * len(companies),
        }
    )
    got = store.get_historical_features(entity_df=entity_df, features=FEATURES).to_df()

    print("\nentity rows: complaint on 2024-07-15; feature rows exist at 2024-07-01 and 2024-08-01")
    ok = True
    for name in companies:
        rows = source[source["company"] == name].set_index("event_timestamp")
        earlier, later = rows.loc[june], rows.loc[july]
        value = got.loc[got["company"] == name, "complaints_last_month"].item()
        right = value == earlier["complaints_last_month"]
        ok &= bool(right)
        print(
            f"  {name[:38]:38s} got {value:>4}  earlier row {earlier['complaints_last_month']:>4}"
            f"  later row {later['complaints_last_month']:>4}  -> {'earlier' if right else 'WRONG'}"
        )

    end = datetime(2024, 8, 2, tzinfo=UTC)
    store.materialize(start_date=end - timedelta(days=62), end_date=end)
    online = store.get_online_features(
        features=FEATURES, entity_rows=[{"company": companies[0]}]
    ).to_dict()
    print(
        f"\nonline store, {companies[0]}: complaints_last_month = "
        f"{online['complaints_last_month'][0]} (latest row before 2024-08-02)"
    )
    print("\npoint-in-time join:", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.path.insert(0, str(REPO))
    sys.exit(main())
