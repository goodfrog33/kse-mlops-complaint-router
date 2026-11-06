"""The LLM component's plumbing: one config file, hashed prompt files, timed and costed calls."""

import hashlib
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

from complaint_router import settings

CONFIG_FILE = settings.PROJECT_ROOT / "configs/llm.yaml"


def load_config(path: Path = CONFIG_FILE) -> dict:
    return yaml.safe_load(path.read_text())


def file_hash(path: str | Path) -> str:
    """sha256 of a file's bytes, first 12 hex digits: the version of a prompt or a config."""
    p = Path(path)
    p = p if p.is_absolute() else settings.PROJECT_ROOT / p
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12]


def read_prompt(path: str) -> str:
    return (settings.PROJECT_ROOT / path).read_text()


@dataclass
class Reply:
    text: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_s: float
    tokens_reasoning: int = 0
    error: str = ""


class Client:
    """OpenAI-compatible chat client. OpenRouter today; a vLLM server only changes base_url."""

    def __init__(self, cfg: dict, model: str | None = None):
        from openai import OpenAI

        llm = cfg["llm"]
        key = os.environ.get(llm["api_key_env"])
        if not key:
            raise SystemExit(f"{llm['api_key_env']} is not set; put it in .env (see .env.example)")
        self.model = model or llm["model"]
        self.temperature = llm["temperature"]
        self.concurrency = llm["concurrency"]
        # Models that reason before answering bill those tokens as completion tokens and can
        # spend the whole max_tokens on them; the config decides, per run, whether they may.
        self.extra = {"usage": {"include": True}}
        if "reasoning" in llm and model is None:
            self.extra["reasoning"] = {"enabled": bool(llm["reasoning"])}
        self._api = OpenAI(base_url=llm["base_url"], api_key=key, timeout=llm["timeout_s"])

    def chat(self, prompt: str, max_tokens: int) -> Reply:
        t0 = time.perf_counter()
        try:
            r = self._api.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.temperature,
                max_tokens=max_tokens,
                # OpenRouter reports the billed cost per call; a vLLM server ignores this.
                extra_body=self.extra,
            )
        except Exception as e:  # one failed call is scored as wrong, not a crashed run
            return Reply("", 0, 0, 0.0, time.perf_counter() - t0, error=type(e).__name__)
        latency = time.perf_counter() - t0
        usage = r.usage
        cost = (usage.model_extra or {}).get("cost") if usage else None
        details = usage.completion_tokens_details if usage else None
        return Reply(
            text=(r.choices[0].message.content or "").strip(),
            tokens_in=usage.prompt_tokens if usage else 0,
            tokens_out=usage.completion_tokens if usage else 0,
            cost_usd=float(cost or 0.0),
            latency_s=latency,
            tokens_reasoning=(details.reasoning_tokens or 0) if details else 0,
        )

    def chat_many(self, prompts: list[str], max_tokens: int) -> list[Reply]:
        with ThreadPoolExecutor(self.concurrency) as pool:
            return list(pool.map(lambda p: self.chat(p, max_tokens), prompts))


def usage_metrics(replies: list[Reply], prefix: str = "llm") -> dict:
    """Tokens, money and latency of a run: what the provider reported, call by call."""
    lat = np.array([r.latency_s for r in replies])
    return {
        f"{prefix}_calls": len(replies),
        f"{prefix}_errors": sum(bool(r.error) for r in replies),
        f"{prefix}_tokens_in": sum(r.tokens_in for r in replies),
        f"{prefix}_tokens_out": sum(r.tokens_out for r in replies),
        f"{prefix}_tokens_reasoning": sum(r.tokens_reasoning for r in replies),
        f"{prefix}_empty_replies": sum(not r.text for r in replies),
        f"{prefix}_cost_usd": sum(r.cost_usd for r in replies),
        f"{prefix}_cost_usd_per_call": sum(r.cost_usd for r in replies) / max(len(replies), 1),
        f"{prefix}_latency_p50_s": float(np.percentile(lat, 50)),
        f"{prefix}_latency_p95_s": float(np.percentile(lat, 95)),
    }
