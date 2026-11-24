"""Crash-safe file helpers (Phase 8 runs on a laptop that can lose power mid-write).

* `atomic_write_text`: write to a temp file in the same directory, fsync, then `os.replace` → readers see either the old
  file or the complete new one, never a truncated one.
* `load_json_or_none`: a missing or unparsable file counts as "not done", so the step is recomputed (LLM calls come from
  the cache) instead of crashing the resume.
* `check_artefact` / `quarantine`: IRIS reuses any of its artefacts that exist on disk (iris.py: `if not os.path.exists(...)`).
  A file cut short by a power cut is moved aside to runs/tmp/quarantine/ (never deleted) so that IRIS recomputes it.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from cueinv import CAPSTONE_ROOT, RUNS_DIR

QUARANTINE_DIR = RUNS_DIR / "tmp" / "quarantine"


def _fsync_dir(d: Path) -> None:
    try:
        fd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def atomic_write_text(path: Path | str, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _fsync_dir(path.parent)


def atomic_copy(src: Path | str, dst: Path | str) -> None:
    atomic_write_text(dst, Path(src).read_text())


def load_json_or_none(path: Path | str):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def check_artefact(path: Path | str) -> str | None:
    """None if the file looks complete, else the reason. Only judges files that exist."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        if path.stat().st_size == 0:
            return "empty"
        if path.suffix in (".json", ".sarif"):
            json.loads(path.read_text())
        elif path.suffix == ".csv":
            # CodeQL's `bqrs decode` and pandas both end the file with a newline; a cut-short file usually does not.
            with open(path, "rb") as f:
                f.seek(-1, os.SEEK_END)
                if f.read(1) != b"\n":
                    return "no trailing newline (cut short?)"
    except (OSError, ValueError) as e:
        return f"unreadable: {type(e).__name__}"
    return None


def quarantine(path: Path | str, reason: str, log=print) -> Path:
    """Move a damaged file under runs/tmp/quarantine/<UTC time>/<path relative to the repo>, keeping it for inspection."""
    path = Path(path).resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    try:
        rel = path.relative_to(CAPSTONE_ROOT.resolve())
    except ValueError:
        rel = Path(*path.parts[1:])
    dst = QUARANTINE_DIR / stamp / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(dst))
    log(f"quarantined {path} ({reason}) -> {dst}")
    return dst
