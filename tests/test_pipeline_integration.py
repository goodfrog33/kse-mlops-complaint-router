"""End-to-end pipeline test against a real Postgres (CI service or the local stack).

Needs the raw data (make data-public) and WAREHOUSE_DSN; skipped when either is missing.
"""

from datetime import date

import psycopg
import pytest

from complaint_router import pipeline, settings

MONTH = date(2024, 6, 1)


def _available() -> bool:
    if not (settings.RAW_DIR / "2024-06.csv").exists():
        return False
    try:
        psycopg.connect(settings.WAREHOUSE_DSN, connect_timeout=2).close()
        return True
    except psycopg.OperationalError:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _available(), reason="needs raw data and a reachable warehouse"),
]


def test_concurrent_schema_creation_does_not_race():
    # Runs first: on an empty database (as in CI) six parallel first runs used to fail with
    # UniqueViolation on CREATE ... IF NOT EXISTS. ensure_schema now takes an advisory lock.
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(6) as pool:
        for future in [pool.submit(pipeline.ensure_schema) for _ in range(6)]:
            future.result()


def snapshot(month: date) -> tuple:
    with pipeline.connect() as conn:
        counts = tuple(
            conn.execute(f"select count(*) from {t} where batch_month = %s", (month,)).fetchone()[0]
            for t in ("raw.complaints", "dwh.complaints", "features.complaint_features")
        )
        digest = conn.execute(
            "select md5(string_agg(f::text, '|' order by complaint_id)) "
            "from features.complaint_features f where batch_month = %s",
            (month,),
        ).fetchone()[0]
    return counts, digest


def test_rerunning_a_month_leaves_identical_rows():
    pipeline.run_month(MONTH)
    first = snapshot(MONTH)
    pipeline.run_month(MONTH)
    assert snapshot(MONTH) == first
    assert first[0] == (3000, 3000, 1013)


def test_broken_batch_fails_the_gate_and_a_clean_rerun_recovers():
    with pytest.raises(pipeline.QualityGateError, match="text_share"):
        pipeline.run_month(MONTH, fault="null_narratives")
    with pytest.raises(pipeline.QualityGateError, match="outside"):
        pipeline.run_month(MONTH, fault="wrong_month")
    pipeline.run_month(MONTH)
    assert snapshot(MONTH)[0] == (3000, 3000, 1013)
