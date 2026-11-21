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
