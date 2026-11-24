"""Resumable driver: one project at a time; external labels, internal conditions, optional detection.

    python -m cueinv.run --campaign pilot --projects results/pilot.csv \
        --model qwen2.5-coder:7b-instruct-q4_K_M --external --conditions BASE:1,2 SHUF:1 --detect BASE:1

Everything is cached per call (runs/<run_id>/llm_calls.jsonl) and per project (labels/<slug>/stats.json), so re-running
the same command continues where it stopped. Progress lines go to runs/<campaign>__progress.jsonl.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import time
import traceback
from datetime import datetime, timezone

from cueinv import RUNS_DIR
from cueinv.build_dbs import db_exists
from cueinv.llm import OllamaUnavailable
from cueinv.dataset import load_projects
from cueinv.label import DEFAULT_OUTPUT, OUTPUT_MODES, PRIMARY_MODEL, detect, label_external, label_internal


def parse_condition_reps(specs: list[str]) -> list[tuple[str, int]]:
    """['BASE:1,2', 'SHUF'] -> [('BASE', 1), ('BASE', 2), ('SHUF', 1)]"""
    out = []
    for s in specs or []:
        cond, _, reps = s.partition(":")
        for r in (reps.split(",") if reps else ["1"]):
            out.append((cond, int(r)))
    return out


def log_progress(campaign: str, rec: dict) -> None:
    rec = {"timestamp": datetime.now(timezone.utc).isoformat(), **rec}
    with open(RUNS_DIR / f"{campaign}__progress.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")
        f.flush()
        os.fsync(f.fileno())
    print(json.dumps(rec), flush=True)


def interrupted_steps(campaign: str) -> list[dict]:
    """Steps whose last progress record is 'started' (the process died mid-step: power cut, kill, crash)."""
    f = RUNS_DIR / f"{campaign}__progress.jsonl"
    last: dict[tuple, dict] = {}
    if f.exists():
        for line in f.read_text().splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue  # a cut-short last line
            k = (r.get("slug"), r.get("model"), r.get("step"), r.get("condition"), r.get("rep"))
            last[k] = r
    return [r for r in last.values() if r.get("status") == "started"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign", required=True)
    ap.add_argument("--projects", required=True, help="CSV with a `slug` column (results/subset.csv / pilot.csv)")
    ap.add_argument("--model", default=PRIMARY_MODEL)
    ap.add_argument("--external", action="store_true", help="label external APIs (once per project x model)")
    ap.add_argument("--conditions", nargs="*", default=[], help="COND[:rep,rep] ...")
    ap.add_argument("--detect", nargs="*", default=[], help="COND[:rep] ... to run IRIS's CodeQL stage on")
    ap.add_argument("--output", choices=OUTPUT_MODES, default=DEFAULT_OUTPUT,
                    help="free = IRIS free-form + IRIS parse_json (GATE 2 D1a); schema = JSON-schema constrained")
    args = ap.parse_args(argv)

    RUNS_DIR.mkdir(exist_ok=True)
    by_slug = {p.slug: p for p in load_projects()}
    slugs = [r["slug"] for r in csv.DictReader(open(args.projects))]
    plan = parse_condition_reps(args.conditions)
    det = parse_condition_reps(args.detect)
    failures = 0
    for r in interrupted_steps(args.campaign):
        print(f"resuming after an interrupted step: {r['slug']} {r['step']} {r.get('condition')} r{r.get('rep')} "
              f"(started {r['timestamp']})", flush=True)
    for slug in slugs:
        project = by_slug[slug]
        base = {"campaign": args.campaign, "slug": slug, "model": args.model, "output": args.output}
        if not db_exists(slug):
            log_progress(args.campaign, {**base, "step": "db", "status": "missing_db"})
            failures += 1
            continue
        steps = ([("EXT", 1, "external")] if args.external else []) + [(c, r, "internal") for c, r in plan] + \
                [(c, r, "detect") for c, r in det]
        for cond, rep, kind in steps:
            t0 = time.time()
            log_progress(args.campaign, {**base, "step": kind, "condition": cond, "rep": rep, "status": "started"})
            try:
                if kind == "external":
                    s = label_external(project, args.model, args.campaign, args.output)
                    info = {"n_calls": s["n_calls"], "n_parse_failed": s["n_parse_failed"], "labels": s["labels"]}
                elif kind == "internal":
                    s = label_internal(project, cond, rep, args.model, args.campaign, args.output).stats
                    info = {k: s[k] for k in ("n_internal_candidates", "n_calls", "n_parse_failed", "n_source_params",
                                              "items_unmapped")}
                else:
                    s = detect(project, cond, rep, args.model, args.campaign)
                    v = s.get("vanilla_result", {})
                    info = {"recall_method": v.get("recall_method"), "num_paths": v.get("num_paths"),
                            "detection_s": s.get("detection_s")}
                log_progress(args.campaign, {**base, "step": kind, "condition": cond, "rep": rep, "status": "ok",
                                             "s": round(time.time() - t0, 1), **info})
            except OllamaUnavailable as e:  # the server is gone: stop the part (resume later) instead of failing every step
                log_progress(args.campaign, {**base, "step": kind, "condition": cond, "rep": rep, "status": "stopped",
                                             "error": repr(e)[:500]})
                print("STOPPED: Ollama is not answering. Restart it (memory.md §3) and run the same command again.",
                      flush=True)
                return 2
            except Exception as e:  # record and continue with the next step/project
                failures += 1
                log_progress(args.campaign, {**base, "step": kind, "condition": cond, "rep": rep, "status": "error",
                                             "error": repr(e)[:500], "trace": traceback.format_exc()[-1500:]})
                if kind == "external":
                    break  # internal labels need the shared stage; don't cascade errors
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
