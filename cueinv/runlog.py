"""Run bookkeeping (global rule 2): every run writes runs/<run_id>/config.json."""

from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from cueinv import CAPSTONE_ROOT, IRIS_ROOT, RUNS_DIR

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")


def git_sha(repo: Path) -> str | None:
    try:
        sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True,
                             check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, check=True).stdout.strip()
        return sha + ("-dirty" if dirty else "")
    except Exception:
        return None


def ollama_version() -> str | None:
    try:
        with urllib.request.urlopen(OLLAMA_URL + "/api/version", timeout=10) as r:
            return json.loads(r.read()).get("version")
    except Exception:
        return None


def ollama_model_digest(model: str) -> str | None:
    try:
        with urllib.request.urlopen(OLLAMA_URL + "/api/tags", timeout=10) as r:
            for m in json.loads(r.read()).get("models", []):
                if m.get("name") == model or m.get("model") == model:
                    return m.get("digest")
    except Exception:
        pass
    return None


def build_config(run_id: str, *, model: str | None = None, condition: str | None = None,
                 rep: int | None = None, seed: int | None = None, **extra) -> dict:
    return {
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "iris_git_sha": git_sha(IRIS_ROOT),
        "cueinv_git_sha": git_sha(CAPSTONE_ROOT),
        "ollama_version": ollama_version(),
        "model": model,
        "model_digest": ollama_model_digest(model) if model else None,
        "condition": condition,
        "rep": rep,
        "seed": seed,
        **extra,
    }


def write_config(run_id: str, runs_dir: Path = RUNS_DIR, **fields) -> Path:
    run_dir = Path(runs_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "config.json"
    from cueinv.fsutil import atomic_write_text

    atomic_write_text(path, json.dumps(build_config(run_id, **fields), indent=2))
    return path
