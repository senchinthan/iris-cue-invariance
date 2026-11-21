import csv
import subprocess

import pytest

from cueinv import IRIS_ROOT, dataset, select_subset, sources
from cueinv.dataset import Project, cve_of, iris_parsable, norm_cwe


def test_norm_cwe_and_cve_parsing():
    assert norm_cwe("CWE-22") == norm_cwe("CWE-022") == "CWE-022"
    assert norm_cwe("CWE-78") == "CWE-078"
    assert cve_of("jena__CVE-2025-49656_jena-5.4.0") == "CVE-2025-49656"
    assert iris_parsable("perwendel__spark_CVE-2018-9159_2.7.1")
    assert iris_parsable("apache__jena_CVE-2025-49656_jena-5.4.0")
    assert not iris_parsable("jena__CVE-2025-49656_jena-5.4.0")
    assert not iris_parsable("jena_CVE-2025-49656_jena-5.4.0")
    assert dataset.iris_owner_name("perwendel__spark_CVE-2018-9159_2.7.1") == ("perwendel", "spark")


def test_pool_is_73_with_canonical_slugs():
    ps = dataset.load_projects()
    assert len(ps) == 73
    assert sum(p.cwe == "CWE-022" for p in ps) == 60 and sum(p.cwe == "CWE-078" for p in ps) == 13
    assert all(iris_parsable(p.slug) for p in ps)
    by_cve = {p.cve: p for p in ps}
    jena = by_cve["CVE-2025-49656"]
    assert (jena.slug, jena.docker_tag, jena.project_info_slug) == (
        "apache__jena_CVE-2025-49656_jena-5.4.0", "jena_CVE-2025-49656_jena-5.4.0", "jena__CVE-2025-49656_jena-5.4.0")
    assert {p.cve for p in ps if p.in_annotated_set} == {
        "CVE-2025-49656", "CVE-2025-53622", "CVE-2025-26074", "CVE-2025-46096"}


def test_data_patch_0002_makes_iris_files_consistent():
    """After patches/0002, every pool slug in the working-tree project_info/build_info is IRIS-parsable and matches fix_info."""
    pinfo = {r["cve_id"]: r["project_slug"] for r in csv.DictReader(open(IRIS_ROOT / "data" / "project_info.csv"))}
    binfo = {cve_of(r["project_slug"]): r["project_slug"] for r in csv.DictReader(open(IRIS_ROOT / "data" / "build_info.csv"))}
    for p in dataset.load_projects():
        if p.cve == "CVE-2025-24961":  # s3proxy: no buggy commit, excluded, deliberately not normalised
            continue
        assert pinfo[p.cve] == p.slug, p.cve
        assert binfo[p.cve] == p.slug, p.cve
        assert p.slug.split("_")[3] == p.cve  # what SAPipeline does at iris.py:148


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_fetch_sources_checks_out_commit_and_applies_patch(tmp_path, monkeypatch):
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q")
    _git(origin, "config", "uploadpack.allowReachableSHA1InWant", "true")
    (origin / "A.java").write_text("class A {}\n")
    _git(origin, "add", ".")
    _git(origin, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "c1")
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=origin, capture_output=True, text=True).stdout.strip()
    (origin / "A.java").write_text("class A { int later; }\n")
    _git(origin, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "c2")

    patch_dir = tmp_path / "patches"
    patch_dir.mkdir()
    (patch_dir / "o__r_CVE-2020-1_1.patch").write_text(
        "--- a/A.java\n+++ b/A.java\n@@ -1 +1 @@\n-class A {}\n+class A { /* patched */ }\n")
    monkeypatch.setattr(sources, "IRIS_PATCH_DIR", patch_dir)
    monkeypatch.setattr(sources, "STAGING_DIR", tmp_path / "staging")

    dest = sources.fetch_sources("o__r_CVE-2020-1_1", f"file://{origin}", sha, dest_root=tmp_path / "src")
    assert (dest / "A.java").read_text() == "class A { /* patched */ }\n"
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=dest, capture_output=True, text=True).stdout.strip()
    assert head == sha
    # idempotent: second call is a no-op
    assert sources.fetch_sources("o__r_CVE-2020-1_1", "file:///nonexistent", sha, dest_root=tmp_path / "src") == dest


def test_fetch_sources_rejects_missing_commit(tmp_path):
    with pytest.raises(sources.FetchError):
        sources.fetch_sources("x__y_CVE-2020-1_1", "file:///x", "", dest_root=tmp_path)


def _proj(i, **kw):
    base = dict(slug=f"o{i}__r{i}_CVE-2020-{i}_1", project_info_slug="", docker_tag="", cve=f"CVE-2020-{i}",
                cwe="CWE-022", cve_year=2020, github_username=f"o{i}", github_repository_name=f"r{i}",
                repo_url=f"https://github.com/o{i}/r{i}", buggy_commit="a" * 40, build_status="success",
                build_minutes=2.0)
    base.update(kw)
    return Project(**base)


def test_evaluate_applies_filters_in_order(tmp_path, monkeypatch):
    monkeypatch.setattr(select_subset, "SOURCES_DIR", tmp_path)
    ps = [_proj(1, build_status="failed"), _proj(2, github_repository_name="camel"), _proj(3, buggy_commit=""),
          _proj(4, build_minutes=50.0), _proj(5)]
    rows = {r["cve"]: r["reason"] for r in select_subset.evaluate(ps, fetch=False, hub=False, jobs=1)}
    assert rows == {"CVE-2020-1": "build_failed", "CVE-2020-2": "giant", "CVE-2020-3": "no_buggy_commit",
                    "CVE-2020-4": "build_too_long", "CVE-2020-5": "no_sources"}


def test_choose_pilot_constraints_and_determinism():
    subset = []
    for i in range(20):
        subset.append({"slug": f"s{i}", "sloc": 1000 * (i + 1), "cwe": "CWE-078" if i in (3, 17) else "CWE-022",
                       "in_annotated_set": i == 12, "repo_url": f"r{i // 2}"})
    a = select_subset.choose_pilot(subset)
    b = select_subset.choose_pilot(subset)
    assert [r["slug"] for r in a] == [r["slug"] for r in b]
    assert len(a) == 5
    assert any(r["cwe"] == "CWE-078" for r in a)
    assert any(r["in_annotated_set"] for r in a)
    assert len({r["repo_url"] for r in a}) == 5
    # one per SLOC quintile
    assert sorted((r["sloc"] - 1) // 4000 for r in a) == [0, 1, 2, 3, 4]
