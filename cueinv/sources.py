"""Fetch project sources at the buggy commit into iris/data/project-sources/<slug>.

Same end state as IRIS's `scripts/fetch_one.py` (buggy commit checked out, `data/patches/<slug>.patch` applied), but it fetches
only that one commit (`git fetch --depth 1 origin <sha>`) instead of first cloning the default branch, which halves the download.
Sources are needed for cloc (Phase 3) and for IRIS's println post-filter (iris.py:1120-1133, RECON §7).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from cueinv import IRIS_ROOT, RUNS_DIR

SOURCES_DIR = IRIS_ROOT / "data" / "project-sources"
IRIS_PATCH_DIR = IRIS_ROOT / "data" / "patches"
# Partial fetches are staged here (rule 4: only delete inside runs/tmp/), then moved into place when complete.
STAGING_DIR = RUNS_DIR / "tmp" / "fetch"


class FetchError(RuntimeError):
    pass


def _git(args: list[str], cwd: Path, timeout: int) -> None:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise FetchError(f"git {' '.join(args)} failed: {r.stderr.strip()[-500:]}")


def fetch_sources(slug: str, repo_url: str, commit: str, *, patch_slugs: tuple[str, ...] = (),
                  timeout: int = 1800, dest_root: Path = SOURCES_DIR) -> Path:
    """Idempotent: returns immediately if the directory already holds a checkout (like fetch_one.py:36-38)."""
    if not commit or len(commit) < 40:
        raise FetchError(f"{slug}: no full buggy commit id ({commit!r})")
    dest = Path(dest_root) / slug
    if (dest / ".git").exists() and any(p for p in dest.iterdir() if p.name != ".git"):
        return dest
    tmp = STAGING_DIR / f"{slug}.partial"
    if tmp.exists():
        subprocess.run(["rm", "-rf", "--", str(tmp)], check=True)
    tmp.mkdir(parents=True)
    _git(["init", "-q"], tmp, 60)
    _git(["remote", "add", "origin", repo_url], tmp, 60)
    _git(["fetch", "-q", "--depth", "1", "--no-tags", "origin", commit], tmp, timeout)
    _git(["checkout", "-q", "FETCH_HEAD"], tmp, 600)
    for ps in (slug, *patch_slugs):
        patch = IRIS_PATCH_DIR / f"{ps}.patch"
        if patch.exists():
            _git(["apply", str(patch)], tmp, 120)
            break
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp.rename(dest)
    return dest
