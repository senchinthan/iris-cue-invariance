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
- [ ] 3.1 `cueinv/select_subset.py`: CWE-22/78 + build success
- [ ] 3.2 SLOC via cloc + build durations
- [ ] 3.3 Filter (≤150K SLOC, ≤15 min, no giants)
- [ ] 3.4 `results/subset.csv`
- [ ] 3.5 `results/pilot.csv` (5 projects, both CWEs, varied size)
- [ ] 3.6 CodeQL DBs for pilot (pull image → build DB → `docker rmi`)
- [ ] 3.7 Tests pass; Phase 3 commit

## Phase 4 — Deterministic LLM client + baseline labelling
- [ ] 4.1 `cueinv/llm.py` (temp 0, top_k 1, top_p 1, seed, num_ctx, num_predict 2048, JSON-schema format, warm-up, qwen3 no-think, retry-once, token logging, jsonl cache, resumable)
- [ ] 4.2 `cueinv/label.py` `label_external` (IRIS prompt verbatim, cached, byte-identity assert)
- [ ] 4.3 `cueinv/label.py` `label_internal` (via perturbation layer, map back, IRIS format)
- [ ] 4.4 Run logging (`runs/<run_id>/config.json`, `llm_calls.jsonl`)
- [ ] 4.5 Tests pass; Phase 4 commit

## Phase 5 — Pilot
- [ ] 5.1 Pilot runs: BASE rep1, BASE rep2, SHUF on 5 projects
- [ ] 5.2 `cueinv/pilot_report.py` → `results/pilot_report.md`
- [ ] 5.3 `cueinv/power.py` power simulation (Δ = 5/10/15 pts)
- [ ] 5.4 Tests pass; Phase 5 commit
- [ ] **GATE 2** — go/no-go (≥200 projected baseline internal source labels, power ≥0.8 at 10 pts)

## Phase 6 — Perturbation layer
- [ ] 6.1 `cueinv/perturb.py` conditions BASE, SHUF, R_SYN, R_NEU, R_NEU2, R_MIS, D_STRIP, D_MIS, M_README, M_ID, COMBO, H1C
- [ ] 6.2 Rename inside JavaDoc + README; keep keywords/JDK/external types; bijective map saved per run
- [ ] 6.3 Map-back failure accounting + token-delta logging
- [ ] 6.4 Tests: round-trip identity, external byte-identity, no surviving identifiers, SHUF only reorders
- [ ] 6.5 Phase 6 commit

## Phase 7 — Pre-registration
- [ ] 7.1 `docs/PREREG.md`
- [ ] 7.2 Commit + `git tag prereg-v1`
- [ ] **GATE 3** — user approves and confirms it is public (GitHub / OSF)

## Phase 8 — Main runs
- [ ] 8.1 `cueinv/run.py` (resumable, one project at a time, tmux)
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
