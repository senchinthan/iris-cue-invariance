# Pre-registration: cue-invariance of LLM-inferred internal taint sources in IRIS

Version **prereg-v1** (git tag), written 2026-11-21, **before any cue-perturbation condition was run on real LLM outputs**
(the perturbation layer was validated only with an echo transport; see §9). Repository state at the tag freezes the
analysis code (`cueinv/analysis.py`), the perturbation layer (`cueinv/perturb.py`), the labelling harness and the subset.

## 1. Question

IRIS (Li, Dutta, Naik, ICLR 2025) asks an LLM which internal library methods' parameters are taint sources, then CodeQL
traces taint paths from them. We test whether those **internal-source labels stay stable when the prompt changes in ways
that preserve the code's semantics**: identifier renaming, JavaDoc changes, removing the README summary, masking the
project's identity. The code analysed by CodeQL is never changed; only the internal-labelling prompt is.

## 2. Hypotheses

* **H1 (primary, one test per channel c).** Internal source labels are *not* cue-invariant: the loss of baseline labels under
  c exceeds the inference-noise floor by at least 10 percentage points (Δ(c) ≥ 10 pp, §4).
  * H1a — rename channel: R_SYN (realistic); R_NEU, R_NEU2, R_MIS (stress).
  * H1b — documentation channel: D_STRIP (realistic); D_MIS (stress).
  * H1m — README channel: M_README (realistic).
  * M_ID (identity mask) is an **exploratory** contamination probe, outside the confirmatory family.
* **H1c (mitigation, secondary).** A cue-diverse vote — a parameter is a source iff ≥ 2 of the 3 views BASE, R_NEU,
  D_STRIP label it — yields labels that survive the **held-out** cue perturbations (R_SYN, R_NEU2, R_MIS, D_MIS, M_README;
  none of them is a vote input) better than BASE labels do, at a detection cost we report. The vote adapts the
  majority-voting ideas of AdaTaint (arXiv 2511.04023) and QLPro (arXiv 2506.23644); it is not presented as novel.
* **Secondary.** S1 detection under BASE vs COMBO and BASE vs H1C; S2 internal-source ablation; S3 ground-truth recall
  (pre-declared not estimable, §6); S4 batch-composition effect (SHUF); S5 retention vs prompt-token delta; S6 second model;
  S7 Kaggle anchor model.

## 3. Materials

| item | value |
|---|---|
| IRIS | v2 @ `d2059f299a80a68ea4a0b0315eec2f42b8743776`, core unmodified; patches `0001` (toolchain paths), `0002` (slug normalisation of 5 metadata rows) — `docs/PATCHES.md` |
| CodeQL | 2.23.2, `codeql/java-queries` 1.8.1 (IRIS's paper used patched 2.15.3 — threat T5) |
| Projects | `results/subset.csv`: **46** CWE-22/CWE-78 CVEs of CWE-Bench-Java (34 CWE-22, 12 CWE-78), selection rules in `cueinv/select_subset.py` (build ok, no giants, buggy commit, IRIS Docker build ≤ 15 min, Java SLOC incl. tests ≤ 150K, v2 Docker image exists). All 46 are analysed, including the 5 pilot projects (the pilot ran only BASE and SHUF). |
| Primary model | `qwen2.5-coder:7b-instruct-q4_K_M`, Ollama 0.40.16, digest `dae161e27b0e…f4364` |
| Second model | `qwen3:4b` (`think: false`), digest `359d7dd4bcda…fae7`; internal labels only |
| Anchor | DeepSeekCoder-7B-instruct (Q4_K_M) on Kaggle from exported BASE / R_NEU / D_STRIP prompts |
| Decoding | temperature 0, top_k 1, top_p 1, seed 0, num_ctx 8192, num_predict 2048, num_gpu 99 (all layers on GPU); **no output-format constraint**; responses parsed with IRIS's own `parse_json` (GATE 2 D1(a)) |
| Prompts | IRIS's templates imported verbatim (`src/prompts.py`); external prompts are IRIS's own (stage 3, injected client); internal prompts rebuilt by us from IRIS's candidate rows, byte-identical to IRIS for BASE (test `test_base_view_reproduces_iris_prompts_byte_for_byte`); internal batch 20, external batch 30; README = IRIS's fix-commit README head (GATE 1 Q2) |

## 4. Endpoints (implemented in `cueinv/analysis.py`)

A label is an internal parameter `(package, class, method, parameter)` labelled source, after validating the answer
against its batch and mapping renamed identifiers back (`cueinv/label.py: map_back`). S_c(p) is the label set of project p
under condition c (rep 1; BASE has reps 1 and 2).

* retention_p(c) = |S_BASE1 ∩ S_c| / |S_BASE1|
* spurious_p(c) = |S_c \ S_BASE1| / #internal candidate parameters of p
* **noise floor null(p) = retention_p(BASE2)** — the identical-prompt repeat. Rationale (GATE 2 D2): every cue condition keeps
  BASE's batch composition, so its sampling noise is the repeat noise; the pilot showed batch *composition* (SHUF) is itself a
  large effect (retention 0.25–0.63 vs repeat 0.72–1.00), which we report separately (S4) instead of folding it into the floor.
* d_p(c) = 100 · (null(p) − retention_p(c)) percentage points; **Δ(c) = mean_p d_p(c)**.
* Inference: paired project-level percentile bootstrap of Δ(c), 10,000 resamples, seed 20261121; two-sided bootstrap p-value
  p = min(1, 2·min((1+#{Δ*≤0})/(B+1), (1+#{Δ*≥0})/(B+1))); **Holm** step-down across the 7 confirmatory channels at family
  α = 0.05; each channel's CI is reported at level 1 − α_c (its Holm threshold) and at 95 %.
* **Decision rule (unchanged from the plan):** supported if Δ ≥ 10 pp **and** Holm rejects; falsified if |Δ| ≤ 3 pp;
  inconclusive otherwise. We additionally report whether the 90 % CI lies within ±3 pp (equivalence), without changing the rule.
* Power target (GATE 2): P(supported) ≥ 0.8 at a true 15-pp drop and P(CI excludes 0) ≥ 0.8 at 10 pp; the free-form pilot
  simulation gives ≈ 1.0 for both at N = 46 (binomial model, `results/power_pilot-ff.json`). Because the rule thresholds the
  point estimate at 10 pp, a true 10-pp effect is supported only ≈ 50 % of the time; we state this in advance.

Secondary endpoints
* **S1 detection.** IRIS's CodeQL stage with `--skip-posthoc-filter`, scoring `vanilla_result.recall_method` on
  `results_pp.sarif` (fix-method matching, `src/modules/evaluation_pipeline.py:193-251`), for BASE, SHUF, COMBO, H1C, ABLATION.
  Exact two-sided McNemar for BASE vs COMBO and BASE vs H1C.
* **S2 ablation.** Share of BASE detections lost when every internal source is removed (ABLATION = external specs only).
* **S3 ground-truth recall.** Pre-declared **not estimable**: of the 50 annotated CVEs in `fix_info_source_sink.csv`, two are in
  the subset; conductor's true source is an external API (environment variable, unaffected by internal perturbations) and
  solon's (`JtFunctionLoaderFile.fileGet(String path)`) is not an IRIS internal candidate (it is called from non-test code,
  excluded by `fetch_func_params.ql:19-24`). Reported descriptively.
* **S4 batch composition.** Δ_SHUF = mean_p[retention(BASE2) − retention(SHUF)] with a 95 % bootstrap CI.
* **S5 token covariate.** Project × condition retention ~ condition + mean per-batch prompt-token delta vs BASE, OLS with
  project-clustered (CR1) SEs and a wild cluster bootstrap (Rademacher, 9,999) for the condition coefficients.
* **H1c.** held-out robustness: for each project, r_H1C(p) = mean over c ∈ {R_SYN, R_NEU2, R_MIS, D_MIS, M_README} of
  |H1C ∩ S_c| / |H1C| and r_BASE(p) = the same for S_BASE1; effect = mean_p[r_H1C − r_BASE] with a 95 % paired bootstrap CI
  (projects with |H1C| ≥ 1). COMBO-based robustness is reported alongside but is not held out (it combines the R_NEU and
  D_STRIP views). Costs: S1 for H1C and alert-path counts (FDR proxy) BASE vs H1C (`cueinv.analysis.h1c_robustness`).
* **S6/S7.** Same internal endpoints where the runs exist (second model: BASE ×2, SHUF, R_NEU, D_STRIP, M_README; anchor:
  BASE, R_NEU, D_STRIP, raw retention only), exploratory, no multiplicity correction.

## 5. Conditions (`cueinv/perturb.py`)

| ID | kind | implementation |
|---|---|---|
| BASE | control | IRIS rows, order and batches; reps 1 and 2 |
| SHUF | control (S4) | seeded permutation of the candidates (seed = stable_seed(slug, "SHUF")) → new batch membership and order |
| R_SYN | realistic | curated synonym table: verbs replaced only as a method's leading word (get→fetch, read→load, …), nouns anywhere (dir→folder, value→val, …); security-loaded words (file, path, url, exec, …) deliberately absent |
| R_NEU | stress | neutral names from non-word subword fragments of the model vocabulary (scheme A, `m_`/`p_`/`C_` prefixes for ≥ 3-token originals), token-count-matched (96–99 % exact on the pilot) and length-matched (mean \|Δ\| 2–4 chars) with the pinned Qwen2.5-Coder tokenizer |
| R_NEU2 | stress | as R_NEU with the disjoint half of the fragment vocabulary, camel-case pseudo-words, no prefixes (75–86 % exact) |
| R_MIS | stress | methods → sanitiser-style verb+noun (sanitizeInput, validateFormat, …), parameters → benign adjective+noun (trustedValue, …); classes and constructor names kept |
| D_STRIP | realistic | JavaDoc removed |
| D_MIS | stress | every non-empty JavaDoc replaced by a benign but wrong description (first 50 chars, "..." kept when IRIS had truncated) |
| M_README | realistic | README summary removed (empty string) |
| M_ID | exploratory | owner/name → `projectX` in the prompt header, and owner/name/distinctive name parts masked in README and JavaDoc prose (package names unchanged) |
| COMBO | S1, H1c | R_NEU with **R_NEU's seed** (identical rename) + D_STRIP |
| H1C | H1c, S1 | vote(BASE, R_NEU, D_STRIP) ≥ 2 of 3, no LLM call |
| ABLATION | S2 | no internal sources |

Rename rules (all R_* and COMBO): one string-level map per project (the same identifier string always gets the same new
string), applied to declaring classes, method names, parameter names, project-class names inside signature types, and every
whole-word occurrence in JavaDoc and README prose for identifiers of ≥ 3 characters (shorter ones only in rows/signatures).
Java keywords, primitives, JDK and external class names are never renamed or produced; new names never equal an original
identifier. Maps are bijective, deterministic in (project, condition seed) and saved in `runs/<run_id>/rename_map/<slug>.json`.
Answers naming anything outside their batch are map-back failures, reported per condition.

## 6. Exclusion rules

* **E1** — project absent from an endpoint if its CodeQL DB or IRIS candidate extraction fails after 3 attempts (listed).
* **E2** — project excluded from retention-based endpoints if |S_BASE1| = 0 (spurious still reported).
* **E3** — project × condition excluded if > 20 % of that condition's calls, or of BASE1/BASE2's, end parse_failed, truncated
  or without response; a sensitivity analysis including them is reported (`primary_endpoint_incl_excluded_*.csv`).
* **E4** — detection pairs missing when CodeQL produces no SARIF for one side (listed).
No other exclusions; no outlier removal.

## 7. Run plan

Campaign `main`, one project at a time, resumable (`cueinv/run.py`), tmux, logs in `runs/`:
1. primary model: external labels once; internal BASE r1, r2, SHUF, R_SYN, R_NEU, R_NEU2, R_MIS, D_STRIP, D_MIS, M_README,
   M_ID, COMBO; H1C and ABLATION (no LLM);
2. second model: BASE r1, r2, SHUF, R_NEU, D_STRIP, M_README;
3. detection: BASE, SHUF, COMBO, H1C, ABLATION;
4. Kaggle anchor from `kaggle/prompts.jsonl`.
Projected ≈ 59 machine-hours (free-form pilot timing, `results/pilot_report.md`). Missing DBs for 41 projects are built first
(≈ 22 GB of images, one at a time).

## 8. Analysis code path

`python -m cueinv.analysis --campaign main --projects results/subset.csv` → `results/primary_endpoint_<campaign>__<model>.csv`,
`project_condition_*.csv`, `analysis_*.json` (primary endpoint, S1, S2, S4). Figures and S5 are added in Phase 9 on top of the
same tables; any change to the frozen confirmatory functions after prereg-v1 will be reported as a deviation.

## 9. Pilot (5 projects; `results/pilot_report_pilot.md`, `results/pilot_report_pilot-ff.md`, `docs/GATE2.md`)

* Schema-constrained pilot: 38 of 44 internal batches answered `[]`; 49 baseline labels; power ≈ 0 → GATE 2 no-go.
* Same prompts free-form (`results/diag_schema.json`): 0 of 44 empty, 397 labels → output constraint dropped (D1(a)).
* Free-form pilot (external, BASE ×2, SHUF, BASE detection): 408 baseline labels (≈ 3,700 projected), 2 parse failures in
  423 calls (0 of 132 internal), 7 % of returned internal items outside their batch; repeat retention 0.72–1.00, SHUF
  retention 0.25–0.63; mean 15.2 s per internal and 15.9 s per external call; BASE detection 2 of 5 CVEs (plexus-archiver,
  solon); `results/pilot_report.md` (= `pilot_report_pilot-ff.md`).
* Perturbation layer: 155 tests (round trip 100 %, external prompts byte-identical, no surviving identifiers, SHUF only
  reorders) and an echo dry run (all 13 label conditions × 5 projects map back exactly). No cue condition was run on real
  model outputs before this pre-registration.

## 10. Deviations from `CAPSTONE_PLAN.md` (all decided before main runs)

1. No JSON-schema `format` (plan Phase 4) — it suppressed internal labels (GATE 2 D1(a)).
2. Noise floor = BASE2 instead of min(BASE2, SHUF); SHUF reported as S4 (GATE 2 D2).
3. Subset: Payara added to the giant list; SLOC counts all Java incl. tests; testng excluded (no Docker image); 46 projects.
4. GATE 1 Q1 (keep annotated CVEs) implemented as a data patch (0002) instead of a code hook; only 2 annotated CVEs survive the
   SLOC cap, and ground-truth recall is not estimable (§4 S3).
5. M_ID also masks identity in prose; R_MIS keeps class names; prose replacement only for identifiers ≥ 3 characters; R_NEU
   prefixes only for ≥ 3-token originals; COMBO reuses R_NEU's seed.
6. Spurious denominator = internal candidate *parameters* (plan: "#internal candidates").

## 11. Threats to validity (pre-stated)

T1 inference nondeterminism (repeat floor); T2 realism (realistic vs stress split); T3 construct validity (ablation, S2);
T4 one language, two CWEs, 46 projects; T5 toolchain drift (CodeQL 2.23.2 vs the paper's patched 2.15.3); T6 README from the
fix commit can carry vulnerability cues (GATE 1 Q2; covariate `readme_mentions_vuln` = case-insensitive match of
`vulnerab|CVE|security|exploit|upgrade` in IRIS's README head, reported per project and as a moderator of the M_README
effect); T7 rename prose replacement
also alters JavaDoc content; T8 network: README fetched live once per project and cached.

## 12. Approval

Approved by the study author on 2026-11-21 (GATE 3), unchanged from tag `prereg-v1`. Made public as described in `PROGRESS.md`.
