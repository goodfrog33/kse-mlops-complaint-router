"""Configuration read from the environment, with defaults for the local Docker stack."""

import os
from pathlib import Path

PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", Path(__file__).resolve().parents[2]))
RAW_DIR = PROJECT_ROOT / "data/raw/ccdb_sample"
RAW_DVC_FILE = PROJECT_ROOT / "data/raw/ccdb_sample.dvc"
TAXONOMY_FILE = PROJECT_ROOT / "configs/taxonomy_map.csv"

WAREHOUSE_DSN = os.environ.get(
    "WAREHOUSE_DSN", "postgresql://complaints:complaints@localhost:5442/warehouse"
)
MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5050")
MLFLOW_EXPERIMENT = "complaint-router"
REGISTERED_MODEL = "complaint-router"

FEATURE_VERSION = "v1"

# Time split (design.md 5.4). Months are inclusive, 'YYYY-MM'.
TRAIN_MONTHS = ("2023-04", "2025-03")
VAL_MONTHS = ("2025-04", "2025-06")
TEST_MONTHS = ("2025-07", "2025-09")

# Quality gate thresholds (design.md 6.6).
MIN_ROWS = 1000
# Half of the 2025-07 reference share of complaints with text (26.5%).
MIN_TEXT_SHARE = 0.13
