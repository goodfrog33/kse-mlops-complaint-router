"""Reproduce a registered model version from its two lineage tags.

1. Read the version's tags from the MLflow registry: git_commit and data_version.
2. Check out that commit in a git worktree under .repro/ (the current checkout is untouched).
3. dvc pull the raw data in the worktree and check its hash equals data_version.
4. Rebuild the features with the pipeline code of that commit and retrain.
5. Compare test macro-F1 with the registered run; exit 1 if it differs by more than TOLERANCE.

Usage: uv run python scripts/reproduce.py [--alias champion | --version N]
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import mlflow

ROOT = Path(__file__).resolve().parents[1]
MODEL = "complaint-router"
TOLERANCE = 0.005


def sh(cmd: list[str], cwd: Path, env: dict | None = None) -> str:
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(
        cmd, cwd=cwd, env=env, check=True, text=True, stdout=subprocess.PIPE
    ).stdout


def main() -> int:
    ap = argparse.ArgumentParser()
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--alias", default="champion")
    group.add_argument("--version")
    args = ap.parse_args()

    mlflow.set_tracking_uri(os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5050"))
    client = mlflow.MlflowClient()
    mv = (
        client.get_model_version(MODEL, args.version)
        if args.version
        else client.get_model_version_by_alias(MODEL, args.alias)
    )
    commit, data_version = mv.tags["git_commit"], mv.tags["data_version"]
    expected = client.get_run(mv.run_id).data.metrics["test_macro_f1"]
    print(
        f"version {mv.version}: commit {commit[:10]}, data {data_version}, "
        f"test_macro_f1 {expected:.4f}"
    )

    work = ROOT / ".repro" / commit[:12]
    if not work.exists():
        sh(["git", "worktree", "add", "--detach", str(work), commit], ROOT)
    local_cfg = ROOT / ".dvc/config.local"
    if local_cfg.exists():
        shutil.copy(local_cfg, work / ".dvc/config.local")
    sh(["uv", "run", "--frozen", "dvc", "pull", "data/raw/ccdb_sample.dvc"], work)
    dvc_text = (work / "data/raw/ccdb_sample.dvc").read_text()
    pulled = re.search(r"md5:\s*([0-9a-f]+)\.dir", dvc_text).group(1)
    if pulled != data_version:
        print(f"data version mismatch: {pulled} != {data_version}")
        return 1

    env = {**os.environ, "PROJECT_ROOT": str(work)}
    out = sh(
        [
            "uv",
            "run",
            "--frozen",
            "python",
            "-m",
            "complaint_router.train",
            "--rebuild-features",
            "--skip-rules",
            "--run-name",
            f"reproduce-v{mv.version}",
            "--extra-tag",
            f"reproduces_version={mv.version}",
        ],
        work,
        env,
    )
    result = json.loads(out[out.index("{") :])
    got = result["results"][f"reproduce-v{mv.version}"]["test_macro_f1"]
    diff = abs(got - expected)
    ok = diff <= TOLERANCE
    print(
        f"registered {expected:.4f}  reproduced {got:.4f}  |diff| {diff:.4f}  "
        f"tolerance {TOLERANCE}  -> {'OK' if ok else 'FAILED'}"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
