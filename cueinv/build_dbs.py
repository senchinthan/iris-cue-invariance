"""Phase 3.6: build CodeQL databases with IRIS's container route, one project at a time (plan's Docker policy).

Per project: pull the IRIS v2 image -> (re)tag it to the canonical slug if the Docker Hub tag is spelled differently
(RECON §9, patches/0002) -> `scripts/build_codeql_dbs.py --project <slug> --use-container` (IRIS, unmodified) ->
generate data/package-names/<slug>.txt if missing (IRIS's own get_packages_codeql functions) -> `docker rmi` the image.
DBs are kept in iris/data/codeql-dbs/<slug>-docker. Resumable: existing DBs are skipped. Results: results/db_builds.csv.

Usage:  python -m cueinv.build_dbs --projects results/pilot.csv [--max-hours 5.75]

--max-hours is a wall-clock budget for the whole job (the user asked for permission before anything that needs more than
6 h of machine time): no new project starts when less than MIN_START_MIN minutes remain, and every pull/build timeout
is clipped to the time left. Skipped projects are listed at the end and picked up by the next (resumable) run.
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from cueinv import CAPSTONE_ROOT, IRIS_ROOT, RESULTS_DIR, RUNS_DIR

HUB_REPO = "irissast/cwe-bench-java-containers-v2"
DB_ROOT = IRIS_ROOT / "data" / "codeql-dbs"
PKG_DIR = IRIS_ROOT / "data" / "package-names"
GENERATED_PKG_COPY = CAPSTONE_ROOT / "data" / "package-names-generated"
LOG_DIR = CAPSTONE_ROOT / "logs" / "dbs"
MAX_IMAGE_GB = 5.0  # global rule 4: ask before any single download over 5 GB
BUILD_ATTEMPTS = 3
MIN_START_MIN = 20  # do not start a new project with less than this much budget left
FIELDS = ["slug", "docker_tag", "image_gb", "status", "pull_s", "build_s", "db_mb", "package_names", "n_packages",
          "timestamp", "note"]


def db_path(slug: str) -> Path:
    return DB_ROOT / f"{slug}-docker"


def db_exists(slug: str) -> bool:
    return (db_path(slug) / "db-java").is_dir()


def _run(cmd: list[str], log: Path, cwd: Path | None = None, timeout: int | None = None, env=None) -> int:
    with open(log, "a") as f:
        f.write(f"\n$ {' '.join(cmd)}  [{datetime.now(timezone.utc).isoformat()}]\n")
        f.flush()
        try:
            return subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, cwd=cwd, timeout=timeout, env=env).returncode
        except subprocess.TimeoutExpired:
            f.write(f"TIMEOUT after {timeout}s\n")
            return 124


def du_mb(path: Path) -> int:
    out = subprocess.run(["du", "-sm", str(path)], capture_output=True, text=True).stdout.split()
    return int(out[0]) if out else 0


def generate_package_names(slug: str, workdir: Path) -> tuple[str, int]:
    """Use IRIS's own helpers from scripts/get_packages_codeql.py, pointed at the container-built DB."""
    out_file = PKG_DIR / f"{slug}.txt"
    if out_file.exists():
        return "existing", len(out_file.read_text().split())
    for p in (str(IRIS_ROOT),):
        if p not in sys.path:
            sys.path.insert(0, p)
    from scripts import get_packages_codeql as g  # noqa: E402

    src = IRIS_ROOT / "data" / "project-sources" / slug
    group = g.find_maven_group_id(src) or g.find_gradle_group_id(src)
    if not group:
        return "no_group_id", 0
    workdir.mkdir(parents=True, exist_ok=True)
    cwd = os.getcwd()
    os.chdir(workdir)  # run_codeql_query writes results.bqrs into the CWD
    try:
        pkgs = g.run_codeql_query(db_path(slug), IRIS_ROOT / "scripts" / "codeql-queries" / "packages.ql")
    finally:
        os.chdir(cwd)
    internal = sorted(g.filter_internal_packages(pkgs, group))
    if not internal:
        return f"no_packages_for_{group}", 0
    out_file.write_text("".join(f"{x}\n" for x in internal))
    GENERATED_PKG_COPY.mkdir(parents=True, exist_ok=True)
    shutil.copy(out_file, GENERATED_PKG_COPY / out_file.name)
    return f"generated(groupId={group})", len(internal)


def _left(deadline: float | None) -> float:
    return float("inf") if deadline is None else deadline - time.time()


def _clip(seconds: float, deadline: float | None) -> int:
    return max(1, int(min(seconds, _left(deadline))))


def _kill_containers(image: str, log: Path) -> None:
    """A timed-out IRIS build is SIGKILLed before its `finally: container.remove()` runs, so remove its container."""
    ids = subprocess.run(["docker", "ps", "-aq", "--filter", f"ancestor={image}"], capture_output=True, text=True).stdout.split()
    if ids:
        _run(["docker", "rm", "-f", *ids], log)


def build_one(row: dict, timeout_min: int, keep_image: bool, deadline: float | None = None) -> dict:
    slug, tag = row["slug"], row["docker_tag"] or row["slug"]
    image_gb = float(row["image_gb"]) if row.get("image_gb") else None
    rec = {"slug": slug, "docker_tag": tag, "image_gb": image_gb, "timestamp": datetime.now(timezone.utc).isoformat()}
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = LOG_DIR / f"{slug}.log"
    env = {**os.environ, "PATH": f"{IRIS_ROOT / 'codeql'}:{os.environ.get('PATH', '')}"}

    if db_exists(slug):
        rec.update(status="exists", db_mb=du_mb(db_path(slug)))
    else:
        if image_gb is not None and image_gb > MAX_IMAGE_GB:
            rec.update(status="skipped_large_image", note=f"{image_gb} GB > {MAX_IMAGE_GB} GB: ask the user first")
            return rec
        hub, canon = f"{HUB_REPO}:{tag}", f"{HUB_REPO}:{slug}"
        t0 = time.time()
        if _run(["docker", "pull", "-q", hub], log, timeout=_clip(3600, deadline)) != 0:
            rec.update(status="pull_failed", pull_s=round(time.time() - t0))
            return rec
        rec["pull_s"] = round(time.time() - t0)
        if tag != slug:
            _run(["docker", "tag", hub, canon], log)
        # Container DNS is intermittent on some networks (IRIS re-clones the repo inside the container,
        # build_codeql_dbs.py:325-343), so retry the build; the image stays local between attempts.
        code, attempts = 1, 0
        t0 = time.time()
        while attempts < BUILD_ATTEMPTS and not db_exists(slug) and _left(deadline) > 60:
            attempts += 1
            code = _run([sys.executable, "scripts/build_codeql_dbs.py", "--project", slug, "--use-container"], log,
                        cwd=IRIS_ROOT, timeout=_clip(timeout_min * 60, deadline), env=env)
            if code == 124:
                _kill_containers(canon, log)
            if code == 0 and db_exists(slug):
                break
            time.sleep(10)
        rec["build_s"] = round(time.time() - t0)
        rec["status"] = "built" if db_exists(slug) else f"build_failed(rc={code})"
        rec["note"] = f"attempts={attempts}"
        # Plan policy: keep DBs, delete images. A failed build keeps its image so a rerun does not re-download it.
        if db_exists(slug) and not keep_image:
            for img in ({hub, canon} if tag != slug else {hub}):
                _run(["docker", "rmi", "-f", img], log)
        if db_exists(slug):
            rec["db_mb"] = du_mb(db_path(slug))

    if db_exists(slug):
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = env["PATH"]
        try:
            rec["package_names"], rec["n_packages"] = generate_package_names(slug, RUNS_DIR / "tmp" / "pkgnames" / slug)
        finally:
            os.environ["PATH"] = old_path
    return rec


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--projects", default=str(RESULTS_DIR / "pilot.csv"))
    ap.add_argument("--out", default=str(RESULTS_DIR / "db_builds.csv"))
    ap.add_argument("--timeout-min", type=int, default=90)
    ap.add_argument("--keep-image", action="store_true")
    ap.add_argument("--max-hours", type=float, default=None, help="wall-clock budget for the whole job")
    args = ap.parse_args(argv)
    deadline = None if args.max_hours is None else time.time() + args.max_hours * 3600

    rows = list(csv.DictReader(open(args.projects)))
    out = Path(args.out)
    new = not out.exists()
    with open(out, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        if new:
            w.writeheader()
        deferred = []
        for row in rows:
            if not db_exists(row["slug"]) and _left(deadline) < MIN_START_MIN * 60:
                deferred.append(row["slug"])
                continue
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {row['slug']} ...", flush=True)
            rec = build_one(row, args.timeout_min, args.keep_image, deadline)
            w.writerow(rec)
            f.flush()
            print(f"    {rec}", flush=True)
    if deferred:
        print(f"BUDGET REACHED ({args.max_hours} h): {len(deferred)} project(s) not started: {' '.join(deferred)}", flush=True)
    print("DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
