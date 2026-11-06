"""LLM component: chunking, retrieval metrics, label parsing, config overrides, prompt hashes."""

import pytest

from complaint_router import llm
from complaint_router.llm_baseline import LABELS, UNPARSED, parse_label
from complaint_router.rag import Section, chunk_section
from complaint_router.rag_eval import apply_overrides, retrieval_metrics

SECTION = Section(
    doc_id="1006.14",
    part="1006",
    title="§ 1006.14 Harassing, oppressive, or abusive conduct.",
    text="\n".join(f"({i}) " + "word " * 60 for i in range(12)),
)


@pytest.mark.parametrize(("size", "overlap"), [(400, 0), (400, 50), (800, 100), (1600, 200)])
def test_chunks_cover_the_section_and_respect_size(size, overlap):
    chunks = chunk_section(SECTION, size, overlap)
    header = len(SECTION.title) + 1
    assert all(len(c.text) <= header + size + overlap for c in chunks)
    assert all(c.text.startswith(SECTION.title) for c in chunks)
    assert [c.chunk_id for c in chunks] == [f"1006.14:{i}" for i in range(len(chunks))]
    body = " ".join(c.text[header:] for c in chunks)
    for i in range(12):
        assert f"({i})" in body


def test_bigger_chunks_mean_fewer_chunks():
    counts = [len(chunk_section(SECTION, s, 0)) for s in (400, 800, 1600)]
    assert counts == sorted(counts, reverse=True) and counts[0] > counts[-1]


def test_long_paragraph_is_cut_at_a_word():
    sec = Section("1002.1", "1002", "§ 1002.1 Title.", "alpha " * 500)
    chunks = chunk_section(sec, 400, 0)
    assert len(chunks) > 1
    assert all(not c.text.endswith("alph") for c in chunks)


def test_retrieval_metrics():
    m = retrieval_metrics([1, 2, None, 6])
    assert m["hit_rate_at_1"] == 0.25
    assert m["hit_rate_at_5"] == 0.5
    assert m["hit_rate_at_10"] == 0.75
    assert m["mrr_at_10"] == pytest.approx((1 + 1 / 2 + 0 + 1 / 6) / 4)


@pytest.mark.parametrize(
    ("reply", "label"),
    [
        ("Debt collection", "Debt collection"),
        ("debt collection.", "Debt collection"),
        ("The product is: Mortgage", "Mortgage"),
        ("Credit reporting or Debt collection", UNPARSED),
        ("", UNPARSED),
    ],
)
def test_parse_label(reply, label):
    assert parse_label(reply) == label


def test_classify_prompt_lists_every_label():
    prompt = llm.read_prompt(llm.load_config()["classify"]["prompt"])
    assert all(f"\n{label}\n" in prompt for label in LABELS)


def test_overrides_change_one_value_and_reject_unknown_keys():
    cfg = llm.load_config()
    new = apply_overrides(cfg, ["index.chunk_size=400"])
    assert new["index"]["chunk_size"] == 400
    assert cfg["index"]["chunk_size"] != 400 or new is not cfg
    with pytest.raises(SystemExit):
        apply_overrides(cfg, ["index.chunk_sise=400"])


def test_every_prompt_file_formats_and_hashes():
    cfg = llm.load_config()
    for section, fields in (
        ("classify", {"text": "x"}),
        ("answer", {"context": "x", "question": "y"}),
        ("judge", {"question": "x", "reference": "y", "answer": "z"}),
    ):
        path = cfg[section]["prompt"]
        llm.read_prompt(path).format(**fields)
        assert len(llm.file_hash(path)) == 12
