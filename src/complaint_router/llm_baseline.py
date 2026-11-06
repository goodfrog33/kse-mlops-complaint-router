"""LLM baseline: the Lab 2 task done zero-shot by an LLM, scored on the same test months.

The run lands in the same MLflow experiment as the Lab 2 runs, with the same test metrics
(test_macro_f1, test_accuracy), so the two baselines sit side by side in the tracking UI.
"""

import argparse

import mlflow
import numpy as np
from sklearn.metrics import f1_score

from complaint_router import llm, pipeline, settings
from complaint_router.train import git_state, load_features, scores, split

LABELS = [
    "Credit reporting",
    "Debt collection",
    "Mortgage",
    "Checking or savings",
    "Credit or prepaid card",
    "Money transfer and virtual currency",
    "Student loan",
    "Vehicle loan or lease",
    "Payday title and personal loan",
    "Debt or credit management",
]
UNPARSED = "unparsed"


def parse_label(reply: str) -> str:
    """The product the reply names; anything else counts as a wrong answer."""
    text = reply.strip().strip(".\"'").strip()
    for label in LABELS:
        if text.lower() == label.lower():
            return label
    named = [label for label in LABELS if label.lower() in text.lower()]
    return named[0] if len(named) == 1 else UNPARSED


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="score only the first N test rows")
    ap.add_argument("--run-name", default="llm-zero-shot")
    args = ap.parse_args(argv)

    cfg = llm.load_config()
    c = cfg["classify"]
    template = llm.read_prompt(c["prompt"])
    version = pipeline.data_version()
    commit, dirty = git_state()

    test = split(load_features(version), settings.TEST_MONTHS)
    if args.limit:
        test = test.sample(n=args.limit, random_state=42).reset_index(drop=True)
    prompts = [template.format(text=t[: c["max_input_chars"]]) for t in test["text"].tolist()]

    client = llm.Client(cfg)
    replies = client.chat_many(prompts, c["max_tokens"])
    pred = [parse_label(r.text) for r in replies]

    metrics = {
        **scores(test["label"], pred, "test"),
        "test_unparsed_share": float(np.mean([p == UNPARSED for p in pred])),
        **llm.usage_metrics(replies),
    }
    per_class = f1_score(test["label"], pred, labels=LABELS, average=None, zero_division=0)

    mlflow.set_tracking_uri(settings.MLFLOW_TRACKING_URI)
    mlflow.set_experiment(settings.MLFLOW_EXPERIMENT)
    with mlflow.start_run(run_name=args.run_name) as run:
        mlflow.set_tags(
            {
                "model_type": "llm",
                "data_version": version,
                "git_commit": commit,
                "git_dirty": str(dirty),
                "feature_version": settings.FEATURE_VERSION,
                "test_months": "..".join(settings.TEST_MONTHS),
                "prompt_file": c["prompt"],
                "prompt_hash": llm.file_hash(c["prompt"]),
                "config_hash": llm.file_hash(llm.CONFIG_FILE),
            }
        )
        mlflow.log_params(
            {
                "llm_model": client.model,
                "llm_base_url": cfg["llm"]["base_url"],
                "temperature": client.temperature,
                "max_tokens": c["max_tokens"],
                "max_input_chars": c["max_input_chars"],
                "shots": 0,
                "n_test": len(test),
            }
        )
        mlflow.log_metrics(metrics)
        mlflow.log_artifact(str(settings.PROJECT_ROOT / c["prompt"]))
        mlflow.log_dict({"labels": LABELS, "test_f1": per_class.tolist()}, "per_class_test.json")
        mlflow.log_table(
            {
                "complaint_id": test["complaint_id"].tolist(),
                "label": test["label"].tolist(),
                "reply": [r.text for r in replies],
                "pred": pred,
                "latency_s": [r.latency_s for r in replies],
            },
            "predictions.json",
        )
    print(f"run {run.info.run_id}  prompt {llm.file_hash(c['prompt'])}  n={len(test)}")
    for k in (
        "test_macro_f1",
        "test_accuracy",
        "test_unparsed_share",
        "llm_cost_usd",
        "llm_latency_p50_s",
        "llm_latency_p95_s",
    ):
        print(f"  {k:24s} {metrics[k]:.4f}")
    return {"run_id": run.info.run_id, **metrics}


if __name__ == "__main__":
    main()
