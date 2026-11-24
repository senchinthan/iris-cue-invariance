"""One-time README prefetch for a campaign. This is the only Phase 8 step that needs internet; the runs afterwards are offline.

IRIS downloads the fix-commit README from raw.githubusercontent.com on first use (iris.py:636-690) with `requests.get(link)`:
no timeout, and every error is swallowed, after which IRIS tries the next file name, the next commit, and finally the
`master` branch. On a flaky connection that can silently give a different README (or none). Here IRIS's own code runs
unchanged, but `requests.get` is wrapped to (a) add a timeout and (b) record every URL tried and what happened. A README is
kept only if no network error happened before the successful URL, so the result is exactly what IRIS would get on a perfect
connection. It is stored in data/readmes/<slug>.txt (+ .json provenance) and copied into each model's IRIS cache dir.

Usage:  python -m cueinv.prefetch_readmes --projects results/subset.csv --campaign main \
            --models qwen2.5-coder:7b-instruct-q4_K_M qwen3:4b
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from cueinv import RESULTS_DIR
from cueinv import iris_bridge as ib
from cueinv.dataset import load_projects
from cueinv.fsutil import atomic_write_text
from cueinv.label import PRIMARY_MODEL, READMES_DIR, iris_run_id_for, llm_name

TIMEOUT = (10, 60)  # connect, read (seconds)
ATTEMPTS = 3


class Recorder:
    def __init__(self, real_get):
        self.real_get, self.log = real_get, []

    def __call__(self, url, *a, **k):
        k.setdefault("timeout", TIMEOUT)
        try:
            r = self.real_get(url, *a, **k)
            self.log.append({"url": url, "status": r.status_code})
            return r
        except Exception as e:  # IRIS swallows this; we record it
            self.log.append({"url": url, "error": f"{type(e).__name__}: {str(e)[:200]}"})
            raise


def fetch_one(p) -> tuple[str | None, list[dict]]:
    """Run IRIS's own README fetch with a recorder around requests.get. The cache file is moved aside first so IRIS fetches."""
    import requests

    cache = Path(p.label_func_params_log_path) / "readme_head.txt"
    if cache.exists():
        cache.unlink()  # our own campaign's cache file, about to be rewritten from the network
    rec = Recorder(requests.get)
    requests.get = rec
    try:
        text = ib.readme_summary(p)
    finally:
        requests.get = rec.real_get
    return text, rec.log


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--projects", default=str(RESULTS_DIR / "subset.csv"))
    ap.add_argument("--campaign", default="main")
    ap.add_argument("--models", nargs="+", default=[PRIMARY_MODEL])
    args = ap.parse_args(argv)

    by_slug = {p.slug: p for p in load_projects()}
    slugs = [r["slug"] for r in csv.DictReader(open(args.projects))]
    READMES_DIR.mkdir(parents=True, exist_ok=True)
    summary, bad = [], 0
    for slug in slugs:
        project = by_slug[slug]
        stored, meta_f = READMES_DIR / f"{slug}.txt", READMES_DIR / f"{slug}.json"
        pipes = [ib.make_pipeline(project, iris_run_id_for(args.campaign, m), llm_name=llm_name(m)) for m in args.models]
        if meta_f.exists() and (stored.exists() or json.loads(meta_f.read_text()).get("no_readme")):
            meta = json.loads(meta_f.read_text())
            text = None if meta.get("no_readme") else stored.read_text()
        else:
            text, meta = None, None
            for attempt in range(1, ATTEMPTS + 1):
                got, log = fetch_one(pipes[0])
                errors_before = [x for x in log if "error" in x]
                if got is not None and not errors_before:
                    ok_url = next(x["url"] for x in reversed(log) if x.get("status") == 200)
                    text = got
                    meta = {"slug": slug, "source_url": ok_url, "from_fix_commit": "/master/" not in ok_url,
                            "sha256": hashlib.sha256(got.encode()).hexdigest(), "chars": len(got),
                            "fetched_at": datetime.now(timezone.utc).isoformat(), "attempt": attempt, "requests": log}
                    break
                if got is None and not errors_before:  # every URL answered 404: IRIS really has no README here
                    meta = {"slug": slug, "no_readme": True, "source_url": "", "chars": 0, "from_fix_commit": False,
                            "fetched_at": datetime.now(timezone.utc).isoformat(), "requests": log}
                    break
                print(f"  {slug}: attempt {attempt} unreliable ({len(errors_before)} network errors, "
                      f"readme={'yes' if got else 'no'}) — retrying", flush=True)
                time.sleep(5 * attempt)
            if meta is None:
                bad += 1
                summary.append({"slug": slug, "status": "failed", "source_url": "", "chars": 0})
                print(f"FAILED {slug}: {log[-3:]}", flush=True)
                continue
            if text is not None:
                atomic_write_text(stored, text)
            atomic_write_text(meta_f, json.dumps(meta, indent=1))
        if text is not None:
            for p in pipes:  # identical copy in every model's IRIS cache dir
                atomic_write_text(Path(p.label_func_params_log_path) / "readme_head.txt", text)
        status = "no_readme" if meta.get("no_readme") else "ok"
        summary.append({"slug": slug, "status": status, "source_url": meta["source_url"], "chars": meta["chars"]})
        where = "none (all 404)" if status == "no_readme" else ("fix commit" if meta["from_fix_commit"] else "MASTER")
        print(f"{status} {slug}: {meta['chars']} chars from {where}", flush=True)
    with open(RESULTS_DIR / f"readmes_{args.campaign}.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["slug", "status", "source_url", "chars"])
        w.writeheader()
        w.writerows(summary)
    print(f"{len(slugs) - bad}/{len(slugs)} READMEs ready; {bad} failed", flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
