"""How much a random split would flatter the model (design.md 5.4, Lab 2 question 14).

Same texts, same model, same test size as the time split; only the split changes: a random
shuffle of all 30 months instead of the last three months held out. Logged to MLflow with
tag split=random so it is never mistaken for a real evaluation.

Usage: uv run python scripts/random_split_check.py
"""

import json

import mlflow
from sklearn.model_selection import train_test_split

from complaint_router import pipeline, settings
from complaint_router.train import SEED, build_model, git_state, load_features, scores

X_COLS = ["text", "submitted_via", "state"]


def main() -> None:
    version = pipeline.data_version()
    commit, dirty = git_state()
    df = load_features(version)
    n_test = int((df["batch_month"] >= "2025-07-01").sum())
    train, test = train_test_split(df, test_size=n_test, random_state=SEED, shuffle=True)

    mlflow.set_tracking_uri(settings.MLFLOW_TRACKING_URI)
    mlflow.set_experiment(settings.MLFLOW_EXPERIMENT)
    with mlflow.start_run(run_name="random-split-leaky"):
        mlflow.set_tags(
            {
                "data_version": version,
                "git_commit": commit,
                "git_dirty": str(dirty),
                "split": "random",
                "purpose": "leakage illustration, not an evaluation",
            }
        )
        model = build_model().fit(train[X_COLS], train["label"])
        metrics = scores(test["label"], model.predict(test[X_COLS]), "random_test")
        mlflow.log_params({"n_train": len(train), "n_test": len(test), "seed": SEED})
        mlflow.log_metrics(metrics)
    print(json.dumps({"n_train": len(train), "n_test": len(test), **metrics}, indent=1))


if __name__ == "__main__":
    main()
