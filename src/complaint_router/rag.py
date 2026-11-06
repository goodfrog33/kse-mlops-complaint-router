"""Retrieval over the regulation corpus: eCFR XML -> sections -> chunks -> pgvector.

`python -m complaint_router.rag fetch` downloads the corpus snapshot named in configs/llm.yaml.
`python -m complaint_router.rag index` rebuilds the index from it and prints the counts.
Every setting comes from configs/llm.yaml; nothing here has its own copy of a constant.
"""

import argparse
import hashlib
import json
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from complaint_router import llm, pipeline, settings

ECFR_URL = "https://www.ecfr.gov/api/versioner/v1/full/{date}/title-12.xml?part={part}"
SPACES = re.compile(r"\s+")


@dataclass
class Section:
    doc_id: str  # "1006.34"
    part: str
    title: str  # "§ 1006.34 Notice for validation of debts."
    text: str


@dataclass
class Chunk:
    chunk_id: str  # "1006.34:2"
    doc_id: str
    text: str


# --- corpus --------------------------------------------------------------------------------


def corpus_dir(cfg: dict) -> Path:
    return settings.PROJECT_ROOT / cfg["corpus"]["dir"]


def fetch(cfg: dict) -> list[Path]:
    """Download one XML file per regulation part, for the snapshot date in the config."""
    out = corpus_dir(cfg)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for part in cfg["corpus"]["parts"]:
        url = ECFR_URL.format(date=cfg["corpus"]["date"], part=part)
        req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
        with urllib.request.urlopen(req, timeout=120) as r:
            body = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                import gzip

                body = gzip.decompress(body)
        path = out / f"part-{part}.xml"
        path.write_bytes(body)
        paths.append(path)
        print(f"{path.relative_to(settings.PROJECT_ROOT)}  {len(body):>9,} bytes")
    return paths


def corpus_version(cfg: dict) -> str:
    """The DVC hash of the corpus directory, like data_version for the complaints."""
    dvc_file = corpus_dir(cfg).with_suffix(".dvc")
    match = re.search(r"md5:\s*([0-9a-f]+)\.dir", dvc_file.read_text())
    if not match:
        raise RuntimeError(f"no directory md5 in {dvc_file}")
    return match.group(1)


def _text(el: ET.Element) -> str:
    return SPACES.sub(" ", "".join(el.itertext())).strip()


def load_sections(cfg: dict) -> list[Section]:
    """Regulation sections with text; appendices and [Reserved] sections are left out."""
    sections = []
    for part in cfg["corpus"]["parts"]:
        root = ET.parse(corpus_dir(cfg) / f"part-{part}.xml").getroot()
        for div in root.iter("DIV8"):
            if div.get("TYPE") != "SECTION":
                continue
            head = div.find("HEAD")
            title = _text(head) if head is not None else div.get("N")
            paras = [_text(p) for p in div if p.tag not in ("HEAD", "CITA", "SECAUTH")]
            body = "\n".join(p for p in paras if p)
            if len(body) < 40:  # "[Reserved]"
                continue
            sections.append(Section(div.get("N"), part, title, body))
    return sections


# --- chunking ------------------------------------------------------------------------------


def chunk_section(sec: Section, size: int, overlap: int) -> list[Chunk]:
    """Greedy packing of paragraphs up to `size` characters; the last `overlap` characters of
    a chunk start the next one. Every chunk carries the section header, so a passage is
    citable on its own."""
    paras = []
    for p in sec.text.split("\n"):
        while len(p) > size:  # a paragraph longer than a chunk is cut at a word boundary
            cut = p.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            paras.append(p[:cut])
            p = p[cut:].lstrip()
        paras.append(p)

    bodies, cur = [], ""
    for p in paras:
        if cur and len(cur) + 1 + len(p) > size:
            bodies.append(cur)
            tail = cur[-overlap:] if overlap else ""
            cur = tail[tail.find(" ") + 1 :] if " " in tail else tail
        cur = f"{cur}\n{p}" if cur else p
    if cur:
        bodies.append(cur)
    return [
        Chunk(f"{sec.doc_id}:{i}", sec.doc_id, f"{sec.title}\n{b.strip()}")
        for i, b in enumerate(bodies)
    ]


# --- index ---------------------------------------------------------------------------------


def index_version(cfg: dict) -> str:
    """Everything that changes the vectors: corpus version, chunking, embedding model."""
    ix = cfg["index"]
    key = json.dumps(
        [corpus_version(cfg), ix["chunk_size"], ix["chunk_overlap"], ix["embedding_model"]]
    )
    return hashlib.sha256(key.encode()).hexdigest()[:12]


@cache
def embedder(model: str):
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model, device="cpu")


def embed(model: str, texts: list[str]) -> list[list[float]]:
    vecs = embedder(model).encode(
        texts, batch_size=64, normalize_embeddings=True, show_progress_bar=False
    )
    return vecs.tolist()


def _vec(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def ensure_schema(conn) -> None:
    conn.execute("select pg_advisory_xact_lock(4243)")
    conn.execute("create extension if not exists vector")
    conn.execute("create schema if not exists rag")
    conn.execute(
        """
        create table if not exists rag.chunks (
            index_version text not null,
            chunk_id text not null,
            doc_id text not null,
            text text not null,
            embedding vector not null,
            primary key (index_version, chunk_id))
        """
    )
    conn.execute(
        """
        create table if not exists rag.index_builds (
            index_version text primary key,
            corpus_version text not null,
            chunk_size int not null,
            chunk_overlap int not null,
            embedding_model text not null,
            documents int not null,
            chunks int not null,
            built_at timestamptz not null default now())
        """
    )


def build_index(cfg: dict) -> dict:
    """Rebuild the index for the configured settings. Idempotent: the version is replaced."""
    ix = cfg["index"]
    version = index_version(cfg)
    sections = load_sections(cfg)
    chunks = [c for s in sections for c in chunk_section(s, ix["chunk_size"], ix["chunk_overlap"])]
    t0 = time.perf_counter()
    vectors = embed(ix["embedding_model"], [c.text for c in chunks])
    embed_s = time.perf_counter() - t0
    with pipeline.connect() as conn:
        ensure_schema(conn)
        conn.execute("delete from rag.chunks where index_version = %s", (version,))
        with conn.cursor().copy(
            "copy rag.chunks (index_version, chunk_id, doc_id, text, embedding) from stdin"
        ) as cp:
            for c, v in zip(chunks, vectors, strict=True):
                cp.write_row((version, c.chunk_id, c.doc_id, c.text, _vec(v)))
        conn.execute(
            """
            insert into rag.index_builds (index_version, corpus_version, chunk_size,
                chunk_overlap, embedding_model, documents, chunks)
            values (%s, %s, %s, %s, %s, %s, %s)
            on conflict (index_version) do update set documents = excluded.documents,
                chunks = excluded.chunks, built_at = now()
            """,
            (
                version,
                corpus_version(cfg),
                ix["chunk_size"],
                ix["chunk_overlap"],
                ix["embedding_model"],
                len(sections),
                len(chunks),
            ),
        )
    return {
        "index_version": version,
        "documents": len(sections),
        "chunks": len(chunks),
        "embed_seconds": round(embed_s, 1),
    }


def index_exists(cfg: dict) -> bool:
    with pipeline.connect() as conn:
        ensure_schema(conn)
        row = conn.execute(
            "select 1 from rag.index_builds where index_version = %s", (index_version(cfg),)
        ).fetchone()
    return row is not None


def retrieve(cfg: dict, questions: list[str], k: int) -> list[list[dict]]:
    """Top-k chunks per question by cosine distance, exact search (no ANN approximation)."""
    ix = cfg["index"]
    version = index_version(cfg)
    qvecs = embed(ix["embedding_model"], [ix["query_prefix"] + q for q in questions])
    out = []
    with pipeline.connect() as conn:
        for v in qvecs:
            rows = conn.execute(
                """
                select chunk_id, doc_id, text, embedding <=> %s::vector as distance
                from rag.chunks where index_version = %s
                order by distance, chunk_id limit %s
                """,
                (_vec(v), version, k),
            ).fetchall()
            out.append(
                [
                    dict(zip(("chunk_id", "doc_id", "text", "distance"), r, strict=True))
                    for r in rows
                ]
            )
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["fetch", "index"])
    args = ap.parse_args(argv)
    cfg = llm.load_config()
    if args.command == "fetch":
        fetch(cfg)
        return
    stats = build_index(cfg)
    ix = cfg["index"]
    print(
        f"corpus {corpus_version(cfg)}  chunk_size={ix['chunk_size']} "
        f"overlap={ix['chunk_overlap']}  model={ix['embedding_model']}"
    )
    print(
        f"documents: {stats['documents']}  chunks: {stats['chunks']}  "
        f"index_version: {stats['index_version']}  embedding: {stats['embed_seconds']} s"
    )


if __name__ == "__main__":
    main()
