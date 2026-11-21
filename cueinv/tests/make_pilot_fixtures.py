"""Export the pilot projects' internal candidates, README heads and perturbation contexts as test fixtures.

    python -m cueinv.tests.make_pilot_fixtures --campaign pilot-ff
"""
import argparse
import csv
import json
from pathlib import Path

from cueinv import RESULTS_DIR
from cueinv.dataset import load_projects
from cueinv.label import PRIMARY_MODEL, shared_inputs

OUT = Path(__file__).parent / "fixtures" / "pilot"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign", default="pilot-ff")
    a = ap.parse_args()
    by = {p.slug: p for p in load_projects()}
    for r in csv.DictReader(open(RESULTS_DIR / "pilot.csv")):
        p = by[r["slug"]]
        sh = shared_inputs(p, PRIMARY_MODEL, a.campaign)
        d = OUT / p.slug
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "candidates.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["package", "clazz", "func", "signature", "doc"])
            for c in sh.candidates:
                w.writerow([c.package, c.clazz, c.func, c.signature, c.doc])
        (d / "readme_head.txt").write_text(sh.readme or "")
        (d / "context.json").write_text(json.dumps({"owner": sh.owner, "name": sh.name,
                                                   "project_classes": sorted(sh.ctx.project_classes),
                                                   "reserved": sorted(sh.ctx.reserved)}, indent=0))
        print(p.slug, len(sh.candidates), len(sh.ctx.project_classes), len(sh.ctx.reserved))


if __name__ == "__main__":
    main()
