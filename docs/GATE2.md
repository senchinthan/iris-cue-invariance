# GATE 2 — pilot go/no-go (2026-11-21)

Data: `results/pilot_report.md` (generated), `results/pilot_metrics.json`, `results/power.json`, `results/diag_schema.json`.
Pilot: 5 projects (`results/pilot.csv`), primary model `qwen2.5-coder:7b-instruct-q4_K_M`, BASE r1, BASE r2, SHUF r1,
external labels, BASE detection. 423 LLM calls, 2 parse failures, 100 % GPU, VRAM peak 4.7 GB.

## Verdict under the plan's criteria: **NO-GO as designed**

| criterion (plan Phase 5) | result | pass? |
|---|---|---|
| ≥ ~200 baseline internal source labels projected over the subset | 443–451 (two estimators) | ✅ |
| power ≥ 0.8 for a 10-pp drop | P(rule) = 0.01–0.06; P(CI > 0) = 0.01–0.17 (N_eff = 28) | ❌ |

## Why power collapses (four separate causes)

1. **The JSON-schema output constraint (plan Phase 4) suppresses internal labels.** On the identical BASE prompts,
   38 of 44 batches return `[]` with `format=<schema>` vs **0 of 44** with IRIS's own free-form output; source
   parameters drop from **397 to 49** (`results/diag_schema.json`). 44 of the 49 schema labels are also free-form labels
   (all of them for xstream and solon; 14 of 19 for plexus). IRIS's prompt says "If the package does not seem to be a library,
   just return empty list"; under the grammar the model takes that exit almost every time. Labels therefore arrive in
   all-or-nothing batch blocks, which makes retention bimodal (e.g. plexus SHUF retention = 0.00).
2. **Batch composition/order alone is a large effect.** SHUF retention: plexus 0.00, solon 0.46, xstream 0.75; repeat
   (BASE2) retention 0.74–1.00. The noise floor the cue channels must beat is high.
3. **`null(p) = min(retention(BASE2), retention(SHUF))` biases Δ̂ negative.** Taking the minimum of two noisy controls and
   comparing it with a third noisy run gives Δ̂ ≈ −6.6 pp (binomial model) / −11.7 pp (empirical model) when there is **no**
   effect; a true 10-pp drop is then observed as only ≈ +3.4 / −3.4 pp.
4. **The "supported" rule thresholds the point estimate at 10 pp.** Even with zero noise, a true 10-pp drop clears
   Δ̂ ≥ 10 only ~50 % of the time (`cueinv/tests/test_phase5_pilot_power.py::test_power_rule_at_threshold_is_about_half`),
   so "power ≥ 0.8 at 10 pp" is unattainable under this rule regardless of N.

Also: 2 of 5 pilot projects (jenkins plugin, shiro) have **zero** schema-mode baseline labels, so they drop out of the
retention endpoint (N_eff ≈ 28 of 46). In free-form mode every pilot project has labels.

## Other pilot facts worth keeping

* Internal share of LLM-labelled rows 8.8 % (833 of 9,517); of calls 5.6–21 %.
* BASE detection (IRIS CodeQL stage resumed from our labels) **works end-to-end**: 2 of 5 pilot CVEs detected
  (plexus-archiver, solon — solon is one of the two annotated CVEs).
* External labelling dominates machine time (solon alone: 169 calls, 65 min).
* Machine-hours, schema mode: ≈ 34 h total. **Free-form mode** (internal calls 33.6 s instead of 2.5 s): ≈ 3.5 h per internal
  condition → ≈ 42 h primary internal (12 runs) + ≈ 21 h second model (6 runs, if similar speed) + ≈ 15 h external + ≈ 8.5 h
  detection + ≈ 5 h DB builds ≈ **90 h** (plan estimated 40–70 h; ~8 overnight runs).
* Network: mobile hotspot — container DNS is intermittent (builder retries), `raw.githubusercontent.com` blocked.

## Options (your decision)

**D1 — Output format.**
* **(a, recommended)** Drop `format=<schema>` for both external and internal prompts; parse exactly as IRIS does
  (`parse_json`), then our batch validation + map-back. This restores fidelity to IRIS (which never constrains output) and
  removes the empty-list gating. Then re-run the pilot controls in free-form mode (BASE r1 already exists from the
  diagnostic; need BASE r2 + SHUF r1 ≈ 1 h, and external labels again ≈ 2 h for consistency) and recompute power.
* (b) Keep the schema as the plan specifies, and accept sparse, bimodal labels.

**D2 — Decision rule / noise floor** (goes into PREREG, Phase 7; best decided after D1's re-pilot):
* (a) Keep "supported = Δ̂ ≥ 10 pp and Holm-CI excludes 0", but define the power target as P(CI excludes 0) at δ = 10 pp
  (and report P(rule) at δ = 15 pp).
* (b) Use `null(p) = mean(retention(BASE2), retention(SHUF))` instead of the minimum (removes the built-in negative bias).
* (c) Estimate the noise floor from more control runs (e.g. BASE ×3, SHUF ×2 seeds) — costs ≈ 3.5 h per extra run in free-form.

**D3 — Plan's own fallbacks** (only if D1 + D2 are not enough): pool channels (rename variants; doc variants), widen the
subset (e.g. main-code SLOC cap → +3 projects; relax 150K), add CWE-79 (38 CVEs).

Recommended sequence: approve D1(a) → I re-run the pilot controls free-form and regenerate `results/pilot_report.md` →
you choose D2 (and D3 if needed) with the new numbers → Phase 6.

## Decisions (user)
* **D1 → (a)** (2026-11-21): no `format` constraint for external or internal prompts; responses parsed with IRIS's own
  `parse_json` (iris.py:484-526), then our batch validation + map-back. This deviates from plan Phase 4 ("format=<JSON schema>")
  and is recorded here and in `docs/RECON.md`. The schema-mode pilot (campaign `pilot`) is kept for the record; the
  free-form pilot is campaign `pilot-ff`.
* D2, D3: pending — to be decided on the `pilot-ff` numbers.

## Re-pilot in free-form mode (campaign `pilot-ff`, 2026-11-21) — `results/pilot_report_pilot-ff.md`

Internal controls (BASE r1, BASE r2, SHUF r1) on the same 5 projects; 132 calls, **0 parse failures**, 72 of 983 returned
items name something outside their batch (7 %, counted as map-back failures). External labels + BASE detection were still
running when this was written (tmux `pilotff`); they affect only timing and the plumbing check, not D2/D3.

| project | S_BASE1 params | ret BASE2 (repeat) | ret SHUF (reshuffle) | flip repeat | flip reshuffle |
|---|---|---|---|---|---|
| jenkins workflow-cps-global-lib | 13 | 1.000 | 0.538 | 0.000 | 0.375 |
| plexus-archiver | 65 | 0.908 | 0.631 | 0.071 | 0.417 |
| shiro | 93 | 1.000 | 0.419 | 0.000 | 0.272 |
| xstream | 68 | 0.721 | 0.250 | 0.102 | 0.241 |
| solon | 169 | 0.976 | 0.391 | 0.008 | 0.384 |

* Baseline labels: 408 parameters (325 methods) in the pilot → **≈ 3,700 projected** over the 46 projects (criterion ≥ 200 ✅);
  every pilot project has labels, so N_eff = 46.
* **Reshuffling is itself a large effect**: changing which methods share a batch moves 37–75 % of the labels
  (retention 0.25–0.63), while an identical repeat keeps 0.72–1.00. Batch context, not inference noise, dominates.

### Power at N = 46 (`power_pilot-ff.json`), P(CI excludes 0) at α = .05/7 · P(full rule) at α = .05/7

| noise floor null(p) | δ = 5 pp | δ = 10 pp | δ = 15 pp | bias of Δ̂ at δ = 0 |
|---|---|---|---|---|
| plan: min(BASE2, SHUF), binomial | 0.10 · 0.00 | 0.93 · 0.01 | 1.00 · 0.86 | −3.4 pp |
| plan: min(BASE2, SHUF), empirical | 0.00 · 0.00 | 0.00 · 0.00 | 0.03 · 0.03 | −11.8 pp |
| D2(b): mean(BASE2, SHUF), binomial | – | 1.00 · 0.49 | 1.00 · 1.00 | 0.1 pp |
| D2(b): mean(BASE2, SHUF), empirical | – | 0.37 · 0.36 | 0.78 · 0.78 | 0.2 pp |
| **D2(d): BASE2 only (repeat), binomial** | **1.00 · 0.00** | **1.00 · 0.49** | **1.00 · 1.00** | **0.0 pp** |

(The full rule's ≈ 0.49 at δ = 10 is the cap from thresholding Δ̂ ≥ 10; with project heterogeneity τ = 10 pp the
D2(d) rule power is 0.62 at δ = 10 and 1.00 at δ = 15, but bounded retention then inflates P(CI>0) at δ = 0 to 0.24 —
the analysis should use the paired per-project difference with a bootstrap, as planned, and report τ.)

### Refined D2 options
* **D2(d) (recommended)**: every cue condition (R_*, D_*, M_*, COMBO) keeps BASE's batches, so its noise floor is the
  **repeat** control: `null(p) = retention(BASE2)`. SHUF becomes a **separate, reported effect** (batch-composition
  sensitivity, itself a finding), not part of the noise floor. Optionally add BASE r3 (+1 run, ≈ 1.8 h) to estimate the repeat
  noise per project more precisely.
* D2(a): keep the plan's `min(BASE2, SHUF)` floor — the cue channels would have to beat reshuffle-sized changes; power ≈ 0
  for 10-pp effects under the empirical model.
* D2(b): `mean(BASE2, SHUF)` — unbiased but noisy (empirical power 0.36 at δ = 10).
* **Decision-rule wording (any floor)**: with "supported = Δ̂ ≥ 10 pp and Holm-CI excludes 0", a true 10-pp effect is
  supported only ~50 % of the time by construction. Options: (i) keep the rule and state the power target as P(rule) ≥ 0.8
  at δ = 15 pp (met under D2(d)), or (ii) "supported = Holm-CI lower bound ≥ 5 pp" (a margin test).
* D3 (pool channels / widen / add CWE-79): **not needed** under D2(d) — N = 46 already gives power ≈ 1.0 for CI > 0 at 5 pp.

Machine-hours (free-form): internal ≈ 1.8 h per condition (pilot 15 s/call) → ≈ 22 h primary (12 runs) + ≈ 11 h second
model + external (≈ 22 s/call, ≈ 4,000 calls ≈ 25 h, measured on completion) + detection ≈ 8.5 h + DB builds ≈ 5 h
→ ≈ 70 h total, inside the plan's 40–70 h range at the upper end.
