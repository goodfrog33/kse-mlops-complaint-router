# complaint-router

KSE MLOps course project: one ML system built over five labs.

## The problem in five sentences

A complaint intake desk receives fifteen to twenty thousand free-text consumer complaints a day about financial products, and each one has to reach the team that owns that product. Misrouted complaints wait days to be reassigned while the regulatory response clock keeps running. complaint-router predicts the product queue from the complaint text on arrival, auto-routes the confident cases and gives triage agents a suggestion for the rest. It is trained on the public CFPB Consumer Complaint Database, whose product taxonomy, class mix and vocabulary drift over time. The system therefore ingests, versions, retrains, serves and monitors continuously, instead of being a one-off notebook.

| | |
|---|---|
| Design document | [`docs/design.md`](docs/design.md) (Lab 2 revised 5.2, 5.3, 5.4, 6.1, 6.2, 6.4, 6.5, 6.8, 7.2) |
| Presentation (Lab 1) | [`docs/presentation/complaint-router-lab1.pptx`](docs/presentation/complaint-router-lab1.pptx) ([PDF](docs/presentation/complaint-router-lab1.pdf)) |
| Presentation (Lab 2) | [`docs/presentation/complaint-router-lab2.pptx`](docs/presentation/complaint-router-lab2.pptx) ([PDF](docs/presentation/complaint-router-lab2.pdf)) |
| Data profile | [`docs/data-profile.md`](docs/data-profile.md) |
| Label map | [`configs/taxonomy_map.csv`](configs/taxonomy_map.csv): 20 CFPB product names → 10 classes |
| Lab 2 defense notes (Ukrainian) | [`docs/defense/lab2-qa.md`](docs/defense/lab2-qa.md): the 20 questions of Appendix 2, answered from this repository |

## Run it from a clean clone

Requires Docker, [uv](https://docs.astral.sh/uv/) and `make`. Each step below is one command.

```bash
make setup        # pinned Python environment from uv.lock + pre-commit hook
make up           # Postgres, MinIO, MLflow and Airflow in Docker (creates .env from .env.example)
make data-seed    # fill your empty MinIO from the public mirror (once)
make data         # dvc pull the raw data from MinIO
make backfill     # one Airflow run per month, 2023-04 .. 2025-09 (about 2 minutes)
make train        # rule baselines + TF-IDF model, logged and registered as @champion
make reproduce    # rebuild the @champion run from its commit and data version
make backtest     # optional: rolling-origin backtest, 3 monthly folds
make feast-demo   # optional: Feast point-in-time join + online read
```

| Service | URL |
|---|---|
| Airflow (no login, local only) | http://localhost:8080 |
| MLflow | http://localhost:5050 |
| MinIO console (`minioadmin` / `minioadmin123`) | http://localhost:9101 |
| Postgres warehouse | `postgresql://complaints:complaints@localhost:5442/warehouse` |

Host ports avoid 5000, 5432 and 9000 on purpose. `.env.example` sets `MLFLOW_ENABLE_PROXY_MULTIPART_DOWNLOAD=false`, so MLflow downloads go through the server and never to `minio:9000`. The Apple Silicon `cryptography` workaround was not needed (Docker Desktop 29.3).

## Data (step 2)

The full CFPB export is 5.6 GB, so the pipeline works on a sample, drawn by [`scripts/make_sample.py`](scripts/make_sample.py) from the July 2026 snapshot described in design.md §5.2:

- months **2023-04 .. 2026-06**, one CSV per month, 3,000 complaints each (39 files, 117,000 rows, 66 MB);
- per month, the 3,000 complaints with the smallest Knuth hash of `complaint_id`: a uniform sample that is byte-identical on every rerun;
- all complaints, with and without text, so each month keeps its real share of text.

| | |
|---|---|
| Tracked path | `data/raw/ccdb_sample/` → [`data/raw/ccdb_sample.dvc`](data/raw/ccdb_sample.dvc) |
| Data version (DVC hash) | **`0319edfe2ad83a39d4d8241602a462f8`** |
| Default remote | `minio`: S3 at `s3://dvc/complaint-router`, endpoint `http://localhost:9100` (`.dvc/config`; keys only in the git-ignored `.dvc/config.local`, written by `make dvc-creds`) |
| Public remote | `public`: read-only HTTP, the [`dvc-store`](https://github.com/goodfrog33/kse-mlops-complaint-router/tree/dvc-store) branch of this repository |
| Pull | `make data` (MinIO, after `make data-seed`) or `make data-public` (no credentials, no Docker) |
| Checksum of what arrives | `cat data/raw/ccdb_sample/*.csv \| md5` → **`0bba9005abf7d408af110f7a192fc13a`** (`md5sum` on Linux) |

## Pipeline (step 3)

[`dags/ccdb_pipeline.py`](dags/ccdb_pipeline.py), Airflow 3.3.1: `extract → validate → transform → load → build_features → quality_check`. The code lives in [`src/complaint_router/pipeline.py`](src/complaint_router/pipeline.py).

**The batch is the data interval, never the clock.** Airflow 3 no longer creates intervals for `@monthly`, so the DAG declares them: `schedule=CronDataIntervalTimetable("@monthly", timezone="UTC")`. Every task takes its month from `data_interval_start`. Backfill run `2024-06-01` processes June 2024 whenever it executes.

**Concurrency.** Up to 6 months run at once (`max_active_runs=6`): every write is scoped to its own month, so runs do not touch each other's rows. All 39 months take 113 s (they took about 30 minutes one at a time), with the same 32 / 7 outcome and the same rows.

**Idempotent loads.** Every layer is written with `delete from <table> where batch_month = %s` followed by `COPY` of the month, in one transaction (`replace_month`). The same month run twice:

```text
make run-month MONTH=2024-06-01            make run-month MONTH=2024-06-01   (again)
raw.complaints              3000            raw.complaints              3000
dwh.complaints              3000            dwh.complaints              3000
features.complaint_features 1013            features.complaint_features 1013
checks passed               5/5             checks passed               5/5
```

The content is identical too: `md5(string_agg(row, '|' order by complaint_id))` over the month's feature rows is `e15aa30f3cfa200c820b39234586c457` before and after a third rerun.

## Quality gate (step 3)

Two gates: `validate` (columns, every date inside the interval, numeric and unique `complaint_id`) and `quality_check` (≥ 1,000 rows, text share ≥ 0.13, zero unmapped products, no id reused from the previous month, feature rows > 0). Results land in `ops.quality_results`; training skips any month with a failed check.

**Broken on purpose.** `make run-month MONTH=2024-06-01 FAULT=null_narratives` blanks every narrative in the extracted batch (other faults: `unknown_product`, `wrong_month`). The run turns red at `quality_check`:

```text
QualityGateError: quality_check 2024-06 failed: text_share=0 (want >= 0.13); feature_rows=0 (want > 0)
```

![failed quality_check task](docs/img/quality_gate_failed.png)

A clean rerun of the same month succeeds (Airflow keeps a run's old conf on reprocessing, so `make run-month` clears it first; see [`scripts/airflow_ctl.py`](scripts/airflow_ctl.py)):

![successful run graph](docs/img/run_graph_success.png)

**Broken for real.** The backfill over all 39 months fails exactly the months where CFPB stopped publishing text: text share 0.100 (2025-12), 0.036, 0.028, 0.030, 0.038, 0.010, 0.002 (2026-06). 32 months pass.

![DAG overview](docs/img/dag_overview.png)

## Features (step 4)

`build_features` fills `features.complaint_features`: one row per complaint with text and a mapped label, plus `feature_version = v1` and `data_version`. Where each feature comes from at serving time is in design.md §5.3; `company` was dropped from the model because its training and serving sources differ.

**Feast (optional part).** [`feature_repo/`](feature_repo/) defines the entity `company` and the feature view `company_monthly_stats` (complaints, credit-reporting share and text share of the company's *previous* month), built from `dwh.complaints`. A month's statistics only exist once the month is over, so each row is stamped with the first day of the next month. `make feast-demo` applies the repo, asks for complaints dated 2024-07-15, between the 2024-07-01 and 2024-08-01 rows, and fails unless every entity gets the earlier row:

```text
entity rows: complaint on 2024-07-15; feature rows exist at 2024-07-01 and 2024-08-01
  ALLY FINANCIAL INC.                    got    3  earlier row    3  later row    5  -> earlier
  AMERICAN EXPRESS COMPANY               got    6  earlier row    6  later row    8  -> earlier
  Affirm Holdings, Inc                   got    4  earlier row    4  later row    3  -> earlier
online store, ALLY FINANCIAL INC.: complaints_last_month = 5 (latest row before 2024-08-02)
point-in-time join: OK
```

The same definitions are materialized into the SQLite online store and read back with `get_online_features`, which is the path Lab 4 serving can use.

## Baseline vs model (step 5)

Time split (design.md §5.4): train 2023-04 .. 2025-03 (23,918 texts), validation 2025-04 .. 06 (2,377), test 2025-07 .. 09 (1,933). Never random: complaints about one event share wording and cluster in time.

| Model | Test macro-F1 | Test accuracy | Notes |
|---|---:|---:|---|
| Majority class (always *Credit reporting*) | 0.089 | 0.797 | the floor; accuracy looks fine because 80% is one class |
| Keyword rule (first match wins) | 0.384 | 0.712 | design.md §5.3 |
| **TF-IDF + logistic regression** | **0.638** | **0.876** | at threshold 0.77 (set on validation): 77% auto-routed at 0.948 precision |

The model beats the rule on every metric. It misses the 0.80 macro-F1 target of design.md §3, mostly because *Debt or credit management* (0.2% of data) gets F1 0.00 and *Credit or prepaid card* 0.52. Per-class scores are in design.md §7.2.

**Rolling-origin backtest** (`make backtest`; each fold trains on the 24 months right before its test month):

| Test month | Model macro-F1 | Rule macro-F1 |
|---|---:|---:|
| 2025-07 | 0.652 | 0.374 |
| 2025-08 | 0.682 | 0.377 |
| 2025-09 | 0.716 | 0.405 |
| mean ± std | **0.684 ± 0.026** | 0.385 ± 0.014 |

The model wins every fold. Folds score higher than the registered model (0.638) because they train up to the month before the test, while the registered model stops three months earlier for validation: freshness is worth about 0.045 macro-F1, an argument for monthly retraining.

**Random split, for comparison** (`scripts/random_split_check.py`, run tagged `split=random`): 0.591 macro-F1, *lower* than the time split. On this uniform 0.5% sample, complaints about one event rarely land together, so the leakage a random split usually causes does not show; the difference is within the month-to-month spread above. The time split stays because it is the only one that matches use.

## Tracking and registry (step 6)

MLflow 3.16.1 with a Postgres backend store (`mlflow` database) and a MinIO artifact store (`s3://mlflow`, proxied by the server), started by `make up`. Every run, rules included, logs its parameters, metrics and the tags `data_version` and `git_commit`.

Model `complaint-router`, **version 2, alias `champion`**: `data_version=0319edfe2ad83a39d4d8241602a462f8`, `git_commit=45cb4224fe48df5a26c4cbffd9a85b87e832a0c6`, `feature_version=v1`, `auto_threshold=0.77`. Version 1 is the same model on the earlier data version `3e2aa70e…` (before a sampling fix that touched only the 2026 files), with identical metrics.

![MLflow model version 2](docs/img/mlflow_model_v2.png)

**Reproduce:** `make reproduce` reads the two tags from the registry, checks out the commit in a git worktree under `.repro/`, `dvc pull`s and verifies the data version, rebuilds the features with that commit's pipeline code and retrains:

```text
version 2: commit 45cb4224fe, data 0319edfe2ad83a39d4d8241602a462f8, test_macro_f1 0.6378
registered 0.6378  reproduced 0.6378  |diff| 0.0000  tolerance 0.005  -> OK
```

The tolerance of **0.005 macro-F1** covers BLAS and thread-count differences across machines. Data, features, split and seed are fixed, so on the same platform the difference is 0. Reproducing a version with an *older* data version rewrites the training months in the warehouse with that version, until the next `make backfill`.

## Tests and CI

`make test` runs the unit tests in [`tests/`](tests/): the polars and Python cleaning functions give identical output (the training/serving contract), the faults break the batch, rule baselines and the month range.

`make test-integration` runs [`tests/test_pipeline_integration.py`](tests/test_pipeline_integration.py) against a real Postgres: a month run twice leaves identical rows and content hash; `null_narratives` and `wrong_month` faults raise `QualityGateError`; a clean rerun recovers.

CI has two jobs on every push: `lint` (`make setup`, `make lint`, `make test`) and `pipeline`, which starts a Postgres service, pulls the raw data through DVC from the public remote (`make data-public`: *40 files fetched and 39 files added*) and runs `make test-integration` (*2 passed*). So a third party pulls the data and runs the pipeline on every push.

## Maturity scorecard

| Property | Evidence a reviewer checks | Added in |
|---|---|---|
| Repository reproducible from a clean clone | CI is green; `make setup` and `make lint` succeed on a fresh clone | Lab 1 |
| Design document exists and is current | `docs/design.md` follows the template, every section filled or marked as an assumption | Lab 1 |
| Data versioned | `make data-public` (or `make data-seed && make data`) restores `data/raw/ccdb_sample/`; checksum `0bba9005…` above | Lab 2 |
| Pipeline idempotent | same interval twice gives 3000 / 3000 / 1013 rows both times, pasted above | Lab 2 |
| Quality gate present | `FAULT=null_narratives` fails `quality_check`, screenshot above; 2025-12 .. 2026-06 fail for real | Lab 2 |
| Experiments tracked | every run in MLflow experiment `complaint-router` has params, metrics, `data_version` and `git_commit` | Lab 2 |
| Model registered with lineage | `complaint-router` v2 `@champion` tagged with both; `make reproduce` → diff 0.0000 | Lab 2 |
