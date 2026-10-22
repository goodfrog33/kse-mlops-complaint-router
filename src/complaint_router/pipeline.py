"""The ETL steps of the ccdb_pipeline DAG, one function per task.

Every step takes the batch month (the first day of the run's data interval) and never
reads the clock. Every write deletes the month's rows and inserts them again in one
transaction, so rerunning an interval leaves the same rows behind.
"""

import io
import re
from datetime import date
from pathlib import Path

import polars as pl
import psycopg

from complaint_router import settings
from complaint_router.text import clean_text_expr

RAW_COLUMNS = [
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
# Produced after routing: never copied past the raw layer (target leakage, design.md 5.2).
LEAKAGE_COLUMNS = [
    "company_public_response",
    "date_sent_to_company",
    "company_response_to_consumer",
    "timely_response",
]
FAULTS = ("null_narratives", "unknown_product", "wrong_month")

DDL = """
create schema if not exists raw;
create schema if not exists dwh;
create schema if not exists features;
create schema if not exists ops;
create table if not exists raw.complaints (
    batch_month date not null, data_version text not null,
    {raw_cols}
);
create index if not exists raw_complaints_month on raw.complaints (batch_month);
create table if not exists dwh.complaints (
    complaint_id bigint primary key, batch_month date not null, date_received date not null,
    product text, product_class text, sub_product text, issue text, company text,
    state text, submitted_via text, narrative text, data_version text not null
);
create index if not exists dwh_complaints_month on dwh.complaints (batch_month);
create table if not exists features.complaint_features (
    complaint_id bigint primary key, batch_month date not null, event_date date not null,
    feature_version text not null, data_version text not null,
    text text not null, text_chars integer not null, submitted_via text not null,
    state text not null, label text not null
);
create index if not exists features_month on features.complaint_features (batch_month);
create table if not exists ops.cleaning_log (
    batch_month date not null, rule text not null, rows_affected integer not null,
    primary key (batch_month, rule)
);
create table if not exists ops.quality_results (
    batch_month date not null, check_name text not null, value double precision,
    threshold text not null, passed boolean not null, data_version text not null,
    primary key (batch_month, check_name)
);
""".format(raw_cols=",\n    ".join(f"{c} text" for c in RAW_COLUMNS))


class QualityGateError(RuntimeError):
    """Raised when a batch fails a validation or quality check."""


# ---------- helpers ----------
def month_key(month: date) -> str:
    return month.strftime("%Y-%m")


def data_version() -> str:
    """The DVC hash of the raw sample directory, read from its .dvc file."""
    text = settings.RAW_DVC_FILE.read_text()
    match = re.search(r"md5:\s*([0-9a-f]+)\.dir", text)
    if not match:
        raise RuntimeError(f"no directory md5 in {settings.RAW_DVC_FILE}")
    return match.group(1)


def connect() -> psycopg.Connection:
    return psycopg.connect(settings.WAREHOUSE_DSN)


def ensure_schema() -> None:
    with connect() as conn:
        conn.execute(DDL)


def read_sql(conn: psycopg.Connection, query: str, params: tuple = ()) -> pl.DataFrame:
    """Run a query through COPY and return every column as a string."""
    sql = psycopg.ClientCursor(conn).mogrify(query, params)
    buf = io.BytesIO()
    with conn.cursor().copy(f"copy ({sql}) to stdout with (format csv, header)") as copy:
        for chunk in copy:
            buf.write(chunk)
    buf.seek(0)
    if buf.getbuffer().nbytes == 0:
        return pl.DataFrame()
    return pl.read_csv(buf, infer_schema=False)


def replace_month(conn: psycopg.Connection, table: str, df: pl.DataFrame, month: date) -> None:
    """Idempotent load: delete the month's rows, then COPY the new ones, in one transaction."""
    conn.execute(f"delete from {table} where batch_month = %s", (month,))
    if df.height == 0:
        return
    buf = io.BytesIO()
    df.write_csv(buf)
    cols = ", ".join(df.columns)
    with conn.cursor().copy(f"copy {table} ({cols}) from stdin with (format csv, header)") as copy:
        copy.write(buf.getvalue())


def staging_path(month: date) -> Path:
    return settings.PROJECT_ROOT / "data/staging" / month_key(month) / "clean.parquet"


# ---------- tasks ----------
def extract(month: date, version: str, fault: str | None = None) -> int:
    """Copy the month's raw file into raw.complaints, unchanged (all columns as text)."""
    path = settings.RAW_DIR / f"{month_key(month)}.csv"
    if not path.exists():
        raise QualityGateError(f"no raw file for {month_key(month)}: {path}")
    df = pl.read_csv(path, infer_schema=False)
    df = inject_fault(df, fault)
    df = df.select(
        pl.lit(month).alias("batch_month"), pl.lit(version).alias("data_version"), *RAW_COLUMNS
    )
    with connect() as conn:
        replace_month(conn, "raw.complaints", df, month)
    return df.height


def inject_fault(df: pl.DataFrame, fault: str | None) -> pl.DataFrame:
    """Break a batch on purpose, to prove the gates fail (README, 'Quality gate')."""
    if fault is None:
        return df
    if fault not in FAULTS:
        raise ValueError(f"unknown fault {fault!r}, expected one of {FAULTS}")
    if fault == "null_narratives":
        return df.with_columns(pl.lit(None, pl.Utf8).alias("consumer_complaint_narrative"))
    if fault == "unknown_product":
        return df.with_columns(pl.lit("Buy now pay later").alias("product"))
    return df.with_columns(pl.lit("2019-01-15").alias("date_received"))


def validate(month: date) -> dict:
    """Schema and interval checks on the raw rows. Fails the run on any violation."""
    with connect() as conn:
        df = read_sql(conn, "select * from raw.complaints where batch_month = %s", (month,))
    problems = []
    if df.height < settings.MIN_ROWS:
        problems.append(f"{df.height} rows < {settings.MIN_ROWS}")
    else:
        missing = set(RAW_COLUMNS) - set(df.columns)
        if missing:
            problems.append(f"missing columns {sorted(missing)}")
        dates = df["date_received"].str.to_date("%Y-%m-%d", strict=False)
        if dates.null_count():
            problems.append(f"{dates.null_count()} unparseable date_received")
        outside = dates.filter(dates.dt.strftime("%Y-%m") != month_key(month)).len()
        if outside:
            problems.append(f"{outside} rows dated outside {month_key(month)}")
        ids = df["complaint_id"].cast(pl.Int64, strict=False)
        if ids.null_count():
            problems.append(f"{ids.null_count()} non-numeric complaint_id")
        if ids.n_unique() != df.height:
            problems.append(f"{df.height - ids.n_unique()} duplicate complaint_id")
    if problems:
        raise QualityGateError(f"validate {month_key(month)}: " + "; ".join(problems))
    return {"rows": df.height}


def transform(month: date) -> dict:
    """Type, deduplicate, map the taxonomy, mask PII, drop leakage. Writes a staging file."""
    with connect() as conn:
        raw = read_sql(conn, "select * from raw.complaints where batch_month = %s", (month,))
    taxonomy = pl.read_csv(settings.TAXONOMY_FILE).select(
        pl.col("cfpb_product").alias("product"), "product_class"
    )
    stats = {}
    df = raw.with_columns(pl.col(pl.Utf8).str.strip_chars().replace("", None))
    before = df.height
    df = df.unique("complaint_id", keep="first", maintain_order=True)
    stats["drop_duplicate_ids"] = before - df.height
    df = df.join(taxonomy, on="product", how="left")
    stats["unmapped_product"] = df.filter(pl.col("product_class").is_null()).height
    has_text = pl.col("consumer_complaint_narrative").is_not_null()
    stats["missing_narrative"] = df.filter(~has_text).height
    masked = clean_text_expr("consumer_complaint_narrative")
    stats["pii_masked"] = df.filter(
        has_text
        & (
            masked
            != pl.col("consumer_complaint_narrative")
            .str.to_lowercase()
            .str.replace_all(r"\s+", " ")
            .str.strip_chars()
        )
    ).height
    clean = df.select(
        pl.col("complaint_id").cast(pl.Int64),
        "batch_month",
        pl.col("date_received").str.to_date("%Y-%m-%d"),
        "product",
        "product_class",
        "sub_product",
        "issue",
        "company",
        "state",
        "submitted_via",
        masked.alias("narrative"),
        "data_version",
    )
    path = staging_path(month)
    path.parent.mkdir(parents=True, exist_ok=True)
    clean.write_parquet(path)
    with connect() as conn:
        conn.execute("delete from ops.cleaning_log where batch_month = %s", (month,))
        for rule, n in stats.items():
            conn.execute("insert into ops.cleaning_log values (%s, %s, %s)", (month, rule, n))
    return {"rows": clean.height, **stats}


def load(month: date) -> dict:
    """Upsert the cleaned month into dwh.complaints (delete-then-insert)."""
    clean = pl.read_parquet(staging_path(month))
    with connect() as conn:
        replace_month(conn, "dwh.complaints", clean, month)
        rows = conn.execute(
            "select count(*) from dwh.complaints where batch_month = %s", (month,)
        ).fetchone()[0]
    return {"rows": rows}


def build_features(month: date) -> dict:
    """One row per complaint with text and a mapped label, tagged with feature_version."""
    with connect() as conn:
        dwh = read_sql(conn, "select * from dwh.complaints where batch_month = %s", (month,))
        feats = (
            dwh.filter(
                pl.col("narrative").is_not_null() & pl.col("product_class").is_not_null()
            ).select(
                pl.col("complaint_id"),
                pl.col("batch_month"),
                pl.col("date_received").alias("event_date"),
                pl.lit(settings.FEATURE_VERSION).alias("feature_version"),
                pl.col("data_version"),
                pl.col("narrative").alias("text"),
                pl.col("narrative").str.len_chars().alias("text_chars"),
                pl.col("submitted_via").fill_null("unknown"),
                pl.col("state").fill_null("unknown"),
                pl.col("product_class").alias("label"),
            )
            if dwh.height
            else pl.DataFrame()
        )
        replace_month(conn, "features.complaint_features", feats, month)
    return {"rows": feats.height}


def quality_check(month: date, version: str) -> dict:
    """Business checks on the loaded month. Records every result, fails on any miss."""
    with connect() as conn:
        dwh = read_sql(
            conn,
            "select complaint_id, product_class, narrative from dwh.complaints "
            "where batch_month = %s",
            (month,),
        )
        prev_ids = read_sql(
            conn,
            "select complaint_id from dwh.complaints "
            "where batch_month = (%s::date - interval '1 month')::date",
            (month,),
        )
        n_feat = conn.execute(
            "select count(*) from features.complaint_features where batch_month = %s", (month,)
        ).fetchone()[0]
        rows = dwh.height
        text_share = dwh["narrative"].is_not_null().mean() if rows else 0.0
        unmapped = dwh["product_class"].null_count() if rows else 0
        overlap = (
            dwh.join(prev_ids, on="complaint_id", how="semi").height
            if rows and prev_ids.height
            else 0
        )
        checks = [
            ("row_count", rows, f">= {settings.MIN_ROWS}", rows >= settings.MIN_ROWS),
            (
                "text_share",
                text_share,
                f">= {settings.MIN_TEXT_SHARE}",
                text_share >= settings.MIN_TEXT_SHARE,
            ),
            ("unmapped_products", unmapped, "== 0", unmapped == 0),
            ("ids_seen_in_previous_month", overlap, "== 0", overlap == 0),
            ("feature_rows", n_feat, "> 0", n_feat > 0),
        ]
        conn.execute("delete from ops.quality_results where batch_month = %s", (month,))
        for name, value, threshold, passed in checks:
            conn.execute(
                "insert into ops.quality_results values (%s, %s, %s, %s, %s, %s)",
                (month, name, float(value), threshold, bool(passed), version),
            )
    failed = [f"{n}={v:.4g} (want {t})" for n, v, t, ok in checks if not ok]
    if failed:
        raise QualityGateError(f"quality_check {month_key(month)} failed: " + "; ".join(failed))
    return {name: value for name, value, _, _ in checks}


def run_month(month: date, fault: str | None = None) -> dict:
    """All steps in DAG order, in-process. Used by tests and by the reproduce command."""
    version = data_version()
    ensure_schema()
    out = {"extract": extract(month, version, fault)}
    out["validate"] = validate(month)
    out["transform"] = transform(month)
    out["load"] = load(month)
    out["build_features"] = build_features(month)
    out["quality_check"] = quality_check(month, version)
    return out
