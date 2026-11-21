"""Phase 5: pilot report -> results/pilot_report.md (+ results/pilot_metrics.json, results/power.json).

Per project: external / internal candidate counts and the internal share; baseline internal source labels; repeat and
reshuffle flip rates and retention; parse-failure rate; mean seconds per call; VRAM peak. Then projections for the full
subset (results/subset.csv) and the power simulation (cueinv/power.py).

Definitions (plan Phase 9): S_c = set of internal parameters (package, class, method, param) labelled source.
  retention(c) = |S_BASE1 ∩ S_c| / |S_BASE1|;  flip(c) = |S_BASE1 △ S_c| / #internal candidate parameters;
  null(p) = min(retention(BASE2), retention(SHUF)).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics as st
from pathlib import Path

from cueinv import RESULTS_DIR, RUNS_DIR
from cueinv.label import PRIMARY_MODEL, run_id_for
from cueinv.power import PilotProject, power_table

# Main-run workload (plan Phases 8): internal LLM runs per project per model, detection runs per project.
PRIMARY_INTERNAL_RUNS = {"BASE": 2, "SHUF": 1, "R_SYN": 1, "R_NEU": 1, "R_NEU2": 1, "R_MIS": 1, "D_STRIP": 1,
                         "D_MIS": 1, "M_README": 1, "M_ID": 1, "COMBO": 1}   # H1C is a vote: no LLM calls
SECOND_INTERNAL_RUNS = {"BASE": 2, "SHUF": 1, "R_NEU": 1, "D_STRIP": 1, "M_README": 1}
DETECTION_RUNS = ("BASE", "SHUF", "COMBO", "H1C", "ABLATION")


def _load(path: Path):
    return json.loads(path.read_text()) if path.exists() else None


def _params(run_id: str, slug: str) -> set | None:
    d = _load(RUNS_DIR / run_id / "labels" / slug / "source_params.json")
    return {tuple(x) for x in d} if d is not None else None


def project_metrics(slug: str, campaign: str, model: str) -> dict:
    rid = lambda c, r: run_id_for(campaign, model, c, r)  # noqa: E731
    ext = _load(RUNS_DIR / rid("EXT", 1) / "labels" / slug / "external.json")
    st1 = _load(RUNS_DIR / rid("BASE", 1) / "labels" / slug / "stats.json")
    st2 = _load(RUNS_DIR / rid("BASE", 2) / "labels" / slug / "stats.json")
    sts = _load(RUNS_DIR / rid("SHUF", 1) / "labels" / slug / "stats.json")
    det = _load(RUNS_DIR / rid("BASE", 1) / "detection" / f"{slug}.json")
    s1, s2, ss = _params(rid("BASE", 1), slug), _params(rid("BASE", 2), slug), _params(rid("SHUF", 1), slug)
    m = {"slug": slug, "complete": all(x is not None for x in (ext, st1, st2, sts))}
    if not m["complete"]:
        return m
    n_int, n_ext, n_par = st1["n_internal_candidates"], ext["n_external_candidates"], st1["n_internal_params"]

    def ret(s):
        return (len(s1 & s) / len(s1)) if s1 else None

    calls = [c for s in (st1, st2, sts) for c in s["calls"]]
    ext_calls = [json.loads(l) for l in open(RUNS_DIR / rid("EXT", 1) / "llm_calls.jsonl")]
    ext_calls = [c for c in ext_calls if c.get("kind") == "call" and c.get("meta", {}).get("slug") == slug]
    all_calls = calls + [{"wall_s": c["wall_s"], "vram_used_mib": c.get("vram_used_mib"),
                          "gpu_fraction": c.get("gpu_fraction"), "parse_status": c["parse_status"]} for c in ext_calls]
    m.update({
        "n_external_candidates": n_ext, "n_internal_candidates": n_int, "n_internal_params": n_par,
        "internal_share_rows": round(n_int / (n_int + n_ext), 4) if (n_int + n_ext) else None,
        "n_internal_calls": st1["n_calls"], "n_external_calls": ext["n_calls"],
        "internal_share_calls": round(st1["n_calls"] / (st1["n_calls"] + ext["n_calls"]), 4),
        "base1_source_params": len(s1), "base1_methods": st1["n_methods_labelled"],
        "base2_source_params": len(s2), "shuf_source_params": len(ss),
        "retention_base2": ret(s2), "retention_shuf": ret(ss),
        "flip_rate_repeat": round(len(s1 ^ s2) / n_par, 4) if n_par else None,
        "flip_rate_reshuffle": round(len(s1 ^ ss) / n_par, 4) if n_par else None,
        "internal_parse_failed_calls": sum(c["parse_status"] != "ok" for c in calls),
        "internal_calls_total": len(calls),
        "external_parse_failed_calls": ext["n_parse_failed"],
        "items_returned": sum(s["items"] for s in (st1, st2, sts)),
        "items_unmapped": sum(s["items_unmapped"] for s in (st1, st2, sts)),
        "params_unmapped": sum(s["params_unmapped"] for s in (st1, st2, sts)),
        "mean_s_internal_call": round(st.mean(c["wall_s"] for c in calls), 2),
        "mean_s_external_call": round(st.mean(c["wall_s"] for c in ext_calls), 2) if ext_calls else None,
        "internal_s_per_condition": round(sum(c["wall_s"] for c in st1["calls"]), 1),
        "external_s": round(sum(c["wall_s"] for c in ext_calls), 1),
        "codeql_candidates_s": ext.get("candidates_s"),
        "vram_peak_mib": max((c.get("vram_used_mib") or 0) for c in all_calls),
        "min_gpu_fraction": min((c.get("gpu_fraction") if c.get("gpu_fraction") is not None else 1.0) for c in all_calls),
        "prompt_tokens_max": max((c.get("prompt_tokens") or 0) for c in calls),
        "external_labels": ext["labels"],
    })
    r2, rs = m["retention_base2"], m["retention_shuf"]
    m["null_retention"] = min(r2, rs) if r2 is not None and rs is not None else None
    if det:
        v = det.get("vanilla_result", {})
        m.update({"detection_s": det.get("detection_s"), "base_recall_method": v.get("recall_method"),
                  "base_num_paths": v.get("num_paths")})
    return m


def _fmt(x, nd=3):
    if x is None:
        return "–"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def projections(ms: list[dict], subset: list[dict], db_rows: dict) -> dict:
    """Two estimators for subset totals: mean-per-project x N, and a Java-SLOC ratio estimator."""
    N = len(subset)
    sloc_sub = sum(int(r["sloc"]) for r in subset)
    sloc_pil = sum(int(next(r for r in subset if r["slug"] == m["slug"])["sloc"]) for m in ms)

    def both(key):
        vals = [m[key] for m in ms]
        return {"mean_x_N": round(st.mean(vals) * N, 1), "sloc_ratio": round(sum(vals) / sloc_pil * sloc_sub, 1)}

    out = {"N_subset": N, "sloc_subset": sloc_sub, "sloc_pilot": sloc_pil}
    for k in ("base1_source_params", "base1_methods", "n_internal_candidates", "n_external_candidates",
              "internal_s_per_condition", "external_s"):
        out[k] = both(k)
    # machine-hours (use the larger of the two estimators, conservative)
    int_h = max(out["internal_s_per_condition"].values()) / 3600
    ext_h = max(out["external_s"].values()) / 3600
    det_s = [m["detection_s"] for m in ms if m.get("detection_s")]
    det_h = (st.mean(det_s) * N / 3600) if det_s else None
    cand_s = [m["codeql_candidates_s"] for m in ms if m.get("codeql_candidates_s")]
    db_s = [float(db_rows[m["slug"]]["pull_s"] or 0) + float(db_rows[m["slug"]]["build_s"] or 0)
            for m in ms if m["slug"] in db_rows and db_rows[m["slug"]].get("build_s")]
    out["hours"] = {
        "per_internal_condition_primary": round(int_h, 2),
        "external_once_primary": round(ext_h, 2),
        "primary_internal_all_conditions": round(int_h * sum(PRIMARY_INTERNAL_RUNS.values()), 2),
        "second_model_internal_all_conditions_assuming_same_speed": round(int_h * sum(SECOND_INTERNAL_RUNS.values()), 2),
        "per_detection_condition": round(det_h, 2) if det_h is not None else None,
        "detection_all": round(det_h * len(DETECTION_RUNS), 2) if det_h is not None else None,
        "codeql_candidate_extraction_once": round(st.mean(cand_s) * N / 3600, 2) if cand_s else None,
        "db_builds_once": round(st.mean(db_s) * N / 3600, 2) if db_s else None,
    }
    h = out["hours"]
    out["hours"]["total"] = round(sum(v for k, v in h.items() if v is not None and k not in
                                      ("per_internal_condition_primary", "per_detection_condition")), 1)
    return out


def write_report(ms: list[dict], proj: dict, power: list[dict], path: Path, campaign: str, model: str) -> None:
    done = [m for m in ms if m["complete"]]
    L = [f"# Pilot report ({campaign}, `{model}`)", "",
         f"Generated by `cueinv/pilot_report.py`. Projects complete: {len(done)}/{len(ms)}. "
         "Definitions: S = internal parameters labelled source; retention(c) = |S_BASE1 ∩ S_c| / |S_BASE1|; "
         "flip(c) = |S_BASE1 △ S_c| / #internal candidate parameters; null = min(retention BASE2, retention SHUF).", "",
         "## Per project", "",
         "| project | ext cand | int cand | int share (rows) | int share (calls) | S_BASE1 params (methods) | ret BASE2 | ret SHUF | null | flip repeat | flip reshuffle | parse-fail calls | unmapped items | s/call int | VRAM peak MiB | BASE recall@method |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for m in ms:
        if not m["complete"]:
            L.append(f"| `{m['slug']}` | incomplete |" + " |" * 15)
            continue
        L.append("| `{slug}` | {ext} | {int} | {sr} | {sc} | {p} ({mm}) | {r2} | {rs} | {nu} | {f2} | {fs} | {pf}/{pt} | {ui}/{ir} | {spc} | {vr} | {rec} |".format(
            slug=m["slug"], ext=m["n_external_candidates"], int=m["n_internal_candidates"],
            sr=_fmt(m["internal_share_rows"]), sc=_fmt(m["internal_share_calls"]), p=m["base1_source_params"],
            mm=m["base1_methods"], r2=_fmt(m["retention_base2"]), rs=_fmt(m["retention_shuf"]),
            nu=_fmt(m["null_retention"]), f2=_fmt(m["flip_rate_repeat"]), fs=_fmt(m["flip_rate_reshuffle"]),
            pf=m["internal_parse_failed_calls"] + m["external_parse_failed_calls"],
            pt=m["internal_calls_total"] + m["n_external_calls"], ui=m["items_unmapped"], ir=m["items_returned"],
            spc=_fmt(m["mean_s_internal_call"], 1), vr=m["vram_peak_mib"], rec=_fmt(m.get("base_recall_method"))))
    if done:
        tot = lambda k: sum(m[k] for m in done)  # noqa: E731
        L += ["", "## Pooled over pilot projects", "",
              f"* Internal share of LLM-labelled rows: {tot('n_internal_candidates')} / "
              f"{tot('n_internal_candidates') + tot('n_external_candidates')} = "
              f"{tot('n_internal_candidates') / (tot('n_internal_candidates') + tot('n_external_candidates')):.3f}",
              f"* Baseline internal source labels: {tot('base1_source_params')} parameters in {tot('base1_methods')} methods",
              f"* Parse failures: {sum(m['internal_parse_failed_calls'] + m['external_parse_failed_calls'] for m in done)} of "
              f"{sum(m['internal_calls_total'] + m['n_external_calls'] for m in done)} calls; unmapped items "
              f"{tot('items_unmapped')} of {tot('items_returned')}; unmapped params {tot('params_unmapped')}",
              f"* Mean s/call: internal {st.mean(m['mean_s_internal_call'] for m in done):.1f}, external "
              f"{st.mean(m['mean_s_external_call'] for m in done if m['mean_s_external_call']):.1f}; "
              f"VRAM peak {max(m['vram_peak_mib'] for m in done)} MiB; min GPU fraction {min(m['min_gpu_fraction'] for m in done)}",
              ""]
    if proj:
        L += ["## Projections for the full subset", "",
              f"N = {proj['N_subset']} projects, {proj['sloc_subset']:,} Java SLOC (pilot {proj['sloc_pilot']:,}).", "",
              "| quantity | mean × N | Java-SLOC ratio |", "|---|---|---|"]
        for k in ("base1_source_params", "base1_methods", "n_internal_candidates", "n_external_candidates"):
            L.append(f"| {k} | {proj[k]['mean_x_N']:,} | {proj[k]['sloc_ratio']:,} |")
        L += ["", "Machine-hours (conservative = larger estimator):", "", "| item | hours |", "|---|---|"]
        for k, v in proj["hours"].items():
            L.append(f"| {k} | {_fmt(v, 2)} |")
        L.append("")
    if power:
        L += ["## Power simulation (`cueinv/power.py`)", "",
              "Project-clustered: N projects drawn from the pilot (label counts, null retention); binomial label noise; "
              "true drop δ (± τ project heterogeneity); percentile bootstrap CI. 'rule' = Δ̂ ≥ 10 pp **and** CI excludes 0.",
              "", "| δ (pp) | τ (pp) | mean Δ̂ | SD Δ̂ | P(CI>0) α=.05 | P(CI>0) α=.05/7 | P(rule) α=.05 | P(rule) α=.05/7 | P(|Δ̂|≤3) |",
              "|---|---|---|---|---|---|---|---|---|"]
        for r in power:
            L.append(f"| {r['delta_pp']:g} | {r['tau_pp']:g} | {r['mean_estimate_pp']} | {r['sd_estimate_pp']} | "
                     f"{r['power_ci_a05']} | {r['power_ci_a05_over_7']} | {r['power_rule_a05']} | "
                     f"{r['power_rule_a05_over_7']} | {r['p_falsified_band']} |")
        L.append("")
    path.write_text("\n".join(L) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign", default="pilot")
    ap.add_argument("--model", default=PRIMARY_MODEL)
    ap.add_argument("--pilot", default=str(RESULTS_DIR / "pilot.csv"))
    ap.add_argument("--subset", default=str(RESULTS_DIR / "subset.csv"))
    ap.add_argument("--n-sims", type=int, default=1000)
    args = ap.parse_args(argv)

    slugs = [r["slug"] for r in csv.DictReader(open(args.pilot))]
    subset = list(csv.DictReader(open(args.subset)))
    db_rows = {}
    if (RESULTS_DIR / "db_builds.csv").exists():
        for r in csv.DictReader(open(RESULTS_DIR / "db_builds.csv")):
            if r["status"] == "built":
                db_rows[r["slug"]] = r
    ms = [project_metrics(s, args.campaign, args.model) for s in slugs]
    done = [m for m in ms if m["complete"]]
    proj = projections(done, subset, db_rows) if done else {}
    power = []
    pil = [PilotProject(m["slug"], m["base1_source_params"], m["retention_base2"], m["retention_shuf"])
           for m in done if m["base1_source_params"] > 0]
    if pil:
        power = power_table(pil, len(subset), n_sims=args.n_sims)
    (RESULTS_DIR / "pilot_metrics.json").write_text(json.dumps({"projects": ms, "projections": proj}, indent=2))
    (RESULTS_DIR / "power.json").write_text(json.dumps(power, indent=2))
    write_report(ms, proj, power, RESULTS_DIR / "pilot_report.md", args.campaign, args.model)
    print((RESULTS_DIR / "pilot_report.md").read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
