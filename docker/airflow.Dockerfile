FROM apache/airflow:3.3.1-python3.12
# Libraries the pipeline code (src/complaint_router) needs inside the workers.
RUN pip install --no-cache-dir "polars==2.0.0" "psycopg[binary]==3.3.6"
