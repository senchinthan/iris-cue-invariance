"""Live view of the DB-build job: download progress + current network speed.

Docker Desktop pulls images on the Windows side, so WSL's own interface does not see the traffic; the speed is read from
the Windows adapters (WiFi/Ethernet, vEthernet excluded) through powershell.exe. The counters include all Windows
traffic, so the per-image percentage is an estimate.

Usage:  python -m cueinv.dl_status            # refreshes every 5 s (Ctrl-C to quit)
        python -m cueinv.dl_status --once
"""

from __future__ import annotations

import argparse
import ast
import csv
import re
import subprocess
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

from cueinv import CAPSTONE_ROOT, RESULTS_DIR

LOG = CAPSTONE_ROOT / "logs" / "dbs" / "build_subset.log"
BUDGET_H, MIN_START_MIN = 5.75, 20
START_RE = re.compile(r"^\[(\d\d:\d\d:\d\d)\] (\S+) \.\.\.$")
JOB_RE = re.compile(r"^JOB START (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", re.M)
PS = ("(Get-NetAdapterStatistics | Where-Object { $_.Name -notlike 'vEthernet*' } "
      "| Measure-Object -Property ReceivedBytes -Sum).Sum")


def last_job_start(text: str) -> datetime | None:
    """Start time of the most recent run (build_dbs prints a JOB START line; older logs do not have one)."""
    found = JOB_RE.findall(text)
    return datetime.strptime(found[-1], "%Y-%m-%d %H:%M:%S") if found else None


def parse_log(text: str) -> tuple[list[tuple[str, str]], dict[str, dict]]:
    """Return ([(hh:mm:ss, slug) in start order], {slug: final record})."""
    starts, recs = [], {}
    for line in text.splitlines():
        m = START_RE.match(line)
        if m:
            starts.append((m.group(1), m.group(2)))
        elif line.startswith("    {"):
            try:
                r = ast.literal_eval(line.strip())
                # a rerun logs already-built projects as "exists"; keep the earlier record (it has the pull time)
                if not (r.get("status") == "exists" and r["slug"] in recs):
                    recs[r["slug"]] = r
            except (ValueError, SyntaxError, KeyError):
                pass
    return starts, recs


def windows_rx_bytes() -> int | None:
    try:
        out = subprocess.run(["powershell.exe", "-NoProfile", "-Command", PS], capture_output=True, text=True, timeout=20)
        return int(out.stdout.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def local_images() -> set[str]:
    out = subprocess.run(["docker", "images", "--format", "{{.Tag}}"], capture_output=True, text=True).stdout
    return set(out.split())


def fmt_td(sec: float) -> str:
    sec = max(0, int(sec))
    return f"{sec // 3600}h{sec % 3600 // 60:02d}m"


def snapshot(sizes: dict[str, float], tags: dict[str, str], rate: float | None, rx_cur: float | None) -> str:
    text = LOG.read_text() if LOG.exists() else ""
    starts, recs = parse_log(text)
    total_gb = sum(sizes.values())
    now = datetime.now()
    lines = [f"DB build job — {now:%H:%M:%S}", ""]
    if not starts:
        return "\n".join(lines + ["job has not started yet"])
    t0 = last_job_start(text)
    if t0 is None:
        t0 = datetime.combine(now.date(), datetime.strptime(starts[0][0], "%H:%M:%S").time())
        if t0 > now:
            t0 -= timedelta(days=1)
    deadline = t0 + timedelta(hours=BUDGET_H)
    pulled = sum(sizes.get(s, 0) for s, r in recs.items() if r.get("pull_s") is not None)
    built = sum(s in sizes and r.get("status") in ("built", "exists") for s, r in recs.items())
    failed = [s for s, r in recs.items() if r.get("status") not in ("built", "exists")]
    cur = starts[-1][1] if starts[-1][1] not in recs else None
    phase = ""
    running = subprocess.run(["pgrep", "-f", r"^\S*python\S* -m cueinv\.build_dbs"], capture_output=True).returncode == 0
    if cur and not running:
        lines.append(f"STATUS     : job is NOT running (stopped while on {cur}); restart it to resume")
        cur = None
    if cur:
        have = tags.get(cur, cur) in local_images() or cur in local_images()
        phase = "building DB" if have else "downloading image"
        if have:
            pulled += sizes.get(cur, 0)
    lines += [f"projects   : {built} built, {len(failed)} failed, of {len(sizes)}   (started {t0:%H:%M}, elapsed {fmt_td((now - t0).total_seconds())})",
              f"downloaded : {pulled:.2f} / {total_gb:.2f} GB  ({100 * pulled / total_gb:.0f} %)  [compressed sizes from Docker Hub]"]
    if cur:
        pos = list(sizes).index(cur) + 1 if cur in sizes else len(starts)
        lines.append(f"current    : #{pos} {cur}  ({sizes.get(cur, 0):.2f} GB) — {phase}, since {starts[-1][0]}")
        if phase == "downloading image" and rx_cur is not None and sizes.get(cur):
            lines.append(f"             ≈ {rx_cur / 1e9:.2f} GB received so far (≈ {min(99, 100 * rx_cur / 1e9 / sizes[cur]):.0f} %)")
    elif not failed and built + sum(r.get("status") == "exists" for r in recs.values()) >= len(sizes):
        lines.append("current    : — (all done)")
    if rate is not None:
        left = total_gb - pulled
        eta = f", remaining {left:.1f} GB ≈ {fmt_td(left * 1e9 / rate)} of pure download time" if rate > 1e4 else ""
        lines.append(f"network    : {rate * 8 / 1e6:.1f} Mbit/s ({rate / 1e6:.2f} MB/s, 30-s average, Windows WiFi/Ethernet){eta}")
    else:
        lines.append("network    : measuring…")
    lines.append(f"budget     : stops starting projects at {deadline - timedelta(minutes=MIN_START_MIN):%H:%M}, hard stop {deadline:%H:%M}")
    if failed:
        lines.append(f"failed     : {', '.join(failed)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=float, default=5.0)
    args = ap.parse_args(argv)
    rows = list(csv.DictReader(open(RESULTS_DIR / "subset.csv")))
    done_before = {r["slug"] for r in csv.DictReader(open(RESULTS_DIR / "pilot.csv"))}
    sizes = {r["slug"]: float(r["image_gb"]) for r in rows if r["slug"] not in done_before}
    tags = {r["slug"]: r["docker_tag"] or r["slug"] for r in rows}
    samples: deque[tuple[float, int]] = deque()
    cur_base: tuple[str | None, int | None] = ("<init>", None)
    first = True
    while True:
        rx = windows_rx_bytes()
        t = time.time()
        if rx is not None:
            samples.append((t, rx))
            while len(samples) > 2 and t - samples[0][0] > 30:
                samples.popleft()
        if args.once and len(samples) == 1:
            time.sleep(3)
            continue
        rate = (samples[-1][1] - samples[0][1]) / (samples[-1][0] - samples[0][0]) if len(samples) >= 2 else None
        starts, recs = parse_log(LOG.read_text() if LOG.exists() else "")
        cur = starts[-1][1] if starts and starts[-1][1] not in recs else None
        if cur != cur_base[0]:
            # rx baseline for the image now being pulled; unknown if the pull had already started when we launched
            cur_base = (cur, None if first else rx)
        first = False
        rx_cur = rx - cur_base[1] if rx is not None and cur_base[1] is not None else None
        text = snapshot(sizes, tags, rate, rx_cur)
        if args.once:
            print(text)
            return 0
        print("\033[2J\033[H" + text + "\n\n(Ctrl-C to quit; Ctrl-b d to detach from tmux)", flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
