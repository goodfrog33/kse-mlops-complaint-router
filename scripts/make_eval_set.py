"""Draft the retrieval evaluation set: one question per sampled regulation section.

The questions are written by an LLM from a different family than the answering model and the
judge (configs/llm.yaml, eval_set.generator_model), then read and edited by hand: the file in
the repository is the reviewed version, and `reviewed` records what was changed. Rerunning this
script overwrites data/eval/rag_questions.draft.jsonl only, never the reviewed set.

    uv run python scripts/make_eval_set.py --n 40
"""

import argparse
import json
import random
import re

from complaint_router import llm, rag, settings

# Sections per part: roughly proportional to the corpus, at least six for every regulation.
QUOTA = {"1026": 10, "1022": 6, "1002": 6, "1024": 6, "1005": 6, "1006": 6}
MAX_SECTION_CHARS = 6000


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    cfg = llm.load_config()
    ev = cfg["eval_set"]
    template = llm.read_prompt(ev["generator_prompt"])
    rng = random.Random(args.seed)
    sections = [s for s in rag.load_sections(cfg) if len(s.text) >= 1500]
    picked = []
    for part, n in QUOTA.items():
        pool = sorted((s for s in sections if s.part == part), key=lambda s: s.doc_id)
        picked += rng.sample(pool, n)

    client = llm.Client(cfg, model=ev["generator_model"])
    prompts = [template.format(section=f"{s.title}\n{s.text[:MAX_SECTION_CHARS]}") for s in picked]
    replies = client.chat_many(prompts, max_tokens=400)

    out = settings.PROJECT_ROOT / "data/eval/rag_questions.draft.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        for i, (s, r) in enumerate(zip(picked, replies, strict=True)):
            body = re.sub(r"^```(json)?|```$", "", r.text.strip()).strip()
            qa = json.loads(body)
            row = {
                "id": f"q{i + 1:02d}",
                "doc_ids": [s.doc_id],
                "question": qa["question"],
                "reference_answer": qa["reference_answer"],
                "generator": ev["generator_model"],
                "generator_prompt_hash": llm.file_hash(ev["generator_prompt"]),
                "reviewed": "",
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"{len(picked)} questions -> {out.relative_to(settings.PROJECT_ROOT)}")
    print(llm.usage_metrics(replies, "generator"))


if __name__ == "__main__":
    main()
