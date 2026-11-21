"""Confirmatory analysis for the primary endpoint and the detection endpoint (frozen with docs/PREREG.md, tag prereg-v1).

Unit of analysis: project p. A label is an internal parameter (package, class, method, param) labelled source;
S_c(p) is the set for condition c (rep 1 unless stated).

  retention_p(c) = |S_BASE1 ∩ S_c| / |S_BASE1|            (projects with |S_BASE1| >= 1, rule E2)
  spurious_p(c)  = |S_c \\ S_BASE1| / #internal candidate parameters
  null(p)        = retention_p(BASE2)                       (GATE 2 decision D2: repeat control is the noise floor)
  d_p(c)         = null(p) - retention_p(c)                 (in percentage points)
  Delta(c)       = mean_p d_p(c)

Inference per channel c in the confirmatory family (7 channels: realistic R_SYN, D_STRIP, M_README; stress R_NEU,
R_NEU2, R_MIS, D_MIS): paired project-level percentile bootstrap of Delta(c) (10,000 resamples, fixed seed); two-sided
bootstrap p-value; Holm step-down at family alpha 0.05; the reported CI for c is at level 1 - alpha_c, alpha_c = the Holm
threshold at c's rank. Decision rule (plan, unchanged): supported if Delta >= 10 pp and Holm rejects; falsified if
|Delta| <= 3 pp; otherwise inconclusive.

Also reported (not in the family): SHUF batch-composition effect Delta_SHUF = mean[retention(BASE2) - retention(SHUF)];
M_ID (exploratory); COMBO and H1C retention; detection (exact McNemar BASE vs COMBO, BASE vs H1C) and the
internal-source ablation share.

    python -m cueinv.analysis --campaign main --projects results/subset.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from cueinv import RESULTS_DIR, RUNS_DIR
from cueinv.label import PRIMARY_MODEL, run_id_for

REALISTIC = ("R_SYN", "D_STRIP", "M_README")
STRESS = ("R_NEU", "R_NEU2", "R_MIS", "D_MIS")
FAMILY = REALISTIC + STRESS
EXPLORATORY = ("M_ID",)
REPORTED = ("SHUF", "COMBO", "H1C")
DETECTION = ("BASE", "SHUF", "COMBO", "H1C", "ABLATION")
ALPHA = 0.05
N_BOOT = 10_000
BOOT_SEED = 20261121
SUPPORT_PP = 10.0
FALSIFY_PP = 3.0
MAX_FAILED_CALL_SHARE = 0.20   # exclusion rule E3
FAILED = ("parse_failed", "truncated", "no_response")


# ---- loading --------------------------------------------------------------------------------------------------------
def load_labels(campaign: str, model: str, cond: str, rep: int, slug: str, runs_dir: Path = RUNS_DIR):
    d = Path(runs_dir) / run_id_for(campaign, model, cond, rep) / "labels" / slug
    if not (d / "stats.json").exists():
        return None, None
    params = {tuple(x) for x in json.loads((d / "source_params.json").read_text())}
    return params, json.loads((d / "stats.json").read_text())


def failed_share(stats: dict | None) -> float:
    calls = (stats or {}).get("calls") or []
    return (sum(c.get("parse_status") in FAILED for c in calls) / len(calls)) if calls else 0.0


def mean_token_delta(stats: dict | None) -> float | None:
    v = [c["token_delta_vs_base"] for c in (stats or {}).get("calls", []) if c.get("token_delta_vs_base") is not None]
    return float(np.mean(v)) if v else None


# ---- per project x condition table ------------------------------------------------------------------------------------
def project_condition_rows(campaign: str, model: str, slugs: list[str], conditions: tuple[str, ...],
                           runs_dir: Path = RUNS_DIR) -> list[dict]:
    rows = []
    for slug in slugs:
        s1, st1 = load_labels(campaign, model, "BASE", 1, slug, runs_dir)
        s2, st2 = load_labels(campaign, model, "BASE", 2, slug, runs_dir)
        if s1 is None:
            continue  # E1: project not labelled at all (DB/extraction failure) -> absent, reported in the summary
        n_par = st1["n_internal_params"]
        null = (len(s1 & s2) / len(s1)) if (s1 and s2 is not None) else None
        base_failed = max(failed_share(st1), failed_share(st2))
        for cond in conditions:
            sc, stc = (s2, st2) if cond == "BASE2" else load_labels(campaign, model, cond, 1, slug, runs_dir)
            if sc is None:
                continue
            ret = (len(s1 & sc) / len(s1)) if s1 else None
            rows.append({
                "slug": slug, "condition": cond, "n_base1": len(s1), "n_cond": len(sc), "n_internal_params": n_par,
                "retention": ret, "spurious": len(sc - s1) / n_par if n_par else None, "null": null,
                "d_pp": (100 * (null - ret)) if (null is not None and ret is not None) else None,
                "failed_share": failed_share(stc), "base_failed_share": base_failed,
                "map_back_failure_rate": (stc or {}).get("map_back_failure_rate"),
                "token_delta": mean_token_delta(stc),
                "excluded": ("E2_no_baseline_labels" if not s1 else
                             "E3_failed_calls" if max(failed_share(stc), base_failed) > MAX_FAILED_CALL_SHARE else ""),
            })
    return rows


# ---- statistics -----------------------------------------------------------------------------------------------------
@dataclass
class BootResult:
    n: int
    estimate: float
    boot: np.ndarray

    def ci(self, level: float) -> tuple[float, float]:
        a = (1 - level) / 2
        return float(np.quantile(self.boot, a)), float(np.quantile(self.boot, 1 - a))

    @property
    def p_two_sided(self) -> float:
        b = len(self.boot)
        lo = (1 + np.sum(self.boot <= 0)) / (b + 1)
        hi = (1 + np.sum(self.boot >= 0)) / (b + 1)
        return float(min(1.0, 2 * min(lo, hi)))


def bootstrap_mean(values: list[float], n_boot: int = N_BOOT, seed: int = BOOT_SEED) -> BootResult:
    x = np.asarray(values, dtype=float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(x), size=(n_boot, len(x)))
    return BootResult(len(x), float(x.mean()), x[idx].mean(axis=1))


def holm(pvals: dict[str, float], alpha: float = ALPHA) -> dict[str, dict]:
    """Holm step-down. Returns per key: adjusted p, the threshold alpha_c at its rank, and whether it is rejected."""
    order = sorted(pvals, key=lambda k: pvals[k])
    m = len(order)
    out, running, still = {}, 0.0, True
    for i, k in enumerate(order):
        thr = alpha / (m - i)
        running = max(running, min(1.0, (m - i) * pvals[k]))
        rejected = still and pvals[k] <= thr
        still = rejected
        out[k] = {"p": pvals[k], "p_holm": running, "alpha_c": thr, "rejected": rejected}
    return out


def decide(delta_pp: float, rejected: bool) -> str:
    if delta_pp >= SUPPORT_PP and rejected:
        return "supported"
    if abs(delta_pp) <= FALSIFY_PP:
        return "falsified"
    return "inconclusive"


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p on discordant pairs b (only A) and c (only B)."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


# ---- endpoints ------------------------------------------------------------------------------------------------------
def primary_endpoint(rows: list[dict], include_excluded: bool = False) -> list[dict]:
    by = {}
    for r in rows:
        if r["d_pp"] is None or (r["excluded"] and not include_excluded):
            continue
        by.setdefault(r["condition"], []).append(r["d_pp"])
    results = {c: bootstrap_mean(v) for c, v in by.items() if c in FAMILY + EXPLORATORY + ("COMBO", "H1C") and v}
    h = holm({c: results[c].p_two_sided for c in FAMILY if c in results})
    out = []
    for c in FAMILY + EXPLORATORY + ("COMBO", "H1C"):
        if c not in results:
            continue
        br = results[c]
        in_family = c in h
        level = 1 - (h[c]["alpha_c"] if in_family else ALPHA)
        lo, hi = br.ci(level)
        lo95, hi95 = br.ci(0.95)
        lo90, hi90 = br.ci(0.90)
        out.append({
            "condition": c, "kind": "realistic" if c in REALISTIC else "stress" if c in STRESS else
            "exploratory" if c in EXPLORATORY else "reported", "n_projects": br.n,
            "delta_pp": round(br.estimate, 3), "ci_level": round(level, 5), "ci_lo": round(lo, 3), "ci_hi": round(hi, 3),
            "ci95_lo": round(lo95, 3), "ci95_hi": round(hi95, 3),
            "equivalence_90ci_within_3pp": bool(lo90 >= -FALSIFY_PP and hi90 <= FALSIFY_PP),
            "p_boot": round(br.p_two_sided, 5),
            "p_holm": round(h[c]["p_holm"], 5) if in_family else None,
            "holm_rejected": h[c]["rejected"] if in_family else None,
            "decision": decide(br.estimate, h[c]["rejected"]) if in_family else "not in confirmatory family",
        })
    return out


def batch_composition_effect(rows: list[dict]) -> dict | None:
    """Delta_SHUF = mean[retention(BASE2) - retention(SHUF)] over included projects (reported, not in the family)."""
    v = [r["d_pp"] for r in rows if r["condition"] == "SHUF" and r["d_pp"] is not None and not r["excluded"]]
    if not v:
        return None
    br = bootstrap_mean(v)
    lo, hi = br.ci(0.95)
    return {"n_projects": br.n, "delta_pp": round(br.estimate, 3), "ci95_lo": round(lo, 3), "ci95_hi": round(hi, 3),
            "p_boot": round(br.p_two_sided, 5)}


def detection_endpoint(campaign: str, model: str, slugs: list[str], runs_dir: Path = RUNS_DIR) -> dict:
    det = {}
    for slug in slugs:
        for c in DETECTION:
            f = Path(runs_dir) / run_id_for(campaign, model, c, 1) / "detection" / f"{slug}.json"
            if f.exists():
                v = json.loads(f.read_text()).get("vanilla_result", {})
                det[(slug, c)] = {"recall_method": bool(v.get("recall_method")), "num_paths": v.get("num_paths")}
    out = {"per_condition": {}, "mcnemar": {}, "ablation": None}
    for c in DETECTION:
        hits = [det[(s, c)]["recall_method"] for s in slugs if (s, c) in det]
        out["per_condition"][c] = {"n": len(hits), "detected": sum(hits)}
    for other in ("COMBO", "H1C"):
        pairs = [(det[(s, "BASE")]["recall_method"], det[(s, other)]["recall_method"]) for s in slugs
                 if (s, "BASE") in det and (s, other) in det]
        b = sum(a and not o for a, o in pairs)
        c = sum(o and not a for a, o in pairs)
        out["mcnemar"][f"BASE_vs_{other}"] = {"n_pairs": len(pairs), "only_BASE": b, f"only_{other}": c,
                                              "p_exact": mcnemar_exact(b, c)}
    pairs = [(det[(s, "BASE")]["recall_method"], det[(s, "ABLATION")]["recall_method"]) for s in slugs
             if (s, "BASE") in det and (s, "ABLATION") in det]
    base_hits = sum(a for a, _ in pairs)
    lost = sum(a and not b for a, b in pairs)
    out["ablation"] = {"n_pairs": len(pairs), "base_detections": base_hits, "lost_without_internal_sources": lost,
                       "share_needing_internal_sources": (lost / base_hits) if base_hits else None}
    out["paths"] = {f"{s}|{c}": v["num_paths"] for (s, c), v in det.items()}
    return out


H1C_HELDOUT = ("R_SYN", "R_NEU2", "R_MIS", "D_MIS", "M_README")
README_VULN_RE = r"vulnerab|CVE|security|exploit|upgrade"


def h1c_robustness(campaign: str, model: str, slugs: list[str], runs_dir: Path = RUNS_DIR,
                   heldout: tuple[str, ...] = H1C_HELDOUT) -> dict | None:
    """H1c: mean over held-out channels of the share of H1C labels (vs BASE1 labels) that survive in S_c."""
    diffs, per = [], {}
    for slug in slugs:
        h, _ = load_labels(campaign, model, "H1C", 1, slug, runs_dir)
        b, _ = load_labels(campaign, model, "BASE", 1, slug, runs_dir)
        sets = [load_labels(campaign, model, c, 1, slug, runs_dir)[0] for c in heldout]
        if not h or not b or any(x is None for x in sets):
            continue
        r_h = float(np.mean([len(h & s) / len(h) for s in sets]))
        r_b = float(np.mean([len(b & s) / len(b) for s in sets]))
        per[slug] = {"r_h1c": r_h, "r_base": r_b, "n_h1c": len(h), "n_base": len(b)}
        diffs.append(100 * (r_h - r_b))
    if not diffs:
        return None
    br = bootstrap_mean(diffs)
    lo, hi = br.ci(0.95)
    return {"heldout": list(heldout), "n_projects": br.n, "effect_pp": round(br.estimate, 3), "ci95_lo": round(lo, 3),
            "ci95_hi": round(hi, 3), "p_boot": round(br.p_two_sided, 5), "per_project": per}


def readme_covariate(campaign: str, model: str, slugs: list[str]) -> list[dict]:
    """GATE 1 Q2 covariate from IRIS's cached README head of the campaign's shared IRIS run dir."""
    from cueinv import IRIS_ROOT
    from cueinv.dataset import load_projects
    from cueinv.label import iris_run_id_for

    by = {p.slug: p for p in load_projects()}
    out = []
    for slug in slugs:
        q = by[slug].query if slug in by else None
        f = (IRIS_ROOT / "output" / slug / f"{iris_run_id_for(campaign, model)}-docker" / "analysis" / str(q) / "logs" /
             "label_func_params" / "readme_head.txt")
        txt = f.read_text() if f.exists() else None
        out.append({"slug": slug, "readme_found": txt is not None, "readme_mentions_vuln": readme_mentions_vuln(txt)})
    return out


def readme_mentions_vuln(text: str | None) -> bool:
    import re

    return bool(re.search(README_VULN_RE, text or "", re.IGNORECASE))


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign", default="main")
    ap.add_argument("--model", default=PRIMARY_MODEL)
    ap.add_argument("--projects", default=str(RESULTS_DIR / "subset.csv"))
    ap.add_argument("--out-dir", default=str(RESULTS_DIR))
    a = ap.parse_args(argv)
    slugs = [r["slug"] for r in csv.DictReader(open(a.projects))]
    conds = ("BASE2", "SHUF") + FAMILY + EXPLORATORY + ("COMBO", "H1C")
    rows = project_condition_rows(a.campaign, a.model, slugs, conds)
    out = Path(a.out_dir)
    tag = f"{a.campaign}__{a.model.replace(':', '-')}"
    write_csv(out / f"project_condition_{tag}.csv", rows)
    prim = primary_endpoint(rows)
    write_csv(out / f"primary_endpoint_{tag}.csv", prim)
    write_csv(out / f"primary_endpoint_incl_excluded_{tag}.csv", primary_endpoint(rows, include_excluded=True))
    summary = {"campaign": a.campaign, "model": a.model, "n_projects_listed": len(slugs),
               "n_projects_labelled": len({r["slug"] for r in rows}),
               "excluded": {r["slug"]: r["excluded"] for r in rows if r["condition"] == "BASE2" and r["excluded"]},
               "batch_composition_effect": batch_composition_effect(rows), "primary": prim,
               "h1c_heldout_robustness": h1c_robustness(a.campaign, a.model, slugs),
               "detection": detection_endpoint(a.campaign, a.model, slugs)}
    write_csv(out / f"readme_covariate_{tag}.csv", readme_covariate(a.campaign, a.model, slugs))
    (out / f"analysis_{tag}.json").write_text(json.dumps(summary, indent=2, default=str))
    for p in prim:
        print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
