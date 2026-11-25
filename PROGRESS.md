# Capstone progress tracker

Source plan: `CAPSTONE_PLAN.md`. Legend: `[x]` done · `[~]` in progress · `[ ]` not started · `[!]` blocked / needs user.
Each GATE is a hard stop: work resumes only after the user approves.

---

## Phase W — Windows-side manual steps (user)
- [x] W.1 `.wslconfig` applied — actual: `memory=11GB, processors=12, swap=16GB` (plan: 12GB / 6 / 8GB); `free -g` shows 10 GiB usable
- [x] W.2 NVIDIA driver — `nvidia-smi` shows RTX 3060 Laptop, 6144 MiB
- [x] W.3 Docker Desktop with WSL2 integration — `docker run hello-world` OK (Docker 29.9.3)
- [x] W.4 ≥120 GB free — 898 GB free on `/`

## Phase 1 — Environment
- [x] 1.1 Verify env (nvidia-smi, docker hello-world, free, df, Python 3.12.3); conda missing → Miniconda to install
- [x] 1.2 Workspace dirs + `git init` + IRIS clone (v2 @ `d2059f2`), SHA in `docs/VERSIONS.md`
- [x] 1.3 Conda env `iris` (Miniconda, conda-forge only, CPU torch) + CodeQL bundle 2.23.2 / java-queries 1.8.1 (checksum verified); JDK 8 + Maven 3.5.0 user-space (no sudo, no SDKMAN `zip`)
- [x] 1.4 Ollama 0.40.16 serve in tmux with the 4 env vars; `qwen2.5-coder:7b-instruct-q4_K_M` (`dae161e2…`) and `qwen3:4b` (`359d7dd4…`) pulled
- [x] 1.5 VRAM check — 4026-token prompt @ `num_ctx=8192` is **100% GPU only with `num_gpu=99`** (Ollama's default fit keeps a 1 GiB margin → 25/29 layers, 19% CPU, even at 6144). Steady-state ≈12 tok/s gen, ≈410–560 tok/s prompt; GPU reports SW power-cap + SW thermal throttling (Windows-side power mode). `results/vram_check.json`, `results/vram_check_default_offload.json`
- [x] 1.6 IRIS quickstart end-to-end on spark — **native route worked** (build 1m35s, DB 55s, IRIS 10m15s). CodeQL: 2 alarms / 5 paths; Recall@Method **False**. 553 ext / 106 int candidates (79 after dedup); 23 internal methods (39 params) labelled source; 0 parse errors; no context truncation. Outputs archived in `runs/quickstart-spark/`
- [x] 1.7 `docs/VERSIONS.md` filled in; `pytest -q` 5 passed; Phase 1 commit

## Phase 2 — Code reconnaissance (read-only)
- [x] 2.1 `docs/RECON.md` questions 1–8 with file:line refs, plus quickstart-verified facts (§9 data quirks, §10 plan-vs-repo diffs)
- [x] 2.2 Interception design written up (RECON §8) + GATE 1 decisions (RECON §11)
- [x] 2.3 `pytest -q`: 14 passed (incl. byte-for-byte reproduction of IRIS internal prompts); Phase 2 commit
- [x] **GATE 1** — passed. Q1: keep the 4 annotated CVEs (patch cve_id parsing). Q2: keep fix-commit README + `readme_mentions_vuln` covariate. Q3: interception design approved (2026-11-21).

## Phase 3 — Subset selection
- [x] 3.1 `cueinv/select_subset.py` + `cueinv/dataset.py`: CWE-22/78 (both spellings, pool 73) + build success; slugs canonicalised
- [x] 3.2 SLOC via cloc 2.10 (user-space, blob-SHA verified) on sources fetched at the buggy commit (`cueinv/sources.py`); IRIS Docker build durations
- [x] 3.3 Filters: build ok → giants (plan list + Payara) → buggy commit → ≤15 min → fetch → ≤150K Java SLOC → Docker image exists
- [x] 3.4 `results/subset.csv`: **46 projects** (34 CWE-22, 12 CWE-78; 2 annotated); all 73 with reasons in `results/subset_all.csv`
- [x] 3.5 `results/pilot.csv`: 5 projects, SLOC quintiles 4K–125K, 3×CWE-22 + 2×CWE-78, incl. annotated solon (seeded rule)
- [x] 3.6 CodeQL DBs for the 5 pilot projects via `cueinv/build_dbs.py` (pull → retag → IRIS container build with retries → package-names → `docker rmi`); Q1 data patch `patches/0002`. Remaining 41 subset DBs: not built yet (needed for Phase 8)
- [x] 3.7 Tests pass; Phase 3 commit (`d6c8cf0`)

## Phase 4 — Deterministic LLM client + baseline labelling
- [x] 4.1 `cueinv/llm.py` (temp 0, top_k 1, top_p 1, seed, num_ctx 8192, num_predict 2048, num_gpu 99, JSON-schema format, warm-up, qwen3 `think:false`, retry-once, token + GPU logging, jsonl cache, resumable, refuses mixed settings)
- [x] 4.2 `cueinv/label.py` `label_external` via IRIS stage 3 + injected `IrisModelAdapter`; external prompt SHA-256s recorded per project
- [x] 4.3 `label_internal` via `cueinv/perturb.py` (BASE, SHUF so far), batch validation + map-back, IRIS-format label files; `cueinv/iris_bridge.py`, `cueinv/run.py` (+ detection resume path)
- [x] 4.4 Run logging: `runs/<campaign>__<model>__<cond>__r<rep>/{config.json, llm_calls.jsonl, rename_map/, labels/}`
- [x] 4.5 Tests pass (35); smoke run on plexus-archiver OK (17 ext + 4 int calls, 0 parse failures, 100 % GPU)

## Phase 5 — Pilot
- [x] 5.1 Pilot runs: external + BASE r1/r2 + SHUF r1 on 5 projects, + BASE detection (423 calls, 2 parse failures, 100 % GPU)
- [x] 5.2 `cueinv/pilot_report.py` → `results/pilot_report.md` (+ schema-vs-free-form diagnostic `cueinv/diag_schema.py`)
- [x] 5.3 `cueinv/power.py`: binomial + empirical noise models, δ = 0/5/10/15, τ = 0/10, effective N
- [x] 5.4 Tests pass (37); Phase 5 commit
- [x] **GATE 2** — **GO** (2026-11-21). D1(a) free-form output + IRIS parse_json; D2 noise floor = repeat (BASE2), SHUF reported separately, plan rule kept with power target at δ = 15 pp; D3 not needed. Free-form re-pilot: ≈ 3,700 labels projected, 0 parse failures

## Phase 6 — Perturbation layer
- [x] 6.1 `cueinv/perturb.py`: BASE, SHUF, R_SYN, R_NEU, R_NEU2, R_MIS, D_STRIP, D_MIS, M_README, M_ID, COMBO; H1C vote in `cueinv/vote.py`; ABLATION (no internal sources) in `label.py`
- [x] 6.2 String-level bijective rename across rows, signature types, JavaDoc and README (≥3-char identifiers in prose); keywords/JDK/external types reserved; maps saved to `runs/<run_id>/rename_map/<slug>.json`; R_NEU token-count-matched with the pinned Qwen tokenizer (96–99 % exact; R_NEU2 75–86 %)
- [x] 6.3 Map-back failure rate per condition (`map_back_failure_rate`, unmapped items/params) + per-batch `token_delta_vs_base`
- [x] 6.4 Tests (155 in `test_phase6_perturb.py`, 197 total): round-trip identity 100 %, external prompts byte-identical (also vs IRIS's own dump), no surviving identifiers, SHUF only reorders; end-to-end echo dry run: all 13 conditions × 5 pilot projects map back exactly
- [x] 6.5 Phase 6 commit

## Phase 7 — Pre-registration
- [x] 7.1 `docs/PREREG.md` (hypotheses, endpoints, conditions, decision rule, exclusions E1–E4, pilot, analysis code path, deviations, threats) + frozen confirmatory code `cueinv/analysis.py` (8 tests)
- [x] 7.2 Commit + `git tag prereg-v1`
- [x] **GATE 3** — PREREG.md approved by the user (2026-11-21) and **public**: https://github.com/senchinthan/iris-cue-invariance (tag `prereg-v1`, repo created 2026-11-21T13:44:22Z, pushed 13:44:42Z). History rewritten to the GitHub noreply address; memory.md kept local

## Phase 8 — Main runs
- [x] 8.0 CodeQL DBs for the 41 remaining subset projects — **all 46 subset DBs + package-names files present (2026-11-22 13:43)**; images removed; DBs 1.4 GB. Runs: 2026-11-21 16:09–16:23, 2026-11-22 05:00–07:59, 08:06–10:05 (paused by the user), 12:52–13:43. Fixes: network wait + pull retries, `--max-hours` budget, D-1 conductor package prefix, D-2 djl buildSrc groupId, D-3 `patches/0003` spring-cloud-config Checkstyle, D-4 venice Gradle pre-cache (`docs/DEVIATIONS.md`). Tests 214 passed
- [x] 8.1 `cueinv/run.py` (resumable, one project at a time, tmux) + resume safety for power cuts (2026-11-24): crash-safe writes, cut-off cache lines repaired, damaged IRIS files moved to `runs/tmp/quarantine/`, IRIS API-cache snapshot/restore, README prefetch (`cueinv/prefetch_readmes.py`), missing Ollama replies never cached (wait + retry, else stop). Tests 227 passed
  **Phase 8 runs in parts; each part starts only with the user's permission** (campaign `main`, tmux `main`):
  | part | what | needs | est. h | command (`python -m cueinv.run --campaign main --projects results/subset.csv …`) |
  |---|---|---|---|---|
  | 0 ✅ 2026-11-24 | README prefetch — 46/46, 0 network errors, 4 from `master` (IRIS fallback: no README at the fix commit), pilot copies identical; frozen in `data/readmes/` | **internet** | < 0.1 | `python -m cueinv.prefetch_readmes --campaign main --models qwen2.5-coder:7b-instruct-q4_K_M qwen3:4b` |
  | 1 ✅ 2026-11-24 10:31 → 2026-11-25 02:18 UTC (15 h 47 min, exit 0) | external labels, primary model — 46/46 projects, 2,180 calls (2,148 ok, 30 truncated at 2,048 tokens, 2 parse_failed = 1.5 %), 0 errors, 100 % GPU, one request setting; labels: 3,661 sources, 2,136 sinks, 13,476 taint-propagators | offline | 14.7 | `--external` |
  | 2a ▶ started 2026-11-25 10:16 UTC | primary internal: BASE×2, SHUF, R_NEU, D_STRIP, M_README | offline | 10.8 | `--conditions BASE:1,2 SHUF R_NEU D_STRIP M_README` |
  | 2b | primary internal: R_SYN, R_NEU2, R_MIS, D_MIS, M_ID, COMBO, H1C, ABLATION | offline | 10.8 | `--conditions R_SYN R_NEU2 R_MIS D_MIS M_ID COMBO H1C ABLATION` |
  | 3 | second model qwen3:4b: BASE×2, SHUF, R_NEU, D_STRIP, M_README | offline | ≤ 10.8 | `--model qwen3:4b --conditions BASE:1,2 SHUF R_NEU D_STRIP M_README` |
  | 4 | detection: BASE, SHUF, COMBO, H1C, ABLATION (needs 1, 2a, 2b) | offline | 7.5 | `--detect BASE SHUF COMBO H1C ABLATION` |
- [ ] 8.2 Primary model: external once + all internal conditions (BASE ×2)
- [ ] 8.3 Second model `qwen3:4b`: BASE ×2, SHUF, R_NEU, D_STRIP, M_README
- [ ] 8.4 Detection: BASE, SHUF, COMBO, H1C + internal-source ablation
- [ ] 8.5 Nightly `results/progress.md`

## Phase 9 — Analysis
- [ ] 9.1 `cueinv/analysis.py` retention / spurious / null / Δ
- [ ] 9.2 Bootstrap CIs, CR2 check, Holm, decision rule, regression
- [ ] 9.3 Detection McNemar + ablation + ground-truth recall + H1c trade-off
- [ ] 9.4 Figures 1–4 (PNG + PDF)

## Phase 10 — Kaggle anchor
- [ ] 10.1 `cueinv/export_prompts.py` → `kaggle/prompts.jsonl`
- [ ] 10.2 `kaggle/run_dsc7b.ipynb`
- [ ] 10.3 `cueinv/import_responses.py`

## Phase 11 — Write-up support
- [ ] 11.1 `paper/` LaTeX skeleton (ACM sigconf) + generated tables/figures
- [ ] 11.2 Threats to validity + related work sections

---

## Log
- 2026-11-20 — Phase 1 started. Env verified. IRIS cloned. Ollama serving. Downloads (CodeQL, Miniconda, JDK 8, Maven 3.5.0, models) running over a ~3 MB/s link. RECON draft written while waiting.
- 2026-11-20 — Phase 1 done (commit `bdf851a`): env, CodeQL, Ollama + models, VRAM check, spark quickstart end-to-end. Phase 2 done: RECON.md + tests. **Stopped at GATE 1.**
- 2026-11-20 — User answered GATE 1 Q1 (keep CVEs) and Q2 (recommended option). Project **paused** by user before Phase 3; resume notes in `memory.md`.
- 2026-11-21 — User approved the interception design (GATE 1 Q3) and asked to continue. Machine had restarted: Ollama relaunched in tmux with plan env vars. Docker Desktop not running yet.
- 2026-11-21 — Phase 3 (46-project subset, pilot DBs), Phase 4 (client, labelling) and Phase 5 (pilot, report, power, schema A/B) done. **Stopped at GATE 2** (no-go as designed; decisions D1–D3 in docs/GATE2.md).
- 2026-11-21 — User chose GATE 2 D1(a): free-form output + IRIS parse_json. Implementing and re-running the pilot as campaign `pilot-ff`.
- 2026-11-21 — Free-form re-pilot internal controls done (`results/pilot_report_pilot-ff.md`); external + detection still running in tmux `pilotff`. Stopped at GATE 2 for D2/D3.
- 2026-11-21 — GATE 2 passed (D2 recommended options). Starting Phase 6 (perturbation layer).
- 2026-11-21 — Phase 6 done (perturbation layer, 197 tests). No cue condition was run on real LLM outputs before PREREG (dry run only).
- 2026-11-21 — Phase 7 done: PREREG.md + frozen analysis, tagged prereg-v1. Stopped at GATE 3 (approval + public posting by the user).
- 2026-11-21 — User approved PREREG.md. Installed gh 2.105.0 + git-filter-repo (conda env `tools`). Waiting for GitHub sign-in to rewrite emails and push.
- 2026-11-21 — Published: https://github.com/senchinthan/iris-cue-invariance (`prereg-v1`). Next: user permission for the Docker image downloads (Phase 8 DB builds).
