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
make up           # Postgres, MinIO, MLflow and Airflow in Docker; returns when all are healthy
make data-seed    # fill your empty MinIO from the public mirror (once)
make data         # dvc pull the raw data from MinIO
make backfill     # one Airflow run per month, 2023-04 .. 2025-09 (about 3 minutes)
make train        # rule baselines + the model, logged and registered as @champion (~10 min)
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

The stack (Postgres, MinIO, MLflow, Airflow) plus training wants about **8 GB of free memory**; on a Mac under heavy swap, Docker Desktop's VM can be killed mid-run, which shows up as connection resets from MinIO. Run `train`, `backtest` and `reproduce` one at a time. Host ports avoid 5000, 5432 and 9000 on purpose. `.env.example` sets `MLFLOW_ENABLE_PROXY_MULTIPART_DOWNLOAD=false`, so MLflow downloads go through the server and never to `minio:9000`. The Apple Silicon `cryptography` workaround was not needed (Docker Desktop 29.3).

**Clean-clone rehearsal (2026-10-23, on the earlier 3,000-per-month data version).** Fresh clone, empty Docker volumes, the seven commands above in order: all succeeded, `train` reproduced the published numbers exactly and `reproduce` gave a difference of 0.0000. It found two bugs that a long-lived stack hid, both fixed: a schema-creation race when six first runs hit an empty database, and `make up` returning before Airflow and MLflow were ready.

## Data (step 2)

The full CFPB export is 5.6 GB, so the pipeline works on a sample, drawn by [`scripts/make_sample.py`](scripts/make_sample.py) from the July 2026 snapshot described in design.md §5.2:

- months **2023-04 .. 2026-06**, one CSV per month, 8,000 complaints each (39 files, 312,000 rows, 168 MB);
- per month, the 8,000 complaints with the smallest Knuth hash of `complaint_id`: a uniform sample that is byte-identical on every rerun (and contains the earlier 3,000-per-month sample);
- all complaints, with and without text, so each month keeps its real share of text.

| | |
|---|---|
| Tracked path | `data/raw/ccdb_sample/` → [`data/raw/ccdb_sample.dvc`](data/raw/ccdb_sample.dvc) |
| Data version (DVC hash) | **`6b6bda8b4c8ece9715a5c9127a47dc8e`** |
| Default remote | `minio`: S3 at `s3://dvc/complaint-router`, endpoint `http://localhost:9100` (`.dvc/config`; keys only in the git-ignored `.dvc/config.local`, written by `make dvc-creds`) |
| Public remote | `public`: read-only HTTP, the [`dvc-store`](https://github.com/goodfrog33/kse-mlops-complaint-router/tree/dvc-store) branch of this repository (also keeps the earlier data versions) |
| Pull | `make data` (MinIO, after `make data-seed`) or `make data-public` (no credentials, no Docker) |
| Checksum of what arrives | `cat data/raw/ccdb_sample/*.csv \| md5` → **`872e979f783b101ff87505a5a74b5838`** (`md5sum` on Linux) |

## Pipeline (step 3)

[`dags/ccdb_pipeline.py`](dags/ccdb_pipeline.py), Airflow 3.3.1: `extract → validate → transform → load → build_features → quality_check`. The code lives in [`src/complaint_router/pipeline.py`](src/complaint_router/pipeline.py).

**The batch is the data interval, never the clock.** Airflow 3 no longer creates intervals for `@monthly`, so the DAG declares them: `schedule=CronDataIntervalTimetable("@monthly", timezone="UTC")`. Every task takes its month from `data_interval_start`. Backfill run `2024-06-01` processes June 2024 whenever it executes.

**Concurrency.** Up to 6 months run at once (`max_active_runs=6`): every write is scoped to its own month, so runs do not touch each other's rows. All 39 months take 174 s. Schema creation runs under a Postgres advisory lock, so concurrent first runs on an empty database do not race.

**Idempotent loads.** Every layer is written with `delete from <table> where batch_month = %s` followed by `COPY` of the month, in one transaction (`replace_month`). The same month run twice:

```text
make run-month MONTH=2024-06-01            make run-month MONTH=2024-06-01   (again)
raw.complaints              8000            raw.complaints              8000
dwh.complaints              8000            dwh.complaints              8000
features.complaint_features 2683            features.complaint_features 2683
checks passed               5/5             checks passed               5/5
```

The content is identical too: `md5(string_agg(row, '|' order by complaint_id))` over the month's feature rows is `30048bcce381b8f2db30a53fbdfa36c2` after both runs.

## Quality gate (step 3)

Two gates: `validate` (columns, every date inside the interval, numeric and unique `complaint_id`) and `quality_check` (≥ 1,000 rows, text share ≥ 0.13, zero unmapped products, no id reused from the previous month, feature rows > 0). Results land in `ops.quality_results`; training skips any month with a failed check.

**Broken on purpose.** `make run-month MONTH=2024-06-01 FAULT=null_narratives` blanks every narrative in the extracted batch (other faults: `unknown_product`, `wrong_month`). The run turns red at `quality_check`:

```text
QualityGateError: quality_check 2024-06 failed: text_share=0 (want >= 0.13); feature_rows=0 (want > 0)
```

![failed quality_check task](docs/img/quality_gate_failed.png)

A clean rerun of the same month succeeds (Airflow keeps a run's old conf on reprocessing, so `make run-month` clears it first; see [`scripts/airflow_ctl.py`](scripts/airflow_ctl.py)):

![successful run graph](docs/img/run_graph_success.png)

**Broken for real.** The backfill over all 39 months fails exactly the months where CFPB stopped publishing text: text share 0.110 (2025-12), 0.039, 0.027, 0.032, 0.035, 0.010, 0.002 (2026-06); 2026-06 also has one complaint with an unmapped product (`Other`). 32 months pass.

![DAG overview](docs/img/dag_overview.png)

## Features (step 4)

`build_features` fills `features.complaint_features`: one row per complaint with text and a mapped label, plus `feature_version = v1` and `data_version`. Where each feature comes from at serving time is in design.md §5.3; `company` was dropped from the model because its training and serving sources differ.

**Feast (optional part).** [`feature_repo/`](feature_repo/) defines the entity `company` and the feature view `company_monthly_stats` (complaints, credit-reporting share and text share of the company's *previous* month), built from `dwh.complaints`. A month's statistics only exist once the month is over, so each row is stamped with the first day of the next month. `make feast-demo` applies the repo, asks for complaints dated 2024-07-15, between the 2024-07-01 and 2024-08-01 rows, and fails unless every entity gets the earlier row:

```text
entity rows: complaint on 2024-07-15; feature rows exist at 2024-07-01 and 2024-08-01
  ACIMA CREDIT, LLC                      got    5  earlier row    5  later row    1  -> earlier
  ALLY FINANCIAL INC.                    got    7  earlier row    7  later row   13  -> earlier
  AMERICAN EXPRESS COMPANY               got   22  earlier row   22  later row   23  -> earlier
online store, ACIMA CREDIT, LLC: complaints_last_month = 1 (latest row before 2024-08-02)
point-in-time join: OK
```

The same definitions are materialized into the SQLite online store and read back with `get_online_features`, which is the path Lab 4 serving can use.

## Baseline vs model (step 5)

Time split (design.md §5.4): train 2023-04 .. 2025-03 (63,207 texts), validation 2025-04 .. 06 (6,400), test 2025-07 .. 09 (5,212). Never random: complaints about one event share wording and cluster in time.

| Model | Test macro-F1 | Test accuracy | Notes |
|---|---:|---:|---|
| Majority class (always *Credit reporting*) | 0.088 | 0.786 | the floor; accuracy looks fine because 79% is one class |
| Keyword rule (first match wins) | 0.380 | 0.714 | design.md §5.3 |
| **TF-IDF + soft vote (calibrated LinearSVC 0.7, logistic regression 0.3)** | **0.633** | **0.895** | at threshold 0.68 (set on validation): 86% auto-routed at 0.941 precision |

The model beats the rule on every metric. It misses the 0.80 macro-F1 target of design.md §3, mostly because *Debt or credit management* (10 test examples) gets F1 0.00 and *Payday, title and personal loan* 0.46. Per-class scores are in design.md §7.2.

**How the model was chosen.** On the validation quarter only, same features:

| Classifier | Val macro-F1 | Test macro-F1 (after the choice) | Test accuracy |
|---|---:|---:|---:|
| Logistic regression (Lab 2's first model) | 0.592 | 0.631 | 0.856 |
| Calibrated LinearSVC | 0.622 | 0.624 | 0.894 |
| **Soft vote, 0.7 SVC + 0.3 LR** | **0.634** | **0.633** | **0.895** |

On test the three are within noise on macro-F1; the vote adds 3.9 points of accuracy and 9 points of auto-routing coverage over logistic regression. The sample was raised from 3,000 to 8,000 complaints a month first, because with 3,000 the test quarter held 5–30 examples of most small classes and macro-F1 moved by noise.

**Rolling-origin backtest** (`make backtest`; each fold trains on the 24 months right before its test month):

| Test month | Model macro-F1 | Rule macro-F1 |
|---|---:|---:|
| 2025-07 | 0.615 | 0.386 |
| 2025-08 | 0.714 | 0.381 |
| 2025-09 | 0.618 | 0.375 |
| mean ± std | **0.649 ± 0.046** | 0.381 ± 0.004 |

The model wins every fold; the month-to-month spread (0.62 to 0.71) is the honest error bar on any single test number.

**Random split, for comparison** (`scripts/random_split_check.py`, run tagged `split=random`): **0.688** macro-F1, 0.055 above the time split's 0.633 on the same model and test size. That is the optimism a random split brings: it trains on months that come after the ones it tests. (On the earlier 3,000-per-month sample this did not show, 0.591; the larger sample makes it visible.)

## Tracking and registry (step 6)

MLflow 3.16.1 with a Postgres backend store (`mlflow` database) and a MinIO artifact store (`s3://mlflow`, proxied by the server), started by `make up`. Every run, rules, backtest folds and the random-split check included, logs its parameters, metrics and the tags `data_version` and `git_commit`.

Model `complaint-router`, **version 4, alias `champion`**: `data_version=6b6bda8b4c8ece9715a5c9127a47dc8e`, `git_commit=5185a3a51b8dde5012198d70dae425baeb0672ec`, `feature_version=v1`, `auto_threshold=0.68`. Earlier versions stay registered and reproducible: v1 and v2 (logistic regression, 3,000-per-month data), v3 (calibrated LinearSVC, current data).

![MLflow model version 4](docs/img/mlflow_model_champion.png)

**Reproduce:** `make reproduce` reads the two tags from the registry, checks out the commit in a git worktree under `.repro/`, `dvc pull`s and verifies the data version, rebuilds the features with that commit's pipeline code and retrains:

```text
version 4: commit 5185a3a51b, data 6b6bda8b4c8ece9715a5c9127a47dc8e, test_macro_f1 0.6325
registered 0.6325  reproduced 0.6325  |diff| 0.0000  tolerance 0.005  -> OK
```

The tolerance of **0.005 macro-F1** covers BLAS and thread-count differences across machines. Data, features, split and seeds are fixed (the calibration folds are unshuffled), so on the same platform the difference is 0. Reproducing a version with an *older* data version rewrites the training months in the warehouse with that version, until the next `make backfill`. The model uses sklearn calibration classes, which MLflow's skops serializer only saves when they are listed as trusted (`SKOPS_TRUSTED_TYPES` in `train.py`).

## Tests and CI

`make test` runs the unit tests in [`tests/`](tests/): the polars and Python cleaning functions give identical output (the training/serving contract), the faults break the batch, rule baselines and the month range.

`make test-integration` runs [`tests/test_pipeline_integration.py`](tests/test_pipeline_integration.py) against a real Postgres: six concurrent schema creations do not race; a month run twice leaves identical rows and content hash; `null_narratives` and `wrong_month` faults raise `QualityGateError`; a clean rerun recovers.

CI has two jobs on every push: `lint` (`make setup`, `make lint`, `make test`) and `pipeline`, which starts an empty Postgres service, pulls the raw data through DVC from the public remote (`make data-public`) and runs `make test-integration`. So a third party pulls the data and runs the pipeline on every push.

## Maturity scorecard

| Property | Evidence a reviewer checks | Added in |
|---|---|---|
| Repository reproducible from a clean clone | CI is green; `make setup` and `make lint` succeed on a fresh clone | Lab 1 |
| Design document exists and is current | `docs/design.md` follows the template, every section filled or marked as an assumption | Lab 1 |
| Data versioned | `make data-public` (or `make data-seed && make data`) restores `data/raw/ccdb_sample/`; checksum `872e979f…` above | Lab 2 |
| Pipeline idempotent | same interval twice gives 8000 / 8000 / 2683 rows and the same content hash, pasted above | Lab 2 |
| Quality gate present | `FAULT=null_narratives` fails `quality_check`, screenshot above; 2025-12 .. 2026-06 fail for real | Lab 2 |
| Experiments tracked | every run in MLflow experiment `complaint-router` has params, metrics, `data_version` and `git_commit` | Lab 2 |
| Model registered with lineage | `complaint-router` v4 `@champion` tagged with both; `make reproduce` → diff 0.0000 | Lab 2 |
