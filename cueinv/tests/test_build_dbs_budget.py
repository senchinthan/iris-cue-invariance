import csv

from cueinv import build_dbs


def test_budget_defers_projects_without_dbs(tmp_path, monkeypatch, capsys):
    proj = tmp_path / "p.csv"
    with open(proj, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["slug", "docker_tag", "image_gb"])
        w.writeheader()
        w.writerows([{"slug": "has_db", "docker_tag": "", "image_gb": "0.1"},
                     {"slug": "no_db", "docker_tag": "", "image_gb": "0.1"}])
    built = []
    monkeypatch.setattr(build_dbs, "db_exists", lambda s: s == "has_db")
    monkeypatch.setattr(build_dbs, "build_one", lambda row, *a, **k: built.append(row["slug"]) or {"slug": row["slug"]})
    build_dbs.main(["--projects", str(proj), "--out", str(tmp_path / "o.csv"), "--max-hours", "0.1"])  # 6 min < 20
    assert built == ["has_db"]                       # existing DBs are still recorded; nothing new is started
    assert "1 project(s) not started: no_db" in capsys.readouterr().out


def test_clip_never_exceeds_budget():
    import time
    assert build_dbs._clip(5400, time.time() + 600) <= 600
    assert build_dbs._clip(5400, None) == 5400


def test_dl_status_parses_build_log():
    from cueinv.dl_status import parse_log
    text = ("[16:09:03] a ...\n    {'slug': 'a', 'image_gb': 0.4, 'status': 'built', 'pull_s': 194}\n"
            "[16:14:50] b ...\n")
    starts, recs = parse_log(text)
    assert [s for _, s in starts] == ["a", "b"] and recs["a"]["pull_s"] == 194 and "b" not in recs


def test_dl_status_uses_latest_job_start():
    from cueinv.dl_status import last_job_start
    t = "JOB START 2026-11-21 16:09:03 max_hours=5.75\n[16:09:03] a ...\nJOB START 2026-11-22 09:00:00 max_hours=5.75\n"
    assert last_job_start(t).isoformat() == "2026-11-22T09:00:00" and last_job_start("[16:09:03] a ...") is None
