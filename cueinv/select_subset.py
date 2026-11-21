"""Phase 3: select the study subset and the pilot.

Pool: CWE-22 and CWE-78 CVEs of CWE-Bench-Java (both 'CWE-22' and 'CWE-022' spellings, RECON §9) whose build succeeded.
Filters, applied in this order (the first that fires is the recorded reason):
  1. build_status != success                     -> build_failed
  2. repository is a known giant (GIANTS)        -> giant
  3. no full buggy commit id                     -> no_buggy_commit
  4. IRIS Docker build > MAX_BUILD_MIN minutes   -> build_too_long      (projects without a duration are kept, flagged)
  5. sources cannot be fetched                   -> fetch_failed
  6. Java SLOC (cloc, all Java incl. tests) > MAX_SLOC -> too_large
  7. no image in the IRIS v2 Docker Hub repo (HTTP 404) -> no_docker_image  (plan's Docker policy builds DBs in containers)
SLOC counts all Java including tests because IRIS/CodeQL builds compile test sources (-DskipTests), so the DB contains them.
Outputs: results/subset.csv (kept), results/subset_all.csv (every pool project + reason), results/pilot.csv.

Usage:  python -m cueinv.select_subset [--no-fetch] [--no-hub] [--jobs 4]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import subprocess
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from cueinv import RESULTS_DIR
from cueinv.dataset import Project, load_projects
from cueinv.sources import SOURCES_DIR, FetchError, fetch_sources

MAX_SLOC = 150_000
MAX_BUILD_MIN = 15.0
# Plan list + Payara (a GlassFish-derived application server). Matched against the lower-cased repository name.
GIANTS = ("wildfly", "camel", "flink", "keycloak", "glassfish", "nifi", "aws-sdk-java", "payara")
CLOC = os.environ.get("CLOC", str(Path.home() / "opt" / "cloc" / "cloc"))
HUB_REPO = "irissast/cwe-bench-java-containers-v2"
PILOT_SIZE = 5
PILOT_SEED = 20261121

SUBSET_COLUMNS = ["slug", "cwe", "cve", "cve_year", "sloc", "sloc_main", "sloc_all_langs", "build_minutes",
                  "in_annotated_set", "has_package_names", "docker_tag", "image_gb", "jdk", "mvn", "gradle",
                  "project_info_slug", "repo_url", "buggy_commit"]


def is_giant(p: Project) -> bool:
    name = p.github_repository_name.lower()
    return any(g in name for g in GIANTS)


def cloc_java(src: Path) -> dict:
    """Java code lines (all files, and excluding src/test), plus code lines over all languages."""
    def run(extra: list[str]) -> dict:
        out = subprocess.run([CLOC, "--json", "--quiet", *extra, str(src)], capture_output=True, text=True,
                             check=True, timeout=1800).stdout
        return json.loads(out) if out.strip() else {}

    java_all = run(["--include-lang=Java"]).get("Java", {}).get("code", 0)
    java_main = run(["--include-lang=Java", "--fullpath", r"--not-match-d=(^|/)src/test(/|$)"]).get("Java", {}).get("code", 0)
    all_langs = run([]).get("SUM", {}).get("code", 0)
    return {"sloc": java_all, "sloc_main": java_main, "sloc_all_langs": all_langs}


class ImageNotFound(Exception):
    pass


def hub_image_gb(tag: str) -> float | None:
    """Compressed image size in GB; raises ImageNotFound on HTTP 404; None on any other (network) failure."""
    url = f"https://hub.docker.com/v2/repositories/{HUB_REPO}/tags/{tag}"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return round(json.loads(r.read()).get("full_size", 0) / 1e9, 2)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ImageNotFound(tag) from e
        return None
    except Exception:
        return None


def evaluate(projects: list[Project], fetch: bool, hub: bool, jobs: int) -> list[dict]:
    rows = []
    to_measure = []
    for p in projects:
        row = {**asdict(p), "reason": ""}
        row["fix_commits"] = ";".join(p.fix_commits)
        if p.build_status != "success":
            row["reason"] = "build_failed"
        elif is_giant(p):
            row["reason"] = "giant"
        elif len(p.buggy_commit) < 40:
            row["reason"] = "no_buggy_commit"
        elif p.build_minutes is not None and p.build_minutes > MAX_BUILD_MIN:
            row["reason"] = "build_too_long"
        else:
            to_measure.append((p, row))
        rows.append(row)

    def measure(item):
        p, row = item
        src = SOURCES_DIR / p.slug
        if fetch:
            try:
                src = fetch_sources(p.slug, p.repo_url, p.buggy_commit, patch_slugs=(p.project_info_slug, p.docker_tag))
            except (FetchError, subprocess.TimeoutExpired) as e:
                row["reason"] = "fetch_failed"
                row["error"] = str(e)[:300]
                return
        if not src.exists():
            row["reason"] = "no_sources"
            return
        row.update(cloc_java(src))
        if row["sloc"] > MAX_SLOC:
            row["reason"] = "too_large"
        if hub:
            try:
                row["image_gb"] = hub_image_gb(p.docker_tag)
            except ImageNotFound:
                row["reason"] = row["reason"] or "no_docker_image"

    with ThreadPoolExecutor(max_workers=jobs) as ex:
        list(ex.map(measure, to_measure))
    return rows


def choose_pilot(subset: list[dict], k: int = PILOT_SIZE, seed: int = PILOT_SEED) -> list[dict]:
    """k projects from k SLOC-quantile bins (one per bin), with >=1 CWE-078, >=1 annotated CVE if the subset has one,
    and distinct repositories. Deterministic given (subset, seed)."""
    rng = random.Random(seed)
    ordered = sorted(subset, key=lambda r: (r["sloc"], r["slug"]))
    bins = [ordered[i * len(ordered) // k:(i + 1) * len(ordered) // k] for i in range(k)]
    for b in bins:
        rng.shuffle(b)
    chosen: dict[int, dict] = {}

    def repos():
        return {c["repo_url"] for c in chosen.values()}

    def take(pred) -> bool:
        options = [(i, r) for i, b in enumerate(bins) if i not in chosen for r in b
                   if pred(r) and r["repo_url"] not in repos()]
        if not options:
            return False
        i, r = options[rng.randrange(len(options))]
        chosen[i] = r
        return True

    if any(r["in_annotated_set"] for r in subset):
        take(lambda r: r["in_annotated_set"])
    if not any(c["cwe"] == "CWE-078" for c in chosen.values()):
        take(lambda r: r["cwe"] == "CWE-078")
    for i, b in enumerate(bins):
        if i in chosen:
            continue
        for r in b:
            if r["repo_url"] not in repos():
                chosen[i] = r
                break
    return [chosen[i] for i in sorted(chosen)]


def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true", help="only measure sources already on disk")
    ap.add_argument("--no-hub", action="store_true", help="skip Docker Hub image-size lookups")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--out-dir", default=str(RESULTS_DIR))
    args = ap.parse_args(argv)

    projects = load_projects(("CWE-022", "CWE-078"))
    rows = evaluate(projects, fetch=not args.no_fetch, hub=not args.no_hub, jobs=args.jobs)
    out = Path(args.out_dir)
    write_csv(out / "subset_all.csv", sorted(rows, key=lambda r: (r["reason"], r["slug"])),
              SUBSET_COLUMNS + ["reason", "error"])
    subset = sorted([r for r in rows if not r["reason"]], key=lambda r: r["slug"])
    write_csv(out / "subset.csv", subset, SUBSET_COLUMNS)
    pilot = choose_pilot(subset)
    write_csv(out / "pilot.csv", pilot, SUBSET_COLUMNS)

    from collections import Counter
    print(f"pool={len(rows)} kept={len(subset)} excluded={Counter(r['reason'] for r in rows if r['reason'])}")
    print(f"kept by CWE: {Counter(r['cwe'] for r in subset)}; annotated kept: {sum(r['in_annotated_set'] for r in subset)}")
    print("pilot:")
    for r in pilot:
        print(f"  {r['slug']:70} {r['cwe']} sloc={r['sloc']:>7} build={r['build_minutes']} img={r.get('image_gb')}GB "
              f"annot={r['in_annotated_set']} pkgnames={r['has_package_names']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
