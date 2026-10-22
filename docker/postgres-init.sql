-- One Postgres instance, three databases: Airflow metadata, MLflow backend, warehouse.
create user airflow with password 'airflow';
create database airflow owner airflow;
create user mlflow with password 'mlflow';
create database mlflow owner mlflow;
create user complaints with password 'complaints';
create database warehouse owner complaints;
