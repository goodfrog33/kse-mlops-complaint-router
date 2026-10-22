"""ccdb_pipeline: one CFPB month per run.

The batch is the run's data interval. The schedule declares the interval explicitly with
CronDataIntervalTimetable("@monthly"), because in Airflow 3 a plain cron schedule makes
data_interval_start equal to data_interval_end. A run for logical date 2024-06-01 therefore
processes June 2024 and nothing else, whenever it is executed.

Tasks: extract -> validate -> transform -> load -> build_features -> quality_check.
Optional run conf {"fault": "null_narratives" | "unknown_product" | "wrong_month"} breaks
the batch on purpose to prove the gates fail.
"""

from datetime import date

import pendulum
from airflow.sdk import Param, dag, get_current_context, task
from airflow.timetables.interval import CronDataIntervalTimetable

from complaint_router import pipeline


def _month() -> date:
    start = get_current_context()["data_interval_start"]
    return date(start.year, start.month, 1)


@dag(
    dag_id="ccdb_pipeline",
    schedule=CronDataIntervalTimetable("@monthly", timezone="UTC"),
    start_date=pendulum.datetime(2023, 4, 1, tz="UTC"),
    # Last month in the raw sample (data/raw/ccdb_sample/2026-06.csv).
    end_date=pendulum.datetime(2026, 6, 1, tz="UTC"),
    catchup=False,
    # Months are independent and every write is scoped to its own month, so they can overlap.
    max_active_runs=6,
    params={"fault": Param(None, type=["null", "string"], enum=[None, *pipeline.FAULTS])},
    tags=["complaint-router"],
)
def ccdb_pipeline():
    @task
    def extract() -> dict:
        pipeline.ensure_schema()
        version = pipeline.data_version()
        fault = get_current_context()["params"].get("fault")
        rows = pipeline.extract(_month(), version, fault)
        return {"rows": rows, "data_version": version}

    @task
    def validate(extracted: dict) -> dict:
        return pipeline.validate(_month())

    @task
    def transform(validated: dict) -> dict:
        return pipeline.transform(_month())

    @task
    def load(transformed: dict) -> dict:
        return pipeline.load(_month())

    @task
    def build_features(loaded: dict) -> dict:
        return pipeline.build_features(_month())

    @task
    def quality_check(extracted: dict, featured: dict) -> dict:
        return pipeline.quality_check(_month(), extracted["data_version"])

    extracted = extract()
    featured = build_features(load(transform(validate(extracted))))
    quality_check(extracted, featured)


ccdb_pipeline()
