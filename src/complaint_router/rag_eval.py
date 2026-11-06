"""Evaluate retrieval and answers on the question set, and log one MLflow run.

    uv run --group rag python -m complaint_router.rag_eval
    uv run --group rag python -m complaint_router.rag_eval --set index.chunk_size=400

`--set` changes one value of configs/llm.yaml for this run only; the run logs the effective
configuration and its hash next to the file's hash, so a swept value is never a silent copy.
"""

import argparse
import copy
import hashlib
import json
import random
import re
import time

import mlflow
import numpy as np
import yaml

from complaint_router import llm, rag, settings
from complaint_router.train import git_state

EXPERIMENT = "complaint-router-rag"
K_VALUES = (1, 3, 5, 10)
CITATION = re.compile(r"\[(\d{4}\.\d+)[^\]]*\]")
REFUSAL = "The retrieved passages do not answer this."


def apply_overrides(cfg: dict, pairs: list[str]) -> dict:
    cfg = copy.deepcopy(cfg)
    for pair in pairs:
        key, value = pair.split("=", 1)
        node = cfg
        *path, leaf = key.split(".")
        for p in path:
            node = node[p]
        if leaf not in node:
            raise SystemExit(f"unknown setting {key}")
        node[leaf] = yaml.safe_load(value)
    return cfg


def retrieval_metrics(ranks: list[int | None]) -> dict:
    """ranks: 1-based rank of the first chunk from a relevant section, None if not in top 10."""
    out = {
        f"hit_rate_at_{k}": float(np.mean([r is not None and r <= k for r in ranks]))
        for k in K_VALUES
    }
    out["mrr_at_10"] = float(np.mean([1 / r if r else 0.0 for r in ranks]))
    return out


def context(chunks: list[dict]) -> str:
    return "\n\n".join(f"[{c['doc_id']}] {c['text']}" for c in chunks)


def parse_judge(text: str) -> int:
    match = re.search(r'"score"\s*:\s*([1-5])', text)
    return int(match.group(1)) if match else 1


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", dest="overrides", action="append", default=[], help="key=value")
    ap.add_argument("--retrieval-only", action="store_true", help="skip the LLM and the judge")
    ap.add_argument(
        "--judge-control",
        action="store_true",
        help="also judge every answer against another question's reference",
    )
    ap.add_argument("--run-name", default=None)
    args = ap.parse_args(argv)

    base = llm.load_config()
    cfg = apply_overrides(base, args.overrides)
    ix, k = cfg["index"], cfg["retrieve"]["top_k"]
    eval_path = settings.PROJECT_ROOT / cfg["eval_set"]["path"]
    questions = [json.loads(line) for line in eval_path.read_text().splitlines() if line]

    built = None
    if not rag.index_exists(cfg):
        built = rag.build_index(cfg)
        print(f"built index {built}")

    # Retrieval, one question at a time, so latency is per query (embedding + search).
    hits, ranks, r_lat = [], [], []
    for q in questions:
        t0 = time.perf_counter()
        top = rag.retrieve(cfg, [q["question"]], max(10, k))[0]
        r_lat.append(time.perf_counter() - t0)
        hits.append(top)
        found = [i + 1 for i, c in enumerate(top[:10]) if c["doc_id"] in q["doc_ids"]]
        ranks.append(found[0] if found else None)
    metrics = {
        **retrieval_metrics(ranks),
        "hit_rate_at_top_k": float(np.mean([r is not None and r <= k for r in ranks])),
        "retrieval_latency_p50_s": float(np.percentile(r_lat, 50)),
        "retrieval_latency_p95_s": float(np.percentile(r_lat, 95)),
    }

    answers, scores, control = [], [], []
    if not args.retrieval_only:
        client = llm.Client(cfg)
        a_tmpl = llm.read_prompt(cfg["answer"]["prompt"])
        prompts = [
            a_tmpl.format(context=context(h[:k]), question=q["question"])
            for q, h in zip(questions, hits, strict=True)
        ]
        answers = client.chat_many(prompts, cfg["answer"]["max_tokens"])

        judge = llm.Client(cfg, model=cfg["judge"]["model"])
        j_tmpl = llm.read_prompt(cfg["judge"]["prompt"])
        j_prompts = [
            j_tmpl.format(question=q["question"], reference=q["reference_answer"], answer=a.text)
            for q, a in zip(questions, answers, strict=True)
        ]
        judged = judge.chat_many(j_prompts, cfg["judge"]["max_tokens"])
        # An empty answer is wrong by definition; the judge is not asked to decide that.
        scores = [
            parse_judge(j.text) if a.text else 1 for j, a in zip(judged, answers, strict=True)
        ]

        cited = [set(CITATION.findall(a.text)) for a in answers]
        retrieved = [{c["doc_id"] for c in h[:k]} for h in hits]
        with_cite = [bool(c) for c in cited]
        metrics |= {
            "judge_score_mean": float(np.mean(scores)),
            "judge_pass_rate": float(np.mean([s >= 4 for s in scores])),
            "refusal_rate": float(np.mean([REFUSAL.lower() in a.text.lower() for a in answers])),
            "citation_rate": float(np.mean(with_cite)),
            # Every cited section was among the retrieved ones: no citation from thin air.
            "citation_valid_rate": float(
                np.mean(
                    [c <= r for c, r, w in zip(cited, retrieved, with_cite, strict=True) if w]
                    or [0.0]
                )
            ),
            **llm.usage_metrics(answers, "llm"),
            **llm.usage_metrics(judged, "judge"),
        }
        if args.judge_control:
            # Sanity check of the judge: a right answer to a different question must fail.
            rng = random.Random(0)
            perm = list(range(len(questions)))
            while any(i == p for i, p in enumerate(perm)):
                rng.shuffle(perm)
            c_prompts = [
                j_tmpl.format(
                    question=q["question"], reference=q["reference_answer"], answer=answers[p].text
                )
                for q, p in zip(questions, perm, strict=True)
            ]
            control = [
                parse_judge(j.text) if answers[p].text else 1
                for j, p in zip(judge.chat_many(c_prompts, 200), perm, strict=True)
            ]
            metrics["judge_control_pass_rate"] = float(np.mean([s >= 4 for s in control]))

    commit, dirty = git_state()
    effective = yaml.safe_dump(cfg, sort_keys=True)
    mlflow.set_tracking_uri(settings.MLFLOW_TRACKING_URI)
    mlflow.set_experiment(EXPERIMENT)
    name = args.run_name or ("retrieval" if args.retrieval_only else "rag") + "".join(
        f" {o}" for o in args.overrides
    )
    with mlflow.start_run(run_name=name) as run:
        mlflow.set_tags(
            {
                "model_type": "rag",
                "git_commit": commit,
                "git_dirty": str(dirty),
                "corpus_version": rag.corpus_version(cfg),
                "index_version": rag.index_version(cfg),
                "config_hash": llm.file_hash(llm.CONFIG_FILE),
                "effective_config_hash": hashlib.sha256(effective.encode()).hexdigest()[:12],
                "overrides": ";".join(args.overrides) or "none",
                "eval_set_hash": llm.file_hash(eval_path),
                "answer_prompt_hash": llm.file_hash(cfg["answer"]["prompt"]),
                "judge_prompt_hash": llm.file_hash(cfg["judge"]["prompt"]),
                "eval_set_generator": cfg["eval_set"]["generator_model"],
            }
        )
        mlflow.log_params(
            {
                "chunk_size": ix["chunk_size"],
                "chunk_overlap": ix["chunk_overlap"],
                "embedding_model": ix["embedding_model"],
                "top_k": k,
                "n_questions": len(questions),
                "llm_model": "none" if args.retrieval_only else cfg["llm"]["model"],
                "temperature": cfg["llm"]["temperature"],
                "max_tokens": cfg["answer"]["max_tokens"],
                "judge_model": "none" if args.retrieval_only else cfg["judge"]["model"],
            }
        )
        mlflow.log_metrics(metrics)
        mlflow.log_text(effective, "effective_config.yaml")
        if built:
            mlflow.log_metric("index_embed_seconds", built["embed_seconds"])
            mlflow.log_metric("index_chunks", built["chunks"])
        rows = {
            "id": [q["id"] for q in questions],
            "relevant": [",".join(q["doc_ids"]) for q in questions],
            "rank": [r or 0 for r in ranks],
            "retrieved": [",".join(c["doc_id"] for c in h[:k]) for h in hits],
        }
        if answers:
            rows |= {
                "answer": [a.text for a in answers],
                "judge_score": scores,
                "latency_s": [a.latency_s for a in answers],
            }
        if control:
            rows["control_score"] = control
        mlflow.log_table(rows, "per_question.json")

    print(f"run {run.info.run_id}  {name}  index {rag.index_version(cfg)}")
    for key in (
        "hit_rate_at_1",
        "hit_rate_at_5",
        "hit_rate_at_10",
        "mrr_at_10",
        "judge_score_mean",
        "judge_pass_rate",
        "citation_valid_rate",
        "judge_control_pass_rate",
        "llm_cost_usd",
        "llm_latency_p50_s",
        "llm_latency_p95_s",
    ):
        if key in metrics:
            print(f"  {key:24s} {metrics[key]:.4f}")
    return {"run_id": run.info.run_id, **metrics}


if __name__ == "__main__":
    main()
