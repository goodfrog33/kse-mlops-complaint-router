"""Rolling-origin backtest of the TF-IDF model (design.md 5.4).

For each test month in TEST_MONTHS: train on the 24 months that end just before it, test on
that month alone. Logged as one parent MLflow run with a nested run per fold, all carrying
the data_version and git_commit tags.

Usage: uv run python -m complaint_router.backtest   (or: make backtest)
"""

import json
from datetime import date

import mlflow

from complaint_router import pipeline, settings
from complaint_router.rules import KeywordRule, MajorityClass
from complaint_router.train import (
    PARAMS,
    SEED,
    build_model,
    git_state,
    load_features,
    months,
    scores,
)

WINDOW = 24
X_COLS = ["text", "submitted_via", "state"]


def shift(month: date, n: int) -> date:
    idx = month.year * 12 + month.month - 1 + n
    return date(idx // 12, idx % 12 + 1, 1)


def main() -> dict:
    version = pipeline.data_version()
    commit, dirty = git_state()
    df = load_features(version)
    folds = months(settings.TEST_MONTHS)
    tags = {
        "data_version": version,
        "git_commit": commit,
        "git_dirty": str(dirty),
        "feature_version": settings.FEATURE_VERSION,
        "model_type": "backtest",
    }

    mlflow.set_tracking_uri(settings.MLFLOW_TRACKING_URI)
    mlflow.set_experiment(settings.MLFLOW_EXPERIMENT)
    out = {}
    with mlflow.start_run(run_name="backtest-rolling-origin"):
        mlflow.set_tags(tags)
        mlflow.log_params({**PARAMS, "seed": SEED, "window_months": WINDOW, "folds": len(folds)})
        for test_month in folds:
            train_keys = {
                m.isoformat() for m in (shift(test_month, -k) for k in range(WINDOW, 0, -1))
            }
            train = df[df["batch_month"].isin(train_keys)]
            test = df[df["batch_month"] == test_month.isoformat()]
            name = test_month.strftime("%Y-%m")
            with mlflow.start_run(run_name=f"fold-{name}", nested=True):
                mlflow.set_tags({**tags, "test_month": name})
                model = build_model().fit(train[X_COLS], train["label"])
                rule = KeywordRule(MajorityClass().fit(train["label"].tolist()).label_)
                metrics = {
                    **scores(test["label"], model.predict(test[X_COLS]), "model"),
                    **scores(test["label"], rule.predict(test["text"].tolist()), "rule"),
                }
                mlflow.log_params({"n_train": len(train), "n_test": len(test)})
                mlflow.log_metrics(metrics)
                out[name] = {"n_train": len(train), "n_test": len(test), **metrics}
                print(name, json.dumps({k: round(v, 4) for k, v in metrics.items()}), flush=True)
        for key in ("model_macro_f1", "rule_macro_f1", "model_accuracy"):
            values = [out[m.strftime("%Y-%m")][key] for m in folds]
            mean = sum(values) / len(values)
            std = (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5
            mlflow.log_metrics({f"{key}_mean": mean, f"{key}_std": std})
            out[f"{key}_mean"], out[f"{key}_std"] = mean, std
    print(json.dumps(out, indent=1, default=float))
    return out


if __name__ == "__main__":
    main()
