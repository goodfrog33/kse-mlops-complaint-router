"""Small helpers run inside the airflow container (see Makefile).

wait-backfill        block until the newest ccdb_pipeline backfill has completed
reset-conf DATE      clear the conf of the run with that logical date. Airflow 3 keeps a run's
                     original conf when a backfill reprocesses it, so a month that once ran
                     with {"fault": ...} would be broken again on every rerun.
"""

import sys
import time

from airflow.models.backfill import Backfill
from airflow.models.dagrun import DagRun
from airflow.utils.session import create_session
from sqlalchemy import select

DAG_ID = "ccdb_pipeline"


def wait_backfill() -> None:
    while True:
        with create_session() as session:
            bf = session.scalars(
                select(Backfill).where(Backfill.dag_id == DAG_ID).order_by(Backfill.id.desc())
            ).first()
            if bf is None or bf.completed_at is not None:
                break
        time.sleep(5)
    with create_session() as session:
        runs = session.scalars(select(DagRun).where(DagRun.backfill_id == bf.id)).all()
        for run in sorted(runs, key=lambda r: r.logical_date):
            print(run.logical_date.date(), run.state)


def reset_conf(logical_date: str) -> None:
    with create_session() as session:
        runs = session.scalars(select(DagRun).where(DagRun.dag_id == DAG_ID)).all()
        for run in runs:
            if run.logical_date and run.logical_date.date().isoformat() == logical_date:
                run.conf = {}
                print(f"cleared conf of {run.run_id}")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "wait-backfill":
        wait_backfill()
    elif cmd == "reset-conf":
        reset_conf(sys.argv[2])
    else:
        raise SystemExit(__doc__)
