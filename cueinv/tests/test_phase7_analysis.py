import json

import numpy as np
import pytest

from cueinv import analysis
from cueinv.label import run_id_for

M = "m"


def _write(root, cond, rep, slug, params, n_par=10, calls=None, detection=None):
    d = root / run_id_for("t", M, cond, rep) / "labels" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "source_params.json").write_text(json.dumps(sorted(params)))
    (d / "stats.json").write_text(json.dumps({"n_internal_params": n_par, "calls": calls or [
        {"parse_status": "ok", "token_delta_vs_base": 0}], "map_back_failure_rate": 0.0}))
    if detection is not None:
        f = root / run_id_for("t", M, cond, rep) / "detection" / f"{slug}.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"vanilla_result": {"recall_method": detection, "num_paths": 3}}))


def P(*names):
    return {("p", "C", "m", n) for n in names}


def test_definitions_null_is_repeat_and_exclusions(tmp_path):
    _write(tmp_path, "BASE", 1, "a", P("x", "y", "z", "w"))
    _write(tmp_path, "BASE", 2, "a", P("x", "y", "z"))          # null = 0.75
    _write(tmp_path, "R_NEU", 1, "a", P("x", "q"))              # retention 0.25, spurious 1/10
    _write(tmp_path, "SHUF", 1, "a", P("x"))
    _write(tmp_path, "BASE", 1, "b", set())                     # E2: no baseline labels
    _write(tmp_path, "BASE", 2, "b", set())
    _write(tmp_path, "R_NEU", 1, "b", P("x"))
    _write(tmp_path, "BASE", 1, "c", P("x"))
    _write(tmp_path, "BASE", 2, "c", P("x"))
    _write(tmp_path, "R_NEU", 1, "c", P("x"), calls=[{"parse_status": "parse_failed"}] * 3 + [{"parse_status": "ok"}])
    rows = analysis.project_condition_rows("t", M, ["a", "b", "c", "missing"], ("BASE2", "SHUF", "R_NEU"), tmp_path)
    r = {(x["slug"], x["condition"]): x for x in rows}
    a = r[("a", "R_NEU")]
    assert (a["retention"], a["null"], a["spurious"], a["d_pp"]) == (0.25, 0.75, 0.1, 50.0)
    assert r[("a", "BASE2")]["d_pp"] == 0.0
    assert r[("b", "R_NEU")]["excluded"] == "E2_no_baseline_labels"
    assert r[("c", "R_NEU")]["excluded"] == "E3_failed_calls"
    assert not any(x["slug"] == "missing" for x in rows)


def test_holm_step_down():
    h = analysis.holm({"a": 0.001, "b": 0.02, "c": 0.04, "d": 0.5})
    assert h["a"]["rejected"] and h["a"]["alpha_c"] == pytest.approx(0.05 / 4)
    assert not h["b"]["rejected"]           # 0.02 > 0.05/3 -> stop; later ones cannot be rejected
    assert not h["c"]["rejected"] and not h["d"]["rejected"]
    assert h["b"]["p_holm"] == pytest.approx(0.06)


def test_decision_rule():
    assert analysis.decide(12.0, True) == "supported"
    assert analysis.decide(12.0, False) == "inconclusive"
    assert analysis.decide(2.5, False) == "falsified"
    assert analysis.decide(-3.0, False) == "falsified"
    assert analysis.decide(6.0, True) == "inconclusive"


def test_bootstrap_p_and_ci():
    br = analysis.bootstrap_mean([10.0] * 30 + [12.0] * 16, n_boot=2000)
    assert br.p_two_sided < 0.001 and br.ci(0.95)[0] > 0
    z = analysis.bootstrap_mean(list(np.random.default_rng(0).normal(0, 5, 46)), n_boot=2000)
    assert z.p_two_sided > 0.05


def test_mcnemar_exact():
    assert analysis.mcnemar_exact(0, 0) == 1.0
    assert analysis.mcnemar_exact(5, 0) == pytest.approx(2 / 32)
    assert analysis.mcnemar_exact(3, 3) == 1.0


def test_primary_endpoint_family_and_detection(tmp_path):
    rng = np.random.default_rng(1)
    slugs = [f"s{i}" for i in range(20)]
    for s in slugs:
        base = P(*[f"v{j}" for j in range(20)])
        _write(tmp_path, "BASE", 1, s, base, n_par=40, detection=True)
        _write(tmp_path, "BASE", 2, s, base)
        _write(tmp_path, "R_NEU", 1, s, P(*[f"v{j}" for j in range(14)]))           # 30-pp drop
        _write(tmp_path, "D_STRIP", 1, s, base)                                       # no drop
        _write(tmp_path, "COMBO", 1, s, base, detection=bool(rng.random() < 0.5))
        _write(tmp_path, "ABLATION", 1, s, set(), detection=s in slugs[:5])
    rows = analysis.project_condition_rows("t", M, slugs, ("BASE2", "R_NEU", "D_STRIP", "COMBO"), tmp_path)
    prim = {p["condition"]: p for p in analysis.primary_endpoint(rows)}
    assert prim["R_NEU"]["decision"] == "supported" and prim["R_NEU"]["delta_pp"] == 30.0
    assert prim["D_STRIP"]["decision"] == "falsified" and prim["D_STRIP"]["equivalence_90ci_within_3pp"]
    assert prim["COMBO"]["decision"] == "not in confirmatory family"
    det = analysis.detection_endpoint("t", M, slugs, tmp_path)
    assert det["per_condition"]["BASE"] == {"n": 20, "detected": 20}
    assert det["ablation"]["share_needing_internal_sources"] == pytest.approx(15 / 20)


def test_h1c_heldout_robustness(tmp_path):
    for s in ("a", "b", "c"):
        _write(tmp_path, "BASE", 1, s, P("x", "y", "z", "w"))
        _write(tmp_path, "H1C", 1, s, P("x", "y"))
        for c in analysis.H1C_HELDOUT:
            _write(tmp_path, c, 1, s, P("x", "y"))      # H1C labels all survive, BASE keeps 2 of 4
    r = analysis.h1c_robustness("t", M, ["a", "b", "c"], tmp_path)
    assert r["n_projects"] == 3 and r["effect_pp"] == 50.0


def test_readme_vuln_covariate():
    assert analysis.readme_mentions_vuln("Important - There is a vulnerability in older versions of Spark")
    assert not analysis.readme_mentions_vuln("A tiny web framework")
