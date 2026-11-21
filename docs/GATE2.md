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
