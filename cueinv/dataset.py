"""CWE-Bench-Java metadata as shipped with IRIS, with the slug inconsistencies resolved (docs/RECON.md §9).

All reads come from the *committed* IRIS files (`git show HEAD:data/...`), so local data patches
(patches/0002-*) cannot change what the selection sees.

Three spellings exist for the 2025 additions, e.g. CVE-2025-49656:
  project_info.csv           jena__CVE-2025-49656_jena-5.4.0        (breaks slug.split("_")[3])
  build_info.csv / Docker    jena_CVE-2025-49656_jena-5.4.0         (breaks slug.split("_")[3])
  fix_info.csv               apache__jena_CVE-2025-49656_jena-5.4.0 (IRIS-parsable: owner__repo_CVE_tag)
We use the IRIS-parsable spelling as the canonical `slug` everywhere and keep the Docker Hub tag separately.
"""

from __future__ import annotations

import csv
import io
import re
import subprocess
from dataclasses import dataclass, field

from cueinv import IRIS_ROOT

CVE_RE = re.compile(r"CVE-\d{4}-\d+")
CWE_RE = re.compile(r"^CWE-0*(\d+)$")


def read_iris_csv(relpath: str, ref: str = "HEAD") -> list[dict]:
    text = subprocess.run(["git", "-C", str(IRIS_ROOT), "show", f"{ref}:{relpath}"], capture_output=True, text=True,
                          check=True).stdout
    return list(csv.DictReader(io.StringIO(text)))


def norm_cwe(cwe: str) -> str:
    """'CWE-22' / 'CWE-022' -> 'CWE-022' (IRIS query naming uses 3 digits)."""
    m = CWE_RE.match(cwe.strip())
    if not m:
        return cwe.strip()
    return f"CWE-{int(m.group(1)):03d}"


def cve_of(slug: str) -> str | None:
    m = CVE_RE.search(slug)
    return m.group(0) if m else None


def iris_parsable(slug: str) -> bool:
    """True if IRIS's own parsing works: cve = split('_')[3], owner = [0], repo = [2] (iris.py:148, :731-732)."""
    parts = slug.split("_")
    return len(parts) > 3 and parts[1] == "" and parts[3].startswith("CVE-") and parts[3] == cve_of(slug)


def iris_owner_name(slug: str) -> tuple[str, str]:
    parts = slug.split("_")
    return parts[0], parts[2]


@dataclass
class Project:
    slug: str                      # canonical, IRIS-parsable
    project_info_slug: str         # spelling in project_info.csv
    docker_tag: str                # tag in irissast/cwe-bench-java-containers-v2
    cve: str
    cwe: str                       # normalised CWE-0XX
    cve_year: int
    github_username: str
    github_repository_name: str
    repo_url: str
    buggy_commit: str
    fix_commits: list[str] = field(default_factory=list)
    build_status: str | None = None
    jdk: str | None = None
    mvn: str | None = None
    gradle: str | None = None
    build_minutes: float | None = None
    in_annotated_set: bool = False
    has_package_names: bool = False

    @property
    def query(self) -> str:
        return f"cwe-{self.cwe.split('-')[1]}wLLM"


def load_projects(cwes: tuple[str, ...] = ("CWE-022", "CWE-078")) -> list[Project]:
    cwes = tuple(norm_cwe(c) for c in cwes)
    pinfo = read_iris_csv("data/project_info.csv")
    binfo = read_iris_csv("data/build_info.csv")
    durs = read_iris_csv("data/IRIS_docker_build_durations.csv")
    fixinfo = read_iris_csv("data/fix_info.csv")
    annotated = read_iris_csv("data/fix_info_source_sink.csv")

    build_by_cve = {cve_of(r["project_slug"]): r for r in binfo if cve_of(r["project_slug"])}
    dur_by_cve = {cve_of(r["project-slug"]): r for r in durs if cve_of(r["project-slug"])}
    fix_slug_by_cve = {}
    for r in fixinfo:
        c = cve_of(r["project_slug"])
        if c and iris_parsable(r["project_slug"]):
            fix_slug_by_cve.setdefault(c, r["project_slug"])
    annotated_cves = {r["cve_id"] for r in annotated}
    pkg_dir = IRIS_ROOT / "data" / "package-names"

    out = []
    for r in pinfo:
        cwe = norm_cwe(r["cwe_id"])
        if cwe not in cwes:
            continue
        cve = r["cve_id"]
        if iris_parsable(r["project_slug"]):
            slug = r["project_slug"]
        elif cve in fix_slug_by_cve:
            slug = fix_slug_by_cve[cve]
        else:
            slug = f"{r['github_username']}__{r['github_repository_name']}_{cve}_{r['github_tag']}"
        b = build_by_cve.get(cve, {})
        d = dur_by_cve.get(cve)
        out.append(Project(
            slug=slug,
            project_info_slug=r["project_slug"],
            docker_tag=b.get("project_slug") or r["project_slug"],
            cve=cve,
            cwe=cwe,
            cve_year=int(cve.split("-")[1]),
            github_username=r["github_username"],
            github_repository_name=r["github_repository_name"],
            repo_url=r["github_url"],
            buggy_commit=r["buggy_commit_id"].strip(),
            fix_commits=[c for c in r["fix_commit_ids"].split(";") if c],
            build_status=b.get("status"),
            jdk=b.get("jdk_version") or None,
            mvn=b.get("mvn_version") or None,
            gradle=b.get("gradle_version") or None,
            build_minutes=float(d["duration-minutes"]) if d and d.get("duration-minutes") else None,
            in_annotated_set=cve in annotated_cves,
            has_package_names=(pkg_dir / f"{slug}.txt").exists(),
        ))
    return out
