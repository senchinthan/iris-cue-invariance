"""Live view of a Phase 8 part: projects done, current step, LLM call speed, GPU placement, failures, rough ETA.

Usage:  python -m cueinv.run_status [--campaign main] [--once]
Reads runs/<campaign>__progress.jsonl and the run dirs' llm_calls.jsonl (read-only; safe while a run is going).
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from cueinv import RESULTS_DIR, RUNS_DIR


def _jsonl(path: Path) -> list[dict]:
    out = []
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass  # a line being written right now
    return out


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _fmt(sec: float) -> str:
    sec = max(0, int(sec))
    return f"{sec // 3600}h{sec % 3600 // 60:02d}m"


def _running() -> bool:
    return subprocess.run(["pgrep", "-f", r"^\S*python\S* -m cueinv\.run "], capture_output=True).returncode == 0


def snapshot(campaign: str) -> str:
    prog = _jsonl(RUNS_DIR / f"{campaign}__progress.jsonl")
    now = datetime.now(timezone.utc)
    n_projects = len(list(csv.DictReader(open(RESULTS_DIR / "subset.csv"))))
    lines = [f"Phase 8 — campaign {campaign} — {now.astimezone():%Y-%m-%d %H:%M:%S}", ""]
    if not prog:
        return "\n".join(lines + ["nothing logged yet"])
    # group by (model, step kind, condition, rep) = one "run"
    last = {}
    for r in prog:
        last[(r.get("model"), r.get("step"), r.get("condition"), r.get("rep"), r.get("slug"))] = r
    runs = defaultdict(lambda: {"ok": 0, "error": 0, "started": 0, "stopped": 0})
    for (model, step, cond, rep, slug), r in last.items():
        runs[(model, step, cond, rep)][r.get("status", "?")] = runs[(model, step, cond, rep)].get(r.get("status", "?"), 0) + 1
    cur = prog[-1]
    running = _running()
    lines.append(f"job        : {'RUNNING' if running else 'NOT running'}; last event {cur.get('status')} "
                 f"{cur.get('slug', '')[:50]} {cur.get('step')} {cur.get('condition')} r{cur.get('rep')} "
                 f"({_fmt((now - _ts(cur['timestamp'])).total_seconds())} ago)")
    lines.append("runs       : (model / step / condition) — projects ok, errors, of %d" % n_projects)
    for (model, step, cond, rep), c in sorted(runs.items(), key=lambda kv: str(kv[0])):
        lines.append(f"  {model.split(':')[0][:14]:14s} {step:8s} {cond}:r{rep:<3} ok {c['ok']:2d}  err {c['error']:2d}"
                     + ("  <- in progress" if c["started"] else ""))
    # LLM calls of the run in progress
    if cur.get("model") and cur.get("condition"):
        from cueinv.label import run_id_for
        f = RUNS_DIR / run_id_for(campaign, cur["model"], cur["condition"], cur.get("rep", 1)) / "llm_calls.jsonl"
        calls = [r for r in _jsonl(f) if r.get("kind", "call") == "call"]
        if calls:
            recent = calls[-20:]
            tok = sum(r.get("completion_tokens") or 0 for r in recent)
            ev = sum(r.get("eval_s") or 0 for r in recent)
            failed = sum(r.get("parse_status") in ("parse_failed", "truncated", "no_response") for r in calls)
            t_first = _ts(calls[0]["timestamp"])
            lines.append(f"LLM calls  : {len(calls)} in this run ({failed} failed parses); last 20: "
                         f"{(sum(r['wall_s'] for r in recent) / len(recent)):.1f} s/call, {tok / ev if ev else 0:.1f} tok/s; "
                         f"GPU fraction {calls[-1].get('gpu_fraction')}, VRAM {calls[-1].get('vram_used_mib')} MiB")
            key = (cur["model"], cur["step"], cur["condition"], cur.get("rep"))
            done = runs[key]["ok"] + runs[key]["error"]
            el = (now - t_first).total_seconds()
            if done:
                lines.append(f"ETA (rough): {done}/{n_projects} projects in {_fmt(el)} → about "
                             f"{_fmt(el / done * (n_projects - done))} left at this pace (projects differ a lot in size)")
    errs = [r for r in last.values() if r.get("status") == "error"]
    for r in errs[-5:]:
        lines.append(f"error      : {r['slug'][:50]} {r['step']} {r.get('condition')}: {r.get('error', '')[:120]}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign", default="main")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=float, default=30.0)
    a = ap.parse_args(argv)
    while True:
        text = snapshot(a.campaign)
        if a.once:
            print(text)
            return 0
        print("\033[2J\033[H" + text + "\n\n(Ctrl-C to quit; Ctrl-b d to detach from tmux)", flush=True)
        time.sleep(a.interval)


if __name__ == "__main__":
    raise SystemExit(main())
