"""Labelling (plan Phase 4).

label_external(project, model): once per project x model. IRIS builds and parses the external prompts itself
    (verbatim, RECON §8); our deterministic client answers. Every condition reuses these labels.
label_internal(project, condition, rep, model): internal prompts built from IRIS's own candidate rows and templates through
    the perturbation layer; answers are validated against the batch, mapped back to original names and written in IRIS's
    exact label format (`llm_labelled_source_func_params.json`).

Run layout (global rule 2): runs/<campaign>__<model_tag>__<condition>__r<rep>/
    config.json, llm_calls.jsonl, rename_map/<slug>.json, labels/<slug>/{llm_labelled_source_func_params.json,
    source_params.json, stats.json}
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from cueinv import RUNS_DIR
from cueinv import iris_bridge as ib
from cueinv.dataset import Project
from cueinv.llm import EXTERNAL_SCHEMA, FAILED_STATUSES, INTERNAL_SCHEMA, IrisModelAdapter, OllamaClient
from cueinv.perturb import H1C_VIEWS, PROMPT_CONDITIONS, PromptView, make_view, stable_seed
from cueinv.runlog import write_config

PRIMARY_MODEL = "qwen2.5-coder:7b-instruct-q4_K_M"
# GATE 2 D1(a): free-form output parsed with IRIS's parse_json. "schema" reproduces the original plan-Phase-4 setting.
DEFAULT_OUTPUT = "free"
OUTPUT_MODES = ("free", "schema")


def output_settings(output: str, schema: dict):
    """(format schema or None, parser) for an output mode."""
    if output == "schema":
        return schema, None          # strict JSON parser (llm.parse_json_list)
    if output == "free":
        return None, ib.iris_parse   # IRIS's own parser
    raise ValueError(f"unknown output mode {output!r}; expected one of {OUTPUT_MODES}")


def run_id_for(campaign: str, model: str, condition: str, rep: int) -> str:
    return f"{campaign}__{ib.model_tag(model)}__{condition}__r{rep}"


def iris_run_id_for(campaign: str, model: str) -> str:
    """One IRIS output dir per campaign x model for stages 1-3 (candidates, README, external labels)."""
    return f"cueinv-{campaign}-{ib.model_tag(model)}"


def llm_name(model: str) -> str:
    """The string IRIS stores as its `llm` (only used for its cache file name; our client does the calls)."""
    return f"cueinv-{ib.model_tag(model)}"


def condition_seed(project: Project, condition: str) -> int:
    """Per project and condition. COMBO uses R_NEU's seed, so COMBO is exactly R_NEU's rename + D_STRIP."""
    return stable_seed(project.slug, "R_NEU" if condition == "COMBO" else condition)


# ---------------------------------------------------------------------------------------------------------------
@dataclass
class Shared:
    """Per project x model inputs shared by every condition."""
    pipeline: object
    candidates: list
    readme: str | None
    owner: str
    name: str
    n_external_candidates: int
    candidates_s: float = 0.0
    ctx: object = None  # PerturbContext


_SHARED: dict[tuple[str, str, str], Shared] = {}


def shared_inputs(project: Project, model: str, campaign: str) -> Shared:
    key = (project.slug, model, campaign)
    if key not in _SHARED:
        p = ib.make_pipeline(project, iris_run_id_for(campaign, model), llm_name=llm_name(model))
        t0 = time.time()
        ib.collect_candidates(p)
        dt = time.time() - t0
        owner, name = ib.identity(project)
        _SHARED[key] = Shared(p, ib.internal_candidates(p), ib.readme_summary(p), owner, name,
                              ib.n_external_candidates(p), round(dt, 1), ib.perturb_context(p))
    return _SHARED[key]


# ---------------------------------------------------------------------------------------------------------------
def label_external(project: Project, model: str = PRIMARY_MODEL, campaign: str = "main",
                   output: str = DEFAULT_OUTPUT) -> dict:
    sh = shared_inputs(project, model, campaign)
    run_id = run_id_for(campaign, model, "EXT", 1)
    run_dir = RUNS_DIR / run_id
    out = run_dir / "labels" / project.slug / "external.json"
    if out.exists():
        return json.loads(out.read_text())
    if not (run_dir / "config.json").exists():
        write_config(run_id, model=model, condition="EXT", rep=1, seed=0, campaign=campaign, output=output,
                     note="external labels via IRIS stage 3 with injected client")
    client = OllamaClient(model, run_id)
    fmt, parser = output_settings(output, EXTERNAL_SCHEMA)
    adapter = IrisModelAdapter(client, condition="EXT", rep=1, schema=fmt, parser=parser, meta={"slug": project.slug})
    t0 = time.time()
    hashes = ib.run_external_labelling(sh.pipeline, adapter)
    summary = {
        "slug": project.slug, "model": model, "n_external_candidates": sh.n_external_candidates,
        "n_calls": len(adapter.results), "n_cached": sum(r.cached for r in adapter.results),
        "output": output,
        "n_parse_failed": sum(r.parse_status in FAILED_STATUSES for r in adapter.results),
        "n_recovered": sum(r.parse_status == "recovered" for r in adapter.results),
        "wall_s": round(time.time() - t0, 1), "candidates_s": sh.candidates_s,
        "labels": ib.external_label_counts(sh.pipeline),
        "external_prompt_sha256": hashes,
        "iris_output_dir": sh.pipeline.project_output_path,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2))
    return summary


# ---------------------------------------------------------------------------------------------------------------
@dataclass
class InternalResult:
    labels: list[dict]                       # IRIS format, original names
    source_params: set[tuple[str, str, str, str]]
    stats: dict = field(default_factory=dict)


def build_messages(view: PromptView, batch) -> list[dict]:
    ib.ensure_iris_importable()
    from src.prompts import FUNC_PARAM_LABELLING_SYSTEM_PROMPT, FUNC_PARAM_LABELLING_USER_PROMPT

    user = FUNC_PARAM_LABELLING_USER_PROMPT.format(
        project_username=view.owner, project_name=view.name, project_readme_summary=view.readme,
        methods="\n".join(c.row() for c in batch))
    return [{"role": "system", "content": FUNC_PARAM_LABELLING_SYSTEM_PROMPT}, {"role": "user", "content": user}]


def map_back(view: PromptView, batch, parsed: list[dict] | None) -> tuple[dict, dict]:
    """Validate one batch's answer against the batch (perturbed namespace), then map to original names.
    Returns ({orig key: set(orig params)}, counters). Items naming anything not in the batch are 'unmapped'."""
    by_key = {c.key: c for c in batch}
    found: dict[tuple[str, str, str], set[str]] = {}
    ctr = {"items": 0, "items_unmapped": 0, "params": 0, "params_unmapped": 0}
    for item in parsed or []:
        ctr["items"] += 1
        key = (str(item.get("package", "")), str(item.get("class", "")), str(item.get("method", "")))
        cand = by_key.get(key)
        if cand is None:
            ctr["items_unmapped"] += 1
            continue
        orig = view.origin[cand.key]
        valid = set(cand.params)
        params = found.setdefault(orig.key, set())
        for t in item.get("tainted_input") or []:
            ctr["params"] += 1
            if not isinstance(t, str) or t not in valid:
                ctr["params_unmapped"] += 1
                continue
            params.add(view.orig_param(t))
    return found, ctr


FIXED_LABEL_CONDITIONS = ("H1C", "ABLATION")  # no LLM calls: vote over views / no internal sources


def _write_labels(run_dir: Path, project: Project, sh: Shared, found: dict, stats: dict, view=None) -> InternalResult:
    lab_dir = run_dir / "labels" / project.slug
    orig_by_key = {c.key: c for c in sh.candidates}
    labels = [{"package": k[0], "class": k[1], "method": k[2], "signature": orig_by_key[k].signature,
               "tainted_input": sorted(v)} for k, v in sorted(found.items())]
    source_params = {(k[0], k[1], k[2], p) for k, v in found.items() for p in v}
    stats.update({"n_methods_labelled": len(labels), "n_source_params": len(source_params)})
    lab_dir.mkdir(parents=True, exist_ok=True)
    (lab_dir / "llm_labelled_source_func_params.json").write_text(json.dumps(labels, indent=2))
    (lab_dir / "source_params.json").write_text(json.dumps(sorted(source_params), indent=1))
    if view is not None:
        (run_dir / "rename_map").mkdir(exist_ok=True)
        (run_dir / "rename_map" / f"{project.slug}.json").write_text(json.dumps(view.rename_map_json(), indent=1))
    (lab_dir / "stats.json").write_text(json.dumps(stats, indent=2))
    return InternalResult(labels, source_params, stats)


def _load_result(lab_dir: Path) -> InternalResult:
    labels = json.loads((lab_dir / "llm_labelled_source_func_params.json").read_text())
    sp = {tuple(x) for x in json.loads((lab_dir / "source_params.json").read_text())}
    return InternalResult(labels, sp, json.loads((lab_dir / "stats.json").read_text()))


def base_stats(project: Project, model: str, campaign: str, rep: int = 1) -> dict | None:
    f = RUNS_DIR / run_id_for(campaign, model, "BASE", rep) / "labels" / project.slug / "stats.json"
    return json.loads(f.read_text()) if f.exists() else None


def label_internal(project: Project, condition: str, rep: int, model: str = PRIMARY_MODEL,
                   campaign: str = "main", output: str = DEFAULT_OUTPUT) -> InternalResult:
    sh = shared_inputs(project, model, campaign)
    run_id = run_id_for(campaign, model, condition, rep)
    run_dir = RUNS_DIR / run_id
    lab_dir = run_dir / "labels" / project.slug
    seed = condition_seed(project, condition)
    if not (run_dir / "config.json").exists():
        write_config(run_id, model=model, condition=condition, rep=rep, seed=seed, campaign=campaign, output=output,
                     seed_rule="per project: stable_seed(slug, condition); COMBO uses R_NEU's seed")
    if (lab_dir / "stats.json").exists():
        return _load_result(lab_dir)
    base = {"slug": project.slug, "condition": condition, "rep": rep, "model": model, "seed": seed, "output": output,
            "n_internal_candidates": len(sh.candidates), "n_internal_params": sum(len(c.params) for c in sh.candidates),
            "n_external_candidates": sh.n_external_candidates}

    if condition in FIXED_LABEL_CONDITIONS:
        from cueinv.vote import vote_found  # noqa: PLC0415

        if condition == "ABLATION":  # BASE with every internal source removed (external specs only)
            found, extra = {}, {"note": "no internal sources (ablation)"}
        else:  # H1C: cue-diverse vote, a parameter is a source if >= 2 of the 3 views agree
            views = [label_internal(project, v, rep, model, campaign, output) for v in H1C_VIEWS]
            found = vote_found([r.source_params for r in views], k=2)
            extra = {"views": list(H1C_VIEWS), "k": 2,
                     "view_source_params": {v: len(r.source_params) for v, r in zip(H1C_VIEWS, views)}}
        return _write_labels(run_dir, project, sh, found, {**base, "n_calls": 0, "n_parse_failed": 0,
                                                           "n_recovered": 0, "items": 0, "items_unmapped": 0,
                                                           "params": 0, "params_unmapped": 0, "calls": [], **extra})
    if condition not in PROMPT_CONDITIONS:
        raise ValueError(f"unknown condition {condition}")

    view = make_view(sh.candidates, sh.readme, sh.owner, sh.name, condition, seed, ctx=sh.ctx)
    client = OllamaClient(model, run_id)
    fmt, parser = output_settings(output, INTERNAL_SCHEMA)
    found: dict[tuple[str, str, str], set[str]] = {}
    totals = {"items": 0, "items_unmapped": 0, "params": 0, "params_unmapped": 0}
    calls = []
    bstats = base_stats(project, model, campaign) if condition not in ("BASE", "SHUF") else None
    base_tokens = {c["batch_index"]: c.get("prompt_tokens") for c in (bstats or {}).get("calls", [])}
    for bi, b in enumerate(view.batches):
        msgs = build_messages(view, b)
        r = client.chat(msgs, schema=fmt, condition=condition, rep=rep, parser=parser,
                        meta={"slug": project.slug, "batch_index": bi, "batch_keys": [list(c.key) for c in b]})
        f, ctr = map_back(view, b, r.parsed)
        for k, v in f.items():
            found.setdefault(k, set()).update(v)
        for k in totals:
            totals[k] += ctr[k]
        rec = r.record
        pt, bt = rec.get("prompt_tokens"), base_tokens.get(bi)
        calls.append({"batch_index": bi, "batch_size": len(b), "parse_status": r.parse_status, "cached": r.cached,
                      "prompt_sha256": rec.get("prompt_sha256"), "prompt_tokens": pt,
                      # same batch composition as BASE for every non-SHUF condition -> per-batch covariate (plan Ph. 6)
                      "token_delta_vs_base": (pt - bt) if (pt is not None and bt is not None) else None,
                      "completion_tokens": rec.get("completion_tokens"), "wall_s": rec.get("wall_s"),
                      "gpu_fraction": rec.get("gpu_fraction"), "vram_used_mib": rec.get("vram_used_mib"), **ctr})
    stats = {**base, "n_batches": len(view.batches), "n_calls": len(calls),
             "n_parse_failed": sum(c["parse_status"] in FAILED_STATUSES for c in calls),
             "n_recovered": sum(c["parse_status"] == "recovered" for c in calls), **totals,
             "map_back_failure_rate": round(totals["items_unmapped"] / totals["items"], 4) if totals["items"] else 0.0,
             "rename_stats": view.stats, "calls": calls}
    return _write_labels(run_dir, project, sh, found, stats, view)


# ---------------------------------------------------------------------------------------------------------------
def detect(project: Project, condition: str, rep: int, model: str = PRIMARY_MODEL, campaign: str = "main") -> dict:
    """IRIS CodeQL stage (--skip-posthoc-filter) on this condition's internal labels + the shared external labels."""
    run_id = run_id_for(campaign, model, condition, rep)
    out = RUNS_DIR / run_id / "detection" / f"{project.slug}.json"
    if out.exists():
        return json.loads(out.read_text())
    labels_json = RUNS_DIR / run_id / "labels" / project.slug / "llm_labelled_source_func_params.json"
    if not labels_json.exists():
        raise FileNotFoundError(f"no internal labels for {project.slug} in {run_id}")
    sh = shared_inputs(project, model, campaign)
    t0 = time.time()
    res = ib.run_detection(sh.pipeline, project, f"{iris_run_id_for(campaign, model)}-{condition}-r{rep}", labels_json,
                           llm_name=llm_name(model))
    res["detection_s"] = round(time.time() - t0, 1)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=2))
    return res
