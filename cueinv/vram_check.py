"""Phase 1.5 VRAM check.

Sends one ~4K-token prompt with a fixed num_ctx to Ollama, checks via /api/ps and nvidia-smi that the
model is fully resident on the GPU, and logs prompt/eval tokens per second. If the model spills to the
CPU it retries with a smaller num_ctx (plan: 8192 -> 6144).

Usage:
    python -m cueinv.vram_check --model qwen2.5-coder:7b-instruct-q4_K_M --num-ctx 8192 6144
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import urllib.request
from datetime import datetime, timezone

from cueinv import RESULTS_DIR

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")

# A realistic filler: rows shaped like IRIS internal-candidate rows (package,class,signature,doc).
_FILLER_ROW = (
    "org.example.io,FileStore,java.io.File resolve(java.lang.String relativePath, boolean create),"
    "Resolves a path relative to the store root...\n"
)


def build_prompt(target_tokens: int, chars_per_token: float = 4.55) -> str:
    """Return a prompt of roughly `target_tokens` tokens (exact count is read back from Ollama).

    chars_per_token=4.55 was calibrated on qwen2.5-coder for this filler (3.2 gave 2856 tokens for a 4096 target).
    """
    header = (
        "You are a security expert. For each Java method below, say whether any parameter could carry "
        "malicious end-user input. Answer with a JSON list.\n\nPackage,Class,Method,Doc\n"
    )
    n_rows = max(1, int((target_tokens * chars_per_token - len(header)) / len(_FILLER_ROW)))
    return header + _FILLER_ROW * n_rows


def _post(path: str, payload: dict, timeout: float = 600) -> dict:
    req = urllib.request.Request(
        OLLAMA_URL + path, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _get(path: str) -> dict:
    with urllib.request.urlopen(OLLAMA_URL + path, timeout=30) as r:
        return json.loads(r.read())


def gpu_fraction(ps_entry: dict) -> float:
    """Fraction of the loaded model that is in VRAM (1.0 == fully on GPU), from an /api/ps entry."""
    size = ps_entry.get("size") or 0
    vram = ps_entry.get("size_vram") or 0
    return (vram / size) if size else 0.0


def tokens_per_second(count: int | None, duration_ns: int | None) -> float | None:
    if not count or not duration_ns:
        return None
    return count / (duration_ns / 1e9)


def nvidia_smi_used_mib() -> int | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, check=True,
        ).stdout.strip().splitlines()[0]
        return int(out.split(",")[0])
    except Exception:
        return None


def run_once(model: str, num_ctx: int, target_tokens: int, seed: int = 0, num_gpu: int | None = None) -> dict:
    # Unload any resident model so the load happens with this num_ctx.
    try:
        _post("/api/generate", {"model": model, "keep_alive": 0}, timeout=120)
    except Exception:
        pass
    time.sleep(2)
    prompt = build_prompt(target_tokens)
    t0 = time.time()
    resp = _post(
        "/api/chat",
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"num_ctx": num_ctx, "temperature": 0, "top_k": 1, "top_p": 1, "seed": seed,
                        "num_predict": 256, **({"num_gpu": num_gpu} if num_gpu is not None else {})},
            "keep_alive": "5m",
        },
    )
    wall = time.time() - t0
    ps = [m for m in _get("/api/ps").get("models", []) if m.get("name") == model or m.get("model") == model]
    frac = gpu_fraction(ps[0]) if ps else 0.0
    return {
        "model": model,
        "num_ctx": num_ctx,
        "num_gpu": num_gpu,
        "prompt_eval_count": resp.get("prompt_eval_count"),
        "eval_count": resp.get("eval_count"),
        "prompt_tokens_per_s": tokens_per_second(resp.get("prompt_eval_count"), resp.get("prompt_eval_duration")),
        "eval_tokens_per_s": tokens_per_second(resp.get("eval_count"), resp.get("eval_duration")),
        "load_duration_s": (resp.get("load_duration") or 0) / 1e9,
        "wall_s": round(wall, 2),
        "gpu_fraction": round(frac, 4),
        "fully_on_gpu": frac >= 0.999,
        "ps_size_bytes": ps[0].get("size") if ps else None,
        "ps_size_vram_bytes": ps[0].get("size_vram") if ps else None,
        "ps_context_length": ps[0].get("context_length") if ps else None,
        "nvidia_smi_used_mib": nvidia_smi_used_mib(),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen2.5-coder:7b-instruct-q4_K_M")
    ap.add_argument("--num-ctx", type=int, nargs="+", default=[8192, 6144],
                    help="candidates in order; stop at the first that stays fully on the GPU")
    ap.add_argument("--target-tokens", type=int, default=4096)
    ap.add_argument("--num-gpu", type=int, default=None,
                    help="layers to offload (Ollama `num_gpu`); 99 = all. Default: let Ollama decide")
    ap.add_argument("--out", default=str(RESULTS_DIR / "vram_check.json"))
    args = ap.parse_args(argv)

    results = []
    chosen = None
    for ctx in args.num_ctx:
        r = run_once(args.model, ctx, args.target_tokens, num_gpu=args.num_gpu)
        print(json.dumps(r, indent=2))
        results.append(r)
        if r["fully_on_gpu"]:
            chosen = ctx
            break
    out = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ollama_version": _get("/api/version").get("version"),
        "runs": results,
        "chosen_num_ctx": chosen,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(out, f, indent=2)
    print(f"chosen_num_ctx={chosen} -> {args.out}")
    return 0 if chosen else 1


if __name__ == "__main__":
    raise SystemExit(main())
