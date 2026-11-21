"""The only module that touches IRIS's SAPipeline (interception design, docs/RECON.md §8; approved at GATE 1).

IRIS is driven through its own stage methods; we inject our model object for the external stage and write the
internal label file ourselves. No IRIS source file is modified.
"""

from __future__ import annotations

import contextlib
import glob
import hashlib
import io
import os
import re
import sys
from pathlib import Path

from cueinv import IRIS_ROOT
from cueinv.dataset import Project, iris_owner_name
from cueinv.perturb import INTERNAL_BATCH_SIZE, Candidate

EXTERNAL_BATCH_SIZE = 30  # iris.py:1330
IRIS_SEED = 1234          # iris.py:1329 (only reaches IRIS's own model classes, which we bypass)


def ensure_iris_importable() -> None:
    for p in (str(IRIS_ROOT), str(IRIS_ROOT / "src")):
        if p not in sys.path:
            sys.path.insert(0, p)


def model_tag(model: str) -> str:
    """Filesystem-safe short tag, e.g. 'qwen2.5-coder:7b-instruct-q4_K_M' -> 'qwen2.5-coder-7b-instruct-q4_K_M'."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", model)


class IrisStageError(RuntimeError):
    pass


@contextlib.contextmanager
def iris_cwd():
    """IRIS resolves some paths relative to its root; also turn its exit() calls into exceptions."""
    old = os.getcwd()
    os.chdir(IRIS_ROOT)
    try:
        yield
    except SystemExit as e:
        raise IrisStageError(f"IRIS called exit({e.code})") from e
    finally:
        os.chdir(old)


def make_pipeline(project: Project, iris_run_id: str, *, llm_name: str, use_container: bool = True):
    """SAPipeline with every relevant argument explicit (several constructor defaults differ from the CLI, RECON §3)."""
    ensure_iris_importable()
    from src.iris import SAPipeline  # noqa: E402

    with iris_cwd():
        p = SAPipeline(
            project.slug, project.query, run_id=iris_run_id, llm=llm_name,
            label_api_batch_size=EXTERNAL_BATCH_SIZE, label_func_param_batch_size=INTERNAL_BATCH_SIZE,
            num_threads=1, seed=IRIS_SEED, use_exhaustive_qll=True,          # __main__ forces exhaustive QLL (iris.py:1361)
            filter_by_module=False, filter_by_module_large=False,             # must stay off: uses fix locations (RECON §6)
            skip_posthoc_filter=True, use_container=use_container,
        )
    assert p.filter_by_module is False and p.filter_by_module_large is False
    assert p.label_func_param_batch_size == INTERNAL_BATCH_SIZE and p.label_api_batch_size == EXTERNAL_BATCH_SIZE
    # Isolate IRIS's cross-project external-label cache per project (RECON §4) so batches don't depend on run order.
    p.api_labels_cache_path = f"{p.analysis_common_path}/api_labels_cache_{model_tag(llm_name)}.json"
    return p


def collect_candidates(p) -> None:
    """IRIS stages 1-2 (CodeQL candidate extraction + filtering)."""
    with iris_cwd():
        p.collect_invoked_external_apis()
        p.collect_internal_function_parameters()


def internal_candidates(p) -> list[Candidate]:
    """IRIS's own dedup + JavaDoc truncation (iris.py:700-723)."""
    with iris_cwd(), contextlib.redirect_stdout(io.StringIO()):
        rows = p.fetch_func_param_src_candidates()
    return [Candidate(package=r[0], clazz=r[1], func=r[2], signature=r[3], doc=r[4]) for r in rows]


def n_external_candidates(p) -> int:
    import pandas as pd

    return len(pd.read_csv(p.candidate_apis_csv_path, keep_default_na=False))


def readme_summary(p) -> str | None:
    """IRIS's README head (fix-commit README, heuristic first paragraph; cached per IRIS run dir). GATE 1 Q2: kept as is."""
    with iris_cwd():
        return p.fetch_project_description_from_readme()


def identity(project: Project) -> tuple[str, str]:
    """IRIS's prompt identity: owner = slug.split('_')[0], name = slug.split('_')[2] (iris.py:731-732)."""
    return iris_owner_name(project.slug)


def run_external_labelling(p, adapter) -> list[dict]:
    """IRIS stage 3 with our model injected (iris.py:278-281). Returns the prompt files' hashes for the byte-identity check."""
    p.model = adapter
    with iris_cwd():
        p.query_gpt_for_api_src_tp_sink_batched()
    return external_prompt_hashes(p)


def external_prompt_hashes(p) -> list[dict]:
    out = []
    for f in sorted(glob.glob(f"{p.label_api_log_path}/raw_user_prompt_*.txt"),
                    key=lambda s: int(re.search(r"_(\d+)\.txt$", s).group(1))):
        out.append({"file": os.path.basename(f), "sha256": hashlib.sha256(Path(f).read_bytes()).hexdigest()})
    return out


def external_label_counts(p) -> dict:
    import json

    def n(path):
        return len(json.load(open(path))) if os.path.exists(path) else None

    return {"sources": n(p.llm_labelled_source_apis_path), "sinks": n(p.llm_labelled_sink_apis_path),
            "taint_props": n(p.llm_labelled_taint_prop_apis_path)}


# ---- detection (IRIS stages 5-9, resumed from label files) ---------------------------------------------------------
SHARED_ARTEFACTS = ("external_apis_csv_path", "candidate_apis_csv_path", "func_param_path",
                    "source_func_param_candidates_path", "func_locs_path", "class_locs_path",
                    "llm_labelled_source_apis_path", "llm_labelled_sink_apis_path", "llm_labelled_taint_prop_apis_path")


def run_detection(shared_p, project: Project, iris_run_id: str, internal_labels_json: Path, *, llm_name: str) -> dict:
    """Seed a fresh IRIS run dir with the shared stage 1-3 artefacts and our internal label file, then let IRIS build
    the specs, run CodeQL, post-process and score (RECON §8). Returns IRIS's final results.json."""
    import json
    import shutil

    p = make_pipeline(project, iris_run_id, llm_name=llm_name)
    for attr in SHARED_ARTEFACTS:
        src, dst = getattr(shared_p, attr), getattr(p, attr)
        if os.path.exists(src) and not os.path.exists(dst):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy(src, dst)
    os.makedirs(os.path.dirname(p.llm_labelled_source_func_params_path), exist_ok=True)
    shutil.copy(internal_labels_json, p.llm_labelled_source_func_params_path)
    with iris_cwd():
        p.build_project_specific_query()
        p.find_vulnerability()
        if not os.path.exists(p.query_output_result_sarif_path):
            raise IrisStageError("CodeQL produced no SARIF")
        p.post_process_cwe_query_result()
        p.evaluate_result()
    # keep the lazily computed locations in the shared dir for the next condition
    for attr in ("func_locs_path", "class_locs_path"):
        src, dst = getattr(p, attr), getattr(shared_p, attr)
        if os.path.exists(src) and not os.path.exists(dst):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy(src, dst)
    res = json.load(open(p.final_output_json_path))
    res["iris_output_dir"] = p.project_output_path
    return res


# ---- IRIS's response parser (free-form mode, GATE 2 D1(a)) -----------------------------------------------------------
class _ErrorLog:
    def __init__(self):
        self.errors = []

    def info(self, *a, **k):
        pass

    def error(self, m, *a, **k):
        self.errors.append(str(m))


def iris_parse(text: str | None) -> tuple[list | None, str]:
    """Parse exactly as IRIS does (iris.py:484-526) and classify the outcome from IRIS's own error log:
    ok (no error; a genuine `[]` is ok), recovered (IRIS's fallback list parsing salvaged items), parse_failed."""
    if text is None:
        return None, "no_response"
    ensure_iris_importable()
    from src.iris import SAPipeline  # noqa: E402

    p = SAPipeline.__new__(SAPipeline)
    log = _ErrorLog()
    p.project_logger = log
    with contextlib.redirect_stdout(io.StringIO()):
        items = p.parse_json(text)
    items = [x for x in (items or []) if isinstance(x, dict)]
    if not log.errors:
        return items, "ok"
    return (items, "recovered") if items else (items, "parse_failed")
