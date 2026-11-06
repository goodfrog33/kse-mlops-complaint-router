# complaint-router. Every target runs from a clean clone; see README for the order.
-include .env
export

MONTHS_FROM ?= 2023-04-01
MONTHS_TO   ?= 2025-09-01
AF = docker compose exec -T airflow airflow
CTL = docker compose exec -T airflow python /opt/project/scripts/airflow_ctl.py

setup:
	uv sync --locked
	uv run pre-commit install

lint:
	uv run ruff check .

test:
	uv run pytest -q

test-integration:      ## pipeline end to end on Postgres (local stack or CI service)
	uv run pytest -q -m integration

env:
	test -f .env || cp .env.example .env

# --- stack -------------------------------------------------------------------
up: env
	docker compose up -d --build --wait postgres minio mlflow airflow
	docker compose run --rm minio-init >/dev/null

down:
	docker compose down

# --- data (DVC) --------------------------------------------------------------
dvc-creds: env
	uv run dvc remote modify --local minio access_key_id $(AWS_ACCESS_KEY_ID)
	uv run dvc remote modify --local minio secret_access_key $(AWS_SECRET_ACCESS_KEY)

data: dvc-creds        ## pull the raw sample from the MinIO remote
	uv run dvc pull data/raw/ccdb_sample.dvc

data-public:           ## pull from the public read-only mirror, no credentials needed
	uv run dvc pull -r public data/raw/ccdb_sample.dvc

data-seed: dvc-creds   ## fill a fresh MinIO from the public mirror, so `make data` works
	uv run dvc pull -r public data/raw/ccdb_sample.dvc
	uv run dvc push -r minio data/raw/ccdb_sample.dvc

# --- pipeline ----------------------------------------------------------------
backfill:              ## one DAG run per month, MONTHS_FROM..MONTHS_TO (first days of months)
	$(AF) dags unpause ccdb_pipeline >/dev/null
	$(AF) backfill create --dag-id ccdb_pipeline --from-date $(MONTHS_FROM) --to-date $(MONTHS_TO) \
		--max-active-runs 6 --reprocess-behavior completed >/dev/null
	@$(MAKE) -s wait

run-month:             ## rerun one month: make run-month MONTH=2024-06-01 [FAULT=null_narratives]
	$(AF) dags unpause ccdb_pipeline >/dev/null
	$(if $(FAULT),,$(CTL) reset-conf $(MONTH) >/dev/null)
	$(AF) backfill create --dag-id ccdb_pipeline --from-date $(MONTH) --to-date $(MONTH) \
		--reprocess-behavior completed $(if $(FAULT),--dag-run-conf '{"fault": "$(FAULT)"}') >/dev/null
	@$(MAKE) -s wait

wait:                  ## block until the newest backfill completes, then print each run's state
	@$(CTL) wait-backfill

counts:                ## row counts per layer for MONTH
	docker compose exec -T postgres psql -U complaints -d warehouse -c "select 'raw' as layer, count(*) from raw.complaints where batch_month='$(MONTH)' union all select 'dwh', count(*) from dwh.complaints where batch_month='$(MONTH)' union all select 'features', count(*) from features.complaint_features where batch_month='$(MONTH)'"

feast-demo:            ## optional: Feast point-in-time join + online read (needs the backfill)
	cd feature_repo && rm -rf data/registry.db data/online.db
	uv run --group feast python scripts/feast_demo.py

# --- model -------------------------------------------------------------------
train:                 ## rules + TF-IDF model, registered with alias champion
	uv run python -m complaint_router.train --register --alias champion

backtest:              ## rolling-origin backtest, 3 monthly folds, logged to MLflow
	uv run python -m complaint_router.backtest

reproduce:             ## retrain the champion from its commit and data version
	uv run python scripts/reproduce.py --alias champion

# --- LLM component (Lab 3) ---------------------------------------------------
RAG = uv run --group rag python -m complaint_router

corpus:                ## pull the regulation corpus from the MinIO remote
	uv run dvc pull data/corpus/ecfr.dvc

corpus-public:         ## pull the regulation corpus from the public mirror, no credentials
	uv run dvc pull -r public data/corpus/ecfr.dvc

corpus-fetch:          ## re-download the eCFR snapshot named in configs/llm.yaml, then dvc add
	uv run python -m complaint_router.rag fetch

rag-index:             ## rebuild the pgvector index from the corpus; prints the document count
	$(RAG).rag index

llm-baseline:          ## the Lab 2 task done zero-shot by the LLM, logged next to the Lab 2 runs
	uv run python -m complaint_router.llm_baseline

rag-eval:              ## retrieval + answers + judge on the question set: one MLflow run
	$(RAG).rag_eval --judge-control $(if $(SET),--set $(SET))

rag-sweep:             ## one parameter, three values: make rag-sweep PARAM=index.chunk_size VALUES="400 800 1600"
	for v in $(VALUES); do $(RAG).rag_eval --judge-control --set $(PARAM)=$$v || exit 1; done

.PHONY: setup lint test test-integration env up down dvc-creds data data-public data-seed backfill run-month wait counts feast-demo train backtest reproduce
.PHONY: corpus corpus-public corpus-fetch rag-index llm-baseline rag-eval rag-sweep
