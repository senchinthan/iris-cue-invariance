"""Deterministic Ollama client with a resumable per-run call cache (plan Phase 4, global rule 3).

Decoding: temperature=0, top_k=1, top_p=1, fixed seed, fixed num_ctx, num_predict=2048, num_gpu=99 (all layers on the GPU;
see docs/VERSIONS.md "VRAM check"). Output is constrained with Ollama structured outputs (`format=<JSON schema>`) to the
JSON shapes in IRIS's system prompts (src/prompts.py). qwen3 runs with `think: false`.

Every call is appended to runs/<run_id>/llm_calls.jsonl. A call is a cache hit when (prompt_sha256, model, condition, rep)
was already recorded; the stored request_sha256 (messages + options + format + think) must then match, otherwise we
raise — a run_id must never silently mix decoding settings.

Output modes (GATE 2 decision D1(a), docs/GATE2.md): by default prompts are sent **without** `format` (IRIS never constrains
output) and responses are parsed with IRIS's own `parse_json` (`cueinv.iris_bridge.iris_parse`). The schema mode is kept
for the record (campaign `pilot`): with `format=<schema>` the model answered `[]` for 38/44 internal batches.

Parse statuses: ok | recovered (IRIS's fallback list parsing salvaged items) | truncated (num_predict reached) |
parse_failed | no_response. Retry policy: on truncated/parse_failed/no_response the identical request is sent once more.
With greedy decoding this normally reproduces the same output, so the retry mainly absorbs transient server errors; both
attempts are logged and the second outcome wins.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from cueinv import RUNS_DIR

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")

# ---- JSON schemas mirroring IRIS's requested output formats (src/prompts.py:10-15 and :43-47) ----
INTERNAL_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "package": {"type": "string"},
            "class": {"type": "string"},
            "method": {"type": "string"},
            "signature": {"type": "string"},
            "tainted_input": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["package", "class", "method", "signature", "tainted_input"],
    },
}
EXTERNAL_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "package": {"type": "string"},
            "class": {"type": "string"},
            "method": {"type": "string"},
            "signature": {"type": "string"},
            "sink_args": {"type": "array", "items": {"type": "string"}},
            "type": {"type": "string", "enum": ["source", "sink", "taint-propagator"]},
        },
        "required": ["package", "class", "method", "signature", "sink_args", "type"],
    },
}

DEFAULT_OPTIONS = {"temperature": 0, "top_k": 1, "top_p": 1, "seed": 0, "num_ctx": 8192, "num_predict": 2048,
                   "num_gpu": 99}


def sha256(obj) -> str:
    data = obj if isinstance(obj, (bytes, str)) else json.dumps(obj, sort_keys=True, ensure_ascii=False)
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def prompt_sha256(messages: list[dict]) -> str:
    return sha256([{"role": m["role"], "content": m["content"]} for m in messages])


FAILED_STATUSES = ("parse_failed", "truncated", "no_response")


def parse_json_list(text: str | None) -> tuple[list | None, str]:
    """Strict parse of a structured-output response. Returns (items, status)."""
    if text is None:
        return None, "no_response"
    try:
        val = json.loads(text)
    except json.JSONDecodeError:
        return None, "parse_failed"
    if not isinstance(val, list) or not all(isinstance(x, dict) for x in val):
        return None, "parse_failed"
    return val, "ok"


def gpu_snapshot(model: str) -> dict:
    snap = {"gpu_fraction": None, "vram_used_mib": None}
    try:
        with urllib.request.urlopen(OLLAMA_URL + "/api/ps", timeout=10) as r:
            for m in json.loads(r.read()).get("models", []):
                if m.get("name") == model or m.get("model") == model:
                    snap["gpu_fraction"] = round((m.get("size_vram") or 0) / max(1, m.get("size") or 1), 4)
    except Exception:
        pass
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout.strip().splitlines()
        snap["vram_used_mib"] = int(out[0]) if out else None
    except Exception:
        pass
    return snap


@dataclass
class CallResult:
    content: str | None
    parsed: list | None
    parse_status: str
    cached: bool
    record: dict = field(default_factory=dict)


class CallCache:
    """Append-only JSONL cache keyed by (prompt_sha256, model, condition, rep)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._index: dict[tuple, dict] = {}
        if self.path.exists():
            with open(self.path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        rec = json.loads(line)
                        if rec.get("kind", "call") == "call":
                            self._index[self.key(rec["prompt_sha256"], rec["model"], rec["condition"], rec["rep"])] = rec

    @staticmethod
    def key(prompt_hash: str, model: str, condition: str, rep: int) -> tuple:
        return (prompt_hash, model, condition, int(rep))

    def get(self, key: tuple) -> dict | None:
        return self._index.get(key)

    def append(self, rec: dict) -> None:
        with self._lock:
            with open(self.path, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if rec.get("kind", "call") == "call":
                self._index[self.key(rec["prompt_sha256"], rec["model"], rec["condition"], rec["rep"])] = rec

    def __len__(self):
        return len(self._index)


class OllamaClient:
    def __init__(self, model: str, run_id: str, *, options: dict | None = None, timeout: float = 1800,
                 runs_dir: Path = RUNS_DIR, transport=None):
        self.model = model
        self.run_id = run_id
        self.options = {**DEFAULT_OPTIONS, **(options or {})}
        self.think = False if model.startswith("qwen3") else None
        self.timeout = timeout
        self.cache = CallCache(Path(runs_dir) / run_id / "llm_calls.jsonl")
        self._transport = transport or self._http_chat
        self._warm = False

    # -- transport -------------------------------------------------------------------------------------------
    def _http_chat(self, body: dict) -> dict:
        req = urllib.request.Request(OLLAMA_URL + "/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read())

    def _body(self, messages: list[dict], schema: dict | None, options: dict | None = None) -> dict:
        body = {"model": self.model, "messages": messages, "stream": False, "options": options or self.options,
                "keep_alive": "30m"}
        if schema is not None:
            body["format"] = schema
        if self.think is not None:
            body["think"] = self.think
        return body

    # -- warm-up -----------------------------------------------------------------------------------------------
    def warmup(self) -> dict:
        """One discarded call after (re)loading the model, logged as kind=warmup with the GPU placement."""
        t0 = time.time()
        resp = self._transport(self._body([{"role": "user", "content": "Reply with an empty JSON list."}],
                                          {"type": "array", "items": {"type": "string"}},
                                          {**self.options, "num_predict": 8}))
        rec = {"kind": "warmup", "model": self.model, "run_id": self.run_id,
               "timestamp": datetime.now(timezone.utc).isoformat(), "wall_s": round(time.time() - t0, 3),
               "load_duration_s": (resp.get("load_duration") or 0) / 1e9, **gpu_snapshot(self.model)}
        self.cache.append(rec)
        self._warm = True
        return rec

    # -- main entry ----------------------------------------------------------------------------------------------
    def chat(self, messages: list[dict], *, schema: dict | None, condition: str, rep: int,
             meta: dict | None = None, parser=None) -> CallResult:
        """parser(text) -> (items | None, status); default: strict JSON list (right for schema mode)."""
        parser = parser or parse_json_list
        phash = prompt_sha256(messages)
        body = self._body(messages, schema)
        rhash = sha256({k: body[k] for k in ("model", "messages", "options") if k in body}
                       | {"format": body.get("format"), "think": body.get("think")})
        key = CallCache.key(phash, self.model, condition, rep)
        hit = self.cache.get(key)
        if hit is not None:
            if hit["request_sha256"] != rhash:
                raise RuntimeError(f"cache entry for {key} was made with different request settings; "
                                   f"use a new run_id instead of mixing settings")
            return CallResult(hit["response"], hit["parsed"], hit["parse_status"], True, hit)

        if not self._warm:
            self.warmup()
        attempts = []
        content, parsed, status, resp = None, None, "no_response", {}
        for attempt in (1, 2):
            t0 = time.time()
            try:
                resp = self._transport(body)
                content = resp.get("message", {}).get("content")
                err = None
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
                resp, content, err = {}, None, repr(e)[:300]
            wall = time.time() - t0
            if content is None:
                parsed, status = None, "no_response"
            else:
                parsed, status = parser(content)
                if resp.get("done_reason") == "length":
                    status = "truncated"  # cut by num_predict; keep whatever the parser salvaged (IRIS would)
            attempts.append({"attempt": attempt, "parse_status": status, "wall_s": round(wall, 3), "error": err,
                             "done_reason": resp.get("done_reason")})
            if status not in FAILED_STATUSES:
                break

        rec = {
            "kind": "call",
            "run_id": self.run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "prompt_sha256": phash,
            "request_sha256": rhash,
            "model": self.model,
            "condition": condition,
            "rep": int(rep),
            "options": self.options,
            "think": self.think,
            "response": content,
            "parsed": parsed,
            "parse_status": status,
            "attempts": attempts,
            "retry_identical": len(attempts) == 2 and attempts[0]["parse_status"] == attempts[1]["parse_status"],
            "prompt_tokens": resp.get("prompt_eval_count"),
            "completion_tokens": resp.get("eval_count"),
            "prompt_eval_s": (resp.get("prompt_eval_duration") or 0) / 1e9,
            "eval_s": (resp.get("eval_duration") or 0) / 1e9,
            "load_s": (resp.get("load_duration") or 0) / 1e9,
            "wall_s": round(sum(a["wall_s"] for a in attempts), 3),
            "done_reason": resp.get("done_reason"),
            "messages": messages,
            "meta": meta or {},
            **gpu_snapshot(self.model),
        }
        if (rec["load_s"] or 0) > 1.0:  # the model was (re)loaded for this call: next new call warms up again first
            self._warm = False
        self.cache.append(rec)
        return CallResult(content, parsed, status, False, rec)


class IrisModelAdapter:
    """Drop-in for IRIS's model object (`SAPipeline.model`, iris.py:278-281): IRIS calls
    `predict(list_of_[system,user]_messages, batch_size=num_threads)` and parses the returned strings itself."""

    def __init__(self, client: OllamaClient, *, condition: str, rep: int = 1, schema: dict | None = None,
                 meta: dict | None = None, parser=None):
        self.client, self.condition, self.rep, self.schema, self.meta = client, condition, rep, schema, meta or {}
        self.parser = parser  # only for our call log's parse_status; IRIS parses the returned text itself
        self.results: list[CallResult] = []

    def predict(self, prompt, batch_size=0, no_progress_bar=False):
        single = bool(prompt) and isinstance(prompt[0], dict)
        prompts = [prompt] if single else list(prompt)
        out = []
        for i, msgs in enumerate(prompts):  # sequential: OLLAMA_NUM_PARALLEL=1 and deterministic order
            r = self.client.chat(msgs, schema=self.schema, condition=self.condition, rep=self.rep,
                                 meta={**self.meta, "batch_index": i}, parser=self.parser)
            self.results.append(r)
            # IRIS writes str(response)/response+"\n" (iris.py:590,772): never hand it None
            out.append(r.content if r.content is not None else "[]")
        return out[0] if single else out
