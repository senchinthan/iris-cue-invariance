"""Power simulation for the primary endpoint (plan Phase 5): project-clustered bootstrap.

Model of one simulated study with N projects:
  * draw N projects with replacement from the pilot; each brings n_p = |S_BASE1| (baseline internal source labels)
    and a null retention rho_p = mean(retention(BASE2), retention(SHUF)) observed in the pilot;
  * observed controls  ret_B2, ret_SHUF ~ Binomial(n_p, rho_p) / n_p;  null(p) = min(ret_B2, ret_SHUF)  (plan Phase 9);
  * perturbed channel  ret_c ~ Binomial(n_p, clip(rho_p - delta_p)) / n_p,  delta_p = delta + Normal(0, tau);
  * Delta_hat = mean_p(null(p) - ret_c(p)) in percentage points; CI = percentile bootstrap over projects.
Two noise models:
  * 'binomial'  : as above (independent label-level noise; optimistic when labels arrive in all-or-nothing batches);
  * 'empirical' : each run's retention is drawn from that pilot project's *observed* null retentions
                  {retention(BASE2), retention(SHUF)}: null(p) = min(r1, r2), ret_c = clip(r3 - delta_p, 0, 1).
Projects with |S_BASE1| = 0 have undefined retention and are excluded from the endpoint, so callers should pass the
projected number of projects *with* baseline labels as n_projects.
Power is reported for
  * 'ci'   : CI lower bound > 0,
  * 'rule' : the pre-registered "supported" rule (Delta_hat >= 10 pp AND CI excludes 0),
at alpha = 0.05 (unadjusted) and alpha = 0.05/7 (Holm's first step across the 7 realistic + stress channels — the
most conservative Holm threshold).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np

N_CHANNELS_HOLM = 7  # R_SYN, R_NEU, R_NEU2, R_MIS, D_STRIP, D_MIS, M_README
SUPPORT_THRESHOLD_PP = 10.0


@dataclass
class PilotProject:
    slug: str
    n_labels: int        # |S_BASE1|
    ret_b2: float        # retention(BASE2)
    ret_shuf: float      # retention(SHUF)

    @property
    def rho(self) -> float:
        return (self.ret_b2 + self.ret_shuf) / 2


def simulate(pilot: list[PilotProject], n_projects: int, delta_pp: float, *, tau_pp: float = 0.0, n_sims: int = 1000,
             n_boot: int = 2000, alphas=(0.05, 0.05 / N_CHANNELS_HOLM), seed: int = 0, model: str = "binomial") -> dict:
    usable = [p for p in pilot if p.n_labels > 0]
    if not usable:
        raise ValueError("no pilot project has baseline labels")
    rng = np.random.default_rng(seed)
    n = np.array([p.n_labels for p in usable])
    rho = np.array([p.rho for p in usable])
    delta = delta_pp / 100.0
    tau = tau_pp / 100.0

    idx = rng.integers(0, len(usable), size=(n_sims, n_projects))
    n_s, rho_s = n[idx], rho[idx]
    d_p = np.clip(delta + (rng.normal(0, tau, size=n_s.shape) if tau > 0 else 0.0), -1, 1)
    if model == "binomial":
        r_b2 = rng.binomial(n_s, rho_s) / n_s
        r_sh = rng.binomial(n_s, rho_s) / n_s
        r_c = rng.binomial(n_s, np.clip(rho_s - d_p, 0, 1)) / n_s
    elif model == "empirical":
        pool = np.array([[p.ret_b2, p.ret_shuf] for p in usable])[idx]          # (n_sims, N, 2)
        pick = lambda: np.take_along_axis(pool, rng.integers(0, 2, size=idx.shape)[..., None], axis=2)[..., 0]  # noqa
        r_b2, r_sh = pick(), pick()
        r_c = np.clip(pick() - d_p, 0, 1)
    else:
        raise ValueError(model)
    null = np.minimum(r_b2, r_sh)
    diff = (null - r_c) * 100.0                                    # pp, shape (n_sims, N)
    est = diff.mean(axis=1)

    # percentile bootstrap over projects, vectorised in chunks
    lows = {a: np.empty(n_sims) for a in alphas}
    chunk = max(1, 200_000 // max(1, n_boot))
    for s0 in range(0, n_sims, chunk):
        s1 = min(n_sims, s0 + chunk)
        bi = rng.integers(0, n_projects, size=(s1 - s0, n_boot, n_projects))
        boot = np.take_along_axis(diff[s0:s1, None, :].repeat(n_boot, axis=1), bi, axis=2).mean(axis=2)
        for a in alphas:
            lows[a][s0:s1] = np.quantile(boot, a / 2, axis=1)

    out = {"model": model, "delta_pp": delta_pp, "tau_pp": tau_pp, "n_projects": n_projects, "n_sims": n_sims, "n_boot": n_boot,
           "mean_estimate_pp": round(float(est.mean()), 2), "sd_estimate_pp": round(float(est.std()), 2)}
    for a in alphas:
        tag = "a05" if abs(a - 0.05) < 1e-12 else f"a05_over_{N_CHANNELS_HOLM}"
        ci_ok = lows[a] > 0
        out[f"power_ci_{tag}"] = round(float(ci_ok.mean()), 3)
        out[f"power_rule_{tag}"] = round(float((ci_ok & (est >= SUPPORT_THRESHOLD_PP)).mean()), 3)
    out["p_falsified_band"] = round(float((np.abs(est) <= 3.0).mean()), 3)
    return out


def power_table(pilot: list[PilotProject], n_projects: int, deltas=(0, 5, 10, 15), taus=(0.0, 10.0),
                models=("binomial", "empirical"), **kw) -> list[dict]:
    return [simulate(pilot, n_projects, d, tau_pp=t, model=m, **kw) for m in models for t in taus for d in deltas]


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot-json", required=True, help="list of {slug, n_labels, ret_b2, ret_shuf}")
    ap.add_argument("--n-projects", type=int, required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    pil = [PilotProject(**x) for x in json.load(open(a.pilot_json))]
    json.dump(power_table(pil, a.n_projects), open(a.out, "w"), indent=2)
