import json

from cueinv import pilot_report, power
from cueinv.power import PilotProject


def _pilot(rho=1.0):
    return [PilotProject(f"p{i}", n, rho, rho) for i, n in enumerate((10, 20, 30, 40, 50))]


def test_power_null_rarely_supports_and_large_drop_does():
    pil = _pilot(1.0)
    null = power.simulate(pil, 46, 0.0, n_sims=200, n_boot=500, seed=1)
    big = power.simulate(pil, 46, 15.0, n_sims=200, n_boot=500, seed=1)
    assert null["power_rule_a05"] == 0.0 and null["power_ci_a05"] < 0.1
    assert big["power_ci_a05_over_7"] > 0.95 and big["power_rule_a05_over_7"] > 0.95
    assert abs(big["mean_estimate_pp"] - 15) < 1.5


def test_power_rule_at_threshold_is_about_half():
    """With the support threshold at 10 pp on the point estimate, a true 10-pp drop passes it ~half the time."""
    r = power.simulate(_pilot(1.0), 46, 10.0, n_sims=400, n_boot=500, seed=2)
    assert r["power_ci_a05"] > 0.95
    assert 0.3 < r["power_rule_a05"] < 0.7


def test_power_is_deterministic():
    a = power.simulate(_pilot(0.9), 30, 5.0, n_sims=50, n_boot=200, seed=3)
    b = power.simulate(_pilot(0.9), 30, 5.0, n_sims=50, n_boot=200, seed=3)
    assert a == b


def _write_run(root, run_id, slug, stats=None, params=None, ext=None, calls=None):
    d = root / run_id / "labels" / slug
    d.mkdir(parents=True, exist_ok=True)
    if stats is not None:
        (d / "stats.json").write_text(json.dumps(stats))
    if params is not None:
        (d / "source_params.json").write_text(json.dumps(params))
    if ext is not None:
        (d / "external.json").write_text(json.dumps(ext))
    if calls is not None:
        with open(root / run_id / "llm_calls.jsonl", "w") as f:
            for c in calls:
                f.write(json.dumps(c) + "\n")


def test_project_metrics_definitions(tmp_path, monkeypatch):
    monkeypatch.setattr(pilot_report, "RUNS_DIR", tmp_path)
    slug, model = "o__r_CVE-2020-1_1", "m"
    call = {"batch_index": 0, "batch_size": 2, "parse_status": "ok", "wall_s": 2.0, "gpu_fraction": 1.0,
            "vram_used_mib": 4700, "prompt_tokens": 900, "items": 2, "items_unmapped": 0, "params": 3,
            "params_unmapped": 0}

    def stats(n):
        return {"n_internal_candidates": 4, "n_internal_params": 10, "n_calls": 1, "n_methods_labelled": n,
                "items": 2, "items_unmapped": 1, "params_unmapped": 0, "calls": [call]}

    rid = lambda c, r: pilot_report.run_id_for("pilot", model, c, r)  # noqa: E731
    a, b, c_, d = ["p", "C", "m1", "x"], ["p", "C", "m1", "y"], ["p", "C", "m2", "z"], ["p", "C", "m3", "w"]
    _write_run(tmp_path, rid("BASE", 1), slug, stats(2), [a, b, c_, d])
    _write_run(tmp_path, rid("BASE", 2), slug, stats(2), [a, b, c_, d])        # identical repeat
    _write_run(tmp_path, rid("SHUF", 1), slug, stats(2), [a, b])               # loses 2 of 4
    _write_run(tmp_path, rid("EXT", 1), slug, ext={"n_external_candidates": 36, "n_calls": 2, "n_parse_failed": 0,
                                                   "labels": {}, "candidates_s": 60.0},
               calls=[{"kind": "call", "wall_s": 3.0, "parse_status": "ok", "meta": {"slug": slug}}] * 2)
    m = pilot_report.project_metrics(slug, "pilot", model)
    assert m["complete"]
    assert m["internal_share_rows"] == round(4 / 40, 4)
    assert m["base1_source_params"] == 4
    assert m["retention_base2"] == 1.0 and m["retention_shuf"] == 0.5 and m["null_retention"] == 0.5
    assert m["flip_rate_repeat"] == 0.0 and m["flip_rate_reshuffle"] == 0.2
    assert m["internal_share_calls"] == round(1 / 3, 4)


def test_empirical_model_has_more_variance_when_null_retention_is_bimodal():
    pil = [PilotProject("a", 20, 1.0, 0.0), PilotProject("b", 10, 0.8, 0.8), PilotProject("c", 30, 0.9, 1.0)]
    b = power.simulate(pil, 30, 10.0, n_sims=200, n_boot=300, seed=4, model="binomial")
    e = power.simulate(pil, 30, 10.0, n_sims=200, n_boot=300, seed=4, model="empirical")
    assert e["sd_estimate_pp"] > b["sd_estimate_pp"]
    assert e["power_ci_a05"] <= b["power_ci_a05"]


def test_diag_iris_parse_handles_fenced_output():
    import importlib.util
    import pytest
    if not all(importlib.util.find_spec(m) for m in ("torch", "transformers")):
        pytest.skip("needs the iris env")
    from cueinv.diag_schema import iris_parse
    assert iris_parse('```json\n[{"package": "p", "class": "C"}]\n```') == [{"package": "p", "class": "C"}]
    assert iris_parse("no json here") == []


def test_mean_null_removes_negative_bias_under_no_effect():
    pil = [PilotProject("a", 20, 0.9, 0.6), PilotProject("b", 30, 0.8, 0.7), PilotProject("c", 25, 1.0, 0.5)]
    mn = power.simulate(pil, 30, 0.0, n_sims=300, n_boot=200, seed=5, null_def="min")
    me = power.simulate(pil, 30, 0.0, n_sims=300, n_boot=200, seed=5, null_def="mean")
    assert mn["mean_estimate_pp"] < -3 and abs(me["mean_estimate_pp"]) < 2.5


def test_repeat_null_uses_base2_only():
    pil = [PilotProject("a", 40, 1.0, 0.2), PilotProject("b", 40, 1.0, 0.3)]
    r = power.simulate(pil, 30, 10.0, n_sims=200, n_boot=200, seed=6, null_def="repeat")
    assert abs(r["mean_estimate_pp"] - 10) < 1.0          # SHUF's low retention is ignored
    import pytest
    with pytest.raises(ValueError):
        power.simulate(pil, 30, 10.0, n_sims=10, n_boot=10, model="empirical", null_def="repeat")
