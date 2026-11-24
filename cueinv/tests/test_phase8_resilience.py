"""Phase 8 resume safety: power cut mid-write, damaged IRIS artefacts, README offline handling."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cueinv import fsutil, label, llm, run


@pytest.fixture(autouse=True)
def _quarantine_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(fsutil, "QUARANTINE_DIR", tmp_path / "quarantine")


def _rec(i):
    return {"kind": "call", "prompt_sha256": f"h{i}", "model": "m", "condition": "BASE", "rep": 1, "response": i}


def test_atomic_write_and_safe_load(tmp_path):
    f = tmp_path / "a" / "x.json"
    fsutil.atomic_write_text(f, json.dumps({"ok": 1}))
    assert fsutil.load_json_or_none(f) == {"ok": 1}
    assert not [p for p in f.parent.iterdir() if p.name.endswith(".tmp")]
    f.write_text('{"ok": ')                                   # cut short
    assert fsutil.load_json_or_none(f) is None
    assert fsutil.load_json_or_none(tmp_path / "missing.json") is None


def test_call_cache_drops_cut_short_last_line(tmp_path):
    f = tmp_path / "llm_calls.jsonl"
    f.write_text(json.dumps(_rec(1)) + "\n" + json.dumps(_rec(2)) + "\n" + '{"kind": "call", "prompt_sha')
    c = llm.CallCache(f)
    assert len(c) == 2 and c.dropped_tail.startswith(b'{"kind"')
    c.append(_rec(3))                                          # starts on a fresh line, not glued to the stub
    assert [json.loads(x)["response"] for x in f.read_text().splitlines()] == [1, 2, 3]
    assert len(llm.CallCache(f)) == 3


def test_call_cache_complete_json_without_newline_is_unfinished(tmp_path):
    f = tmp_path / "llm_calls.jsonl"
    f.write_text(json.dumps(_rec(1)) + "\n" + json.dumps(_rec(2)))
    c = llm.CallCache(f)
    assert len(c) == 1 and f.read_text().endswith("\n")


def test_call_cache_corrupt_middle_line_raises(tmp_path):
    f = tmp_path / "llm_calls.jsonl"
    f.write_text(json.dumps(_rec(1)) + "\n" + "garbage\n" + json.dumps(_rec(2)) + "\n")
    with pytest.raises(ValueError, match="corrupt line 2"):
        llm.CallCache(f)


def test_check_artefact_and_quarantine(tmp_path):
    good, cut, empty, sarif = tmp_path / "a.csv", tmp_path / "b.csv", tmp_path / "c.json", tmp_path / "d.sarif"
    good.write_text("h1,h2\n1,2\n")
    cut.write_text("h1,h2\n1,")
    empty.write_text("")
    sarif.write_text('{"runs": [')
    assert fsutil.check_artefact(good) is None and fsutil.check_artefact(tmp_path / "nope.csv") is None
    assert "cut short" in fsutil.check_artefact(cut)
    assert fsutil.check_artefact(empty) == "empty"
    assert "unreadable" in fsutil.check_artefact(sarif)
    dst = fsutil.quarantine(cut, "test", log=lambda *_: None)
    assert not cut.exists() and dst.read_text() == "h1,h2\n1,"


def test_check_iris_artefacts_moves_only_damaged(tmp_path):
    from cueinv import iris_bridge as ib

    ok, bad = tmp_path / "ext.csv", tmp_path / "out.sarif"
    ok.write_text("a\n1\n")
    bad.write_text("{")
    p = SimpleNamespace(external_apis_csv_path=str(ok), query_output_result_sarif_path=str(bad),
                        label_func_params_log_path=str(tmp_path))
    assert ib.check_iris_artefacts(p, log=lambda *_: None) == ["query_output_result_sarif_path"]
    assert ok.exists() and not bad.exists()


def test_interrupted_steps(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "RUNS_DIR", tmp_path)
    base = {"slug": "s", "model": "m", "step": "internal", "condition": "BASE", "rep": 1}
    lines = [{**base, "status": "started"}, {**base, "status": "ok"},
             {**base, "condition": "SHUF", "status": "started"}]
    (tmp_path / "c__progress.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines) + '{"slug": "s", "st')
    got = run.interrupted_steps("c")
    assert [(r["condition"], r["status"]) for r in got] == [("SHUF", "started")]


def _pipe(tmp_path):
    d = tmp_path / "iris_run" / "logs" / "label_func_params"
    d.mkdir(parents=True)
    return SimpleNamespace(label_func_params_log_path=str(d), api_labels_cache_path=str(tmp_path / "common" / "r" / "cwe-022" / "api.json"))


def test_ensure_readme_uses_prefetched_copy_offline(tmp_path, monkeypatch):
    monkeypatch.setattr(label, "READMES_DIR", tmp_path / "readmes")
    (tmp_path / "readmes").mkdir()
    (tmp_path / "readmes" / "s.txt").write_text("A tiny web framework")
    p = _pipe(tmp_path)
    (Path(p.label_func_params_log_path) / "readme_head.txt").write_text("A tiny web fr")   # cut short earlier
    monkeypatch.setattr(label.ib, "readme_summary",
                        lambda pp: (Path(pp.label_func_params_log_path) / "readme_head.txt").read_text())
    assert label.ensure_readme(p, "s") == "A tiny web framework"


def test_ensure_readme_refuses_to_prompt_without_readme(tmp_path, monkeypatch):
    monkeypatch.setattr(label, "READMES_DIR", tmp_path / "readmes")
    monkeypatch.setattr(label.ib, "readme_summary", lambda pp: None)        # network down
    with pytest.raises(label.ReadmeUnavailable):
        label.ensure_readme(_pipe(tmp_path), "s")
    (tmp_path / "readmes").mkdir()
    (tmp_path / "readmes" / "s.json").write_text(json.dumps({"no_readme": True}))  # genuinely none (all 404)
    assert label.ensure_readme(_pipe(tmp_path / "x"), "s") is None


def test_api_cache_snapshot_restores_damaged_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(label, "RUNS_DIR", tmp_path / "runs")
    p = _pipe(tmp_path)
    cache = Path(p.api_labels_cache_path)
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps([{"package": "a"}]))
    label.snapshot_api_cache(p, "c", "m")                                  # after a completed project
    cache.write_text('[{"package": "a"}, {"pack')                          # power cut while IRIS rewrote it
    label.restore_api_cache(p, "c", "m")
    assert json.loads(cache.read_text()) == [{"package": "a"}]


def test_prefetch_recorder_marks_network_errors(monkeypatch):
    from cueinv import prefetch_readmes as pr

    def fake_get(url, timeout=None):
        if "README.md" in url:
            raise ConnectionError("timed out")
        return SimpleNamespace(status_code=200 if "README.adoc" in url else 404)

    r = pr.Recorder(fake_get)
    with pytest.raises(ConnectionError):
        r("https://x/README.md")
    r("https://x/README.adoc")
    assert [("error" in x, x.get("status")) for x in r.log] == [(True, None), (False, 200)]


MSGS = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]


def test_no_reply_is_retried_not_cached(tmp_path):
    replies = [OSError("connection refused"), {"error": "model unloading"}, {"message": {"content": "[]"}},  # warm-up
               OSError("timed out"), {"message": {"content": "[]"}}]                                           # the call

    def transport(body):
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    c = llm.OllamaClient("m", "r", runs_dir=tmp_path, transport=transport, probe=lambda: True, poll_s=0)
    out = c.chat(MSGS, schema=None, condition="BASE", rep=1)
    assert out.parse_status == "ok" and out.record["attempts"][0]["server_retries"] == 1 and not replies
    assert len(llm.CallCache(tmp_path / "r" / "llm_calls.jsonl")) == 1


def test_ollama_gone_raises_and_caches_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "SERVER_MAX_WAIT_S", 0)
    def transport(body):
        raise OSError("connection refused")

    c = llm.OllamaClient("m", "r", runs_dir=tmp_path, transport=transport, probe=lambda: False, poll_s=0)
    with pytest.raises(llm.OllamaUnavailable):
        c.chat(MSGS, schema=None, condition="BASE", rep=1)
    f = tmp_path / "r" / "llm_calls.jsonl"
    assert not f.exists() or len(llm.CallCache(f)) == 0
