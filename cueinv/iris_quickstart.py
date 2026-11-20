"""Phase 1.6: run IRIS's own CLI (`src/iris.py`) unmodified, with two *runtime* overrides that IRIS has no flag for.

1. Register our Ollama tag in IRIS's closed model-name map (`src/models/ollama.py:8-14`).
2. Set temperature to 0 in `_OLLAMA_DEFAULT_OPTIONS` (`src/models/ollama.py:18-23`; IRIS default is 0.8).

Everything else (seed 1234, no num_ctx, batch sizes, prompts, parsing, CodeQL, scoring) is IRIS's default.
No IRIS file is changed. The real experiment does not use this path; see docs/RECON.md §8.

Usage (inside the `iris` conda env, OLLAMA_HOST set):
    python -m cueinv.iris_quickstart --run-id quickstart-spark -- \
        perwendel__spark_CVE-2018-9159_2.7.1 --query cwe-022wLLM --skip-posthoc-filter
"""

from __future__ import annotations

import argparse
import os
import runpy
import sys

from cueinv import IRIS_ROOT
from cueinv.runlog import write_config

MODEL_ALIAS = "ollama-qwen2.5-coder-7b-q4km"
MODEL_TAG = "qwen2.5-coder:7b-instruct-q4_K_M"


def install_overrides(temperature: float = 0.0, alias: str = MODEL_ALIAS, tag: str = MODEL_TAG):
    """Patch IRIS's Ollama module *in memory*. Returns the patched module."""
    for p in (str(IRIS_ROOT), str(IRIS_ROOT / "src")):
        if p not in sys.path:
            sys.path.insert(0, p)
    # IRIS imports this module as `models.ollama` (src/models/llm.py:238), so patch that object.
    import models.ollama as om  # noqa: E402

    om._model_name_map[alias] = tag
    om._OLLAMA_DEFAULT_OPTIONS["temperature"] = temperature
    return om


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" in argv:
        i = argv.index("--")
        ours, iris_args = argv[:i], argv[i + 1:]
    else:
        ours, iris_args = argv, []
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--temperature", type=float, default=0.0)
    args = ap.parse_args(ours)

    if not os.environ.get("OLLAMA_HOST"):
        os.environ["OLLAMA_HOST"] = "http://127.0.0.1:11434"

    om = install_overrides(args.temperature)
    iris_argv = iris_args + ["--run-id", args.run_id, "--llm", MODEL_ALIAS]
    # IRIS writes its --seed (default 1234, iris.py:1329) into _OLLAMA_DEFAULT_OPTIONS when the model is built
    # (iris.py:280 -> ollama.py:35-37), so record the options as they will be at request time.
    seed = int(iris_args[iris_args.index("--seed") + 1]) if "--seed" in iris_args else 1234
    effective = {**om._OLLAMA_DEFAULT_OPTIONS, "seed": seed}
    write_config(args.run_id, model=MODEL_TAG, condition="IRIS_QUICKSTART", rep=1, seed=seed,
                 iris_argv=iris_argv, ollama_options_effective=effective,
                 note="IRIS CLI unmodified; in-memory overrides: model alias + temperature; num_ctx unset "
                      "(server default 4096), num_gpu unset (Ollama default fit)")

    sys.argv = [str(IRIS_ROOT / "src" / "iris.py")] + iris_argv
    os.chdir(IRIS_ROOT)
    try:
        runpy.run_path(str(IRIS_ROOT / "src" / "iris.py"), run_name="__main__")
    except SystemExit as e:  # IRIS calls exit() in several places
        return int(e.code or 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
