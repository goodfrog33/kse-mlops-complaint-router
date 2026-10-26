"""Train the rule baselines and the TF-IDF model on the time split; track and register.

Reads features.complaint_features for the data version in data/raw/ccdb_sample.dvc, only
for months whose quality checks all passed. Every run logs its parameters, metrics, the
data version (DVC hash) and the git commit. The TF-IDF run is registered as a model
version with the same two tags and, with --alias, gets that alias.

Usage: uv run python -m complaint_router.train [--rebuild-features] [--register] [--alias champion]
"""

import argparse
import json
import subprocess
import sys
from datetime import date

import mlflow
import numpy as np
import pandas as pd
import polars as pl
from mlflow.models import infer_signature
from sklearn.calibration import CalibratedClassifierCV
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from sklearn.svm import LinearSVC

from complaint_router import pipeline, settings
from complaint_router.rules import KeywordRule, MajorityClass

SEED = 42
PARAMS = {
    "word_ngrams": "1-2",
    "word_min_df": 2,
    "word_max_features": 50000,
    "char_ngrams": "3-5",
    "char_max_features": 50000,
    # Chosen on the validation quarter (design.md 7.2): calibrated linear SVM, val macro-F1
    # 0.622 against 0.592 for the earlier logistic regression on the same features.
    "classifier": "linear_svc_calibrated",
    "C": 1.0,
    "class_weight": "balanced",
    "max_iter": 5000,
    "calibration": "sigmoid",
    "calibration_folds": 3,
    "target_precision": 0.95,
}


def months(window: tuple[str, str]) -> list[date]:
    first, last = (date.fromisoformat(m + "-01") for m in window)
    out, cur = [], first
    while cur <= last:
        out.append(cur)
        cur = date(cur.year + cur.month // 12, cur.month % 12 + 1, 1)
    return out


def git_state() -> tuple[str, bool]:
    root = str(settings.PROJECT_ROOT)
    commit = subprocess.check_output(["git", "-C", root, "rev-parse", "HEAD"], text=True).strip()
    dirty = bool(
        subprocess.check_output(
            ["git", "-C", root, "status", "--porcelain", "--untracked-files=no"], text=True
        ).strip()
    )
    return commit, dirty


def load_features(version: str) -> pd.DataFrame:
    wanted = (
        months(settings.TRAIN_MONTHS) + months(settings.VAL_MONTHS) + months(settings.TEST_MONTHS)
    )
    with pipeline.connect() as conn:
        df = pipeline.read_sql(
            conn,
            """
            select f.* from features.complaint_features f
            where f.data_version = %s and f.feature_version = %s
              and f.batch_month = any(%s)
              and f.batch_month not in (
                  select batch_month from ops.quality_results where not passed)
            order by f.complaint_id
            """,
            (version, settings.FEATURE_VERSION, wanted),
        )
    if df.height == 0:
        raise SystemExit(f"no features for data version {version}; run the pipeline first")
    present = set(df["batch_month"].unique().to_list())
    missing = [m.isoformat() for m in wanted if m.isoformat() not in present]
    if missing:
        raise SystemExit(f"months missing or failing quality checks: {missing}")
    return df.with_columns(pl.col("text_chars").cast(pl.Int64)).to_pandas()


def split(df: pd.DataFrame, window: tuple[str, str]) -> pd.DataFrame:
    keys = {m.isoformat() for m in months(window)}
    return df[df["batch_month"].isin(keys)].reset_index(drop=True)


def build_model() -> Pipeline:
    features = ColumnTransformer(
        [
            (
                "word",
                TfidfVectorizer(
                    ngram_range=(1, 2),
                    min_df=PARAMS["word_min_df"],
                    max_features=PARAMS["word_max_features"],
                    sublinear_tf=True,
                ),
                "text",
            ),
            (
                "char",
                TfidfVectorizer(
                    analyzer="char_wb",
                    ngram_range=(3, 5),
                    max_features=PARAMS["char_max_features"],
                    sublinear_tf=True,
                ),
                "text",
            ),
            ("cat", OneHotEncoder(handle_unknown="ignore"), ["submitted_via", "state"]),
        ]
    )
    # Calibration turns SVM margins into probabilities, so the auto-route threshold is a
    # real confidence. Its internal folds are unshuffled, which keeps training deterministic.
    clf = CalibratedClassifierCV(
        LinearSVC(
            C=PARAMS["C"],
            class_weight=PARAMS["class_weight"],
            max_iter=PARAMS["max_iter"],
            random_state=SEED,
        ),
        method=PARAMS["calibration"],
        cv=PARAMS["calibration_folds"],
    )
    return Pipeline([("features", features), ("clf", clf)])


def scores(y_true, y_pred, prefix: str) -> dict:
    return {
        f"{prefix}_macro_f1": f1_score(y_true, y_pred, average="macro", zero_division=0),
        f"{prefix}_accuracy": accuracy_score(y_true, y_pred),
        f"{prefix}_macro_recall": recall_score(y_true, y_pred, average="macro", zero_division=0),
    }


def pick_threshold(proba: np.ndarray, y_pred, y_true, target: float) -> float:
    """Lowest confidence at which auto-routed predictions reach the target precision."""
    conf = proba.max(axis=1)
    correct = np.asarray(y_pred) == np.asarray(y_true)
    for t in np.round(np.arange(0.30, 1.0, 0.01), 2):
        mask = conf >= t
        if mask.sum() >= 50 and correct[mask].mean() >= target:
            return float(t)
    return 1.0


def auto_route(proba: np.ndarray, y_pred, y_true, threshold: float, prefix: str) -> dict:
    mask = proba.max(axis=1) >= threshold
    correct = np.asarray(y_pred) == np.asarray(y_true)
    return {
        f"{prefix}_auto_coverage": float(mask.mean()),
        f"{prefix}_auto_precision": float(correct[mask].mean()) if mask.any() else 0.0,
    }


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--rebuild-features",
        action="store_true",
        help="run the pipeline steps for every needed month before training",
    )
    ap.add_argument("--register", action="store_true")
    ap.add_argument("--alias", default=None)
    ap.add_argument("--run-name", default="tfidf-linearsvc")
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--skip-rules", action="store_true", help="train only the TF-IDF model")
    ap.add_argument("--extra-tag", action="append", default=[], help="key=value")
    args = ap.parse_args(argv)

    version = pipeline.data_version()
    commit, dirty = git_state()
    if dirty and args.register and not args.allow_dirty:
        raise SystemExit("working tree has uncommitted changes; commit before registering")
    if args.rebuild_features:
        for m in (
            months(settings.TRAIN_MONTHS)
            + months(settings.VAL_MONTHS)
            + months(settings.TEST_MONTHS)
        ):
            pipeline.run_month(m)

    df = load_features(version)
    train, val, test = (
        split(df, w) for w in (settings.TRAIN_MONTHS, settings.VAL_MONTHS, settings.TEST_MONTHS)
    )
    tags = {
        "data_version": version,
        "git_commit": commit,
        "git_dirty": str(dirty),
        "feature_version": settings.FEATURE_VERSION,
        "train_months": "..".join(settings.TRAIN_MONTHS),
        "val_months": "..".join(settings.VAL_MONTHS),
        "test_months": "..".join(settings.TEST_MONTHS),
        **dict(t.split("=", 1) for t in args.extra_tag),
    }
    sizes = {"n_train": len(train), "n_val": len(val), "n_test": len(test)}

    mlflow.set_tracking_uri(settings.MLFLOW_TRACKING_URI)
    mlflow.set_experiment(settings.MLFLOW_EXPERIMENT)
    results = {}

    # Rule baselines: no fitting beyond the majority label.
    majority = MajorityClass().fit(train["label"].tolist())
    rules = {"majority-class": majority, "keyword-rule": KeywordRule(default=majority.label_)}
    for name, rule in ({} if args.skip_rules else rules).items():
        with mlflow.start_run(run_name=name) as run:
            mlflow.set_tags({**tags, "model_type": "rule"})
            mlflow.log_params({"rule": name, **sizes})
            metrics = {
                **scores(val["label"], rule.predict(val["text"].tolist()), "val"),
                **scores(test["label"], rule.predict(test["text"].tolist()), "test"),
            }
            mlflow.log_metrics(metrics)
            results[name] = {"run_id": run.info.run_id, **metrics}

    model = build_model()
    x_cols = ["text", "submitted_via", "state"]
    with mlflow.start_run(run_name=args.run_name) as run:
        mlflow.set_tags({**tags, "model_type": "tfidf-linearsvc"})
        mlflow.log_params({**PARAMS, **sizes, "seed": SEED})
        model.fit(train[x_cols], train["label"])
        val_pred, val_proba = model.predict(val[x_cols]), model.predict_proba(val[x_cols])
        test_pred, test_proba = model.predict(test[x_cols]), model.predict_proba(test[x_cols])
        threshold = pick_threshold(val_proba, val_pred, val["label"], PARAMS["target_precision"])
        metrics = {
            **scores(val["label"], val_pred, "val"),
            **scores(test["label"], test_pred, "test"),
            **auto_route(test_proba, test_pred, test["label"], threshold, "test"),
            "auto_threshold": threshold,
        }
        labels = sorted(train["label"].unique())
        per_class = f1_score(test["label"], test_pred, labels=labels, average=None, zero_division=0)
        prec = precision_score(
            test["label"], test_pred, labels=labels, average=None, zero_division=0
        )
        mlflow.log_metrics(metrics)
        mlflow.log_dict(
            {"labels": labels, "test_f1": per_class.tolist(), "test_precision": prec.tolist()},
            "per_class_test.json",
        )
        signature = infer_signature(test[x_cols].head(5), test_pred[:5])
        mlflow.sklearn.log_model(
            model, name="model", signature=signature, input_example=test[x_cols].head(3)
        )
        results[args.run_name] = {"run_id": run.info.run_id, **metrics}

        if args.register:
            mv = mlflow.register_model(f"runs:/{run.info.run_id}/model", settings.REGISTERED_MODEL)
            client = mlflow.MlflowClient()
            for k in ("data_version", "git_commit", "feature_version"):
                client.set_model_version_tag(settings.REGISTERED_MODEL, mv.version, k, tags[k])
            client.set_model_version_tag(
                settings.REGISTERED_MODEL, mv.version, "auto_threshold", str(threshold)
            )
            if args.alias:
                client.set_registered_model_alias(settings.REGISTERED_MODEL, args.alias, mv.version)
            results["registered_version"] = mv.version

    print(
        json.dumps(
            {"data_version": version, "git_commit": commit, **sizes, "results": results},
            indent=1,
            default=float,
        )
    )
    return results


if __name__ == "__main__":
    main(sys.argv[1:])
