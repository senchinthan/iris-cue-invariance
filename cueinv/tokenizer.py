"""Token counting with the primary model's tokenizer (Qwen2.5-Coder, pinned revision), used to token-count-match the
neutral renames (R_NEU, R_NEU2). Loaded from the local Hugging Face cache only (no network at run time)."""

from __future__ import annotations

import functools
import os
from pathlib import Path

TOKENIZER_REPO = "Qwen/Qwen2.5-Coder-7B-Instruct"
TOKENIZER_REVISION = "c03e6d358207e414f1eca0bb1891e29f1db0e242"


def tokenizer_path() -> Path:
    root = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    return root / f"models--{TOKENIZER_REPO.replace('/', '--')}" / "snapshots" / TOKENIZER_REVISION


@functools.lru_cache(maxsize=1)
def _tok():
    from transformers import AutoTokenizer

    p = tokenizer_path()
    if not p.exists():
        raise FileNotFoundError(f"tokenizer not cached at {p}; fetch with huggingface_hub.snapshot_download("
                                f"'{TOKENIZER_REPO}', revision='{TOKENIZER_REVISION}', allow_patterns=['tokenizer*', "
                                f"'vocab.json', 'merges.txt'])")
    return AutoTokenizer.from_pretrained(str(p), local_files_only=True)


@functools.lru_cache(maxsize=200_000)
def count_tokens(s: str) -> int:
    return len(_tok().encode(s, add_special_tokens=False))


def vocab() -> dict[str, int]:
    return _tok().get_vocab()
