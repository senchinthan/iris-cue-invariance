"""Diagnostic (pilot, GATE 2 input): does the JSON-schema constraint change internal labelling?

For each project, re-send the *identical* BASE internal prompts without `format` (IRIS's own behaviour: free-form text,
parsed by IRIS's `parse_json`, iris.py:484-526) and compare with the schema-constrained BASE r1 of the pilot.
Same decoding otherwise (temperature 0, top_k 1, num_ctx 8192, ...). Writes results/diag_schema.json.

    python -m cueinv.diag_schema --projects results/pilot.csv
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import io
import json

from cueinv import RESULTS_DIR, RUNS_DIR
from cueinv import iris_bridge as ib
from cueinv.dataset import load_projects
from cueinv.label import PRIMARY_MODEL, build_messages, condition_seed, map_back, run_id_for, shared_inputs
from cueinv.llm import OllamaClient
from cueinv.perturb import make_view


class _Log:
    def info(self, *a, **k):
        pass

    error = info


def iris_parse(text: str) -> list:
    ib.ensure_iris_importable()
    from src.iris import SAPipeline

    p = SAPipeline.__new__(SAPipeline)
    p.project_logger = _Log()
    with contextlib.redirect_stdout(io.StringIO()):
        return p.parse_json(text or "")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--projects", default=str(RESULTS_DIR / "pilot.csv"))
    ap.add_argument("--campaign", default="pilot")
    ap.add_argument("--model", default=PRIMARY_MODEL)
    args = ap.parse_args(argv)
    by_slug = {p.slug: p for p in load_projects()}
    run_id = run_id_for("diag", args.model, "BASE_FREEFORM", 1)
    client = OllamaClient(args.model, run_id)
    out = []
    for r in csv.DictReader(open(args.projects)):
        proj = by_slug[r["slug"]]
        sh = shared_inputs(proj, args.model, args.campaign)
        view = make_view(sh.candidates, sh.readme, sh.owner, sh.name, "BASE", condition_seed(proj, "BASE"))
        free_params, empty_batches, items = set(), 0, 0
        for bi, b in enumerate(view.batches):
            res = client.chat(build_messages(view, b), schema=None, condition="BASE_FREEFORM", rep=1,
                              meta={"slug": proj.slug, "batch_index": bi})
            parsed = [x for x in iris_parse(res.content) if isinstance(x, dict)]
            items += len(parsed)
            empty_batches += (len(parsed) == 0)
            found, _ = map_back(view, b, parsed)
            free_params |= {(k[0], k[1], k[2], p) for k, v in found.items() for p in v}
        schema_stats = json.loads((RUNS_DIR / run_id_for(args.campaign, args.model, "BASE", 1) / "labels" / proj.slug /
                                   "stats.json").read_text())
        schema_params = {tuple(x) for x in json.loads((RUNS_DIR / run_id_for(args.campaign, args.model, "BASE", 1) /
                                                       "labels" / proj.slug / "source_params.json").read_text())}
        schema_empty = sum(1 for c in schema_stats["calls"] if c["items"] == 0)
        rec = {"slug": proj.slug, "n_batches": len(view.batches),
               "schema_empty_batches": schema_empty, "schema_source_params": len(schema_params),
               "free_empty_batches": empty_batches, "free_items": items, "free_source_params": len(free_params),
               "overlap": len(schema_params & free_params)}
        print(json.dumps(rec), flush=True)
        out.append(rec)
    (RESULTS_DIR / "diag_schema.json").write_text(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
