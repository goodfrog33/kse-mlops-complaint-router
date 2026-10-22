FROM ghcr.io/mlflow/mlflow:v3.16.1
# Postgres backend store and S3 (MinIO) artifact store drivers.
RUN pip install --no-cache-dir "psycopg2-binary==2.9.13" "boto3==1.40.70"
