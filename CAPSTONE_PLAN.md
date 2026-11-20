# Capstone implementation plan: cue-invariance of LLM-inferred taint specs in IRIS

You are Claude Code, working in WSL2 Ubuntu on the user's laptop. Follow this plan phase by phase. Stop at every **GATE** and wait for the user before going further.

---

## 0. Context (read first)

**Research question.** IRIS (Li, Dutta, Naik; ICLR 2025; github.com/iris-sast/iris) asks an LLM to label which APIs are taint *sources* and *sinks*, then CodeQL traces taint paths. We test whether the LLM's labels for **internal-API sources** stay stable under semantics-preserving changes to the *prompt*. The changes are:
- renaming identifiers;
- stripping or altering JavaDoc;
- stripping the README summary;
- masking the project's identity.

The code itself is never changed.

**Primary endpoint.** Retention of internal source labels under each perturbation. It is compared against two null controls:
- (a) a **reshuffle** control: same inputs, different batch composition and order;
- (b) a **repeat** control: an identical second run, which measures inference nondeterminism.

**Pre-registered rule, per perturbation channel.**
- **Supported:** the retention drop beyond the larger of the two null controls is ≥ 10 percentage points, and the 95% CI excludes 0 (Holm-corrected across channels).
- **Falsified:** the drop is within ±3 points.
- **Inconclusive:** anything else.

**Secondary endpoints.**
- Pre-filter CodeQL detection for 4 conditions, tested with exact McNemar.
- An internal-source ablation: what share of baseline detections needs internal sources.
- Recall of the annotated true sources in `fix_info_source_sink.csv`.

**Mitigation (H1c).** Cue-diverse voting: a parameter is a source only if at least 2 of 3 views agree (original, neutral-renamed, doc-stripped). This *adapts* the existing majority-voting ideas of AdaTaint (arXiv 2511.04023) and QLPro (arXiv 2506.23644). Do not describe voting as novel anywhere in code comments or docs.

**Hardware.**
- RTX 3060 Laptop GPU with 6 GB VRAM.
- 16 GB system RAM; WSL2 is capped at 12 GB.
- No model training anywhere in this project.

**Scope.**
- CWE-22 and CWE-78 CVEs from CWE-Bench-Java v2 (60 + 13 = 73 in the pool).
- Keep the ones that build successfully, have ≤ ~150K SLOC, and build in ≤ 15 min.
- Target about 50 projects; pilot on 5.

**Models.**
- Primary: `qwen2.5-coder:7b-instruct-q4_K_M` (Ollama, about 4.7 GB).
- Second model: `qwen3:4b` with thinking disabled, used for internal labelling only.
- Anchor: DeepSeekCoder-7B, run on Kaggle (T4) from exported prompts only.

---

## Global rules for Claude Code

1. **Don't touch IRIS core.** Write our code in `~/capstone/cueinv/`. If IRIS needs a hook, keep it minimal, put it in `~/capstone/patches/*.patch`, and document it in `docs/PATCHES.md`.
2. **Log every run.** Each run writes `runs/<run_id>/config.json` with:
   - the IRIS git SHA and our git SHA;
   - the Ollama version and model digest;
   - the condition, repeat index and seed;
   - the timestamp.
3. **Cache every LLM call.** Append each one to `runs/<run_id>/llm_calls.jsonl` with: prompt SHA-256, model, condition, rep, raw response, parsed labels, parse status and token counts. Runs must be resumable: skip calls whose `(prompt_hash, model, condition, rep)` is already cached.
4. **Ask before big downloads.** Ask the user before any single download over 5 GB, before pulling more than 5 Docker images at once, and before deleting anything outside `runs/tmp/`.
5. **Run long jobs in tmux.** Use `tmux new -s <name>` and log to a file.
6. **Test with pytest.** Every module gets tests in `cueinv/tests/`, and `pytest -q` must pass before each GATE.
7. **Commit often.** `~/capstone` is its own git repo. Commit after each phase with a clear message.
8. **Don't guess IRIS internals.** Read the code. When a fact here (paths, flags, defaults) disagrees with the repo, the repo wins: note the difference in `docs/RECON.md`.

---

## Phase W: manual Windows-side steps (the USER does these before pasting this plan)

1. Create `C:\Users\<you>\.wslconfig` with these contents, then run `wsl --shutdown` in PowerShell:
   ```
   [wsl2]
   memory=12GB
   swap=8GB
   processors=6
   ```
2. Install the latest NVIDIA Windows driver. Inside WSL, `nvidia-smi` must show the RTX 3060.
3. Install Docker Desktop with the WSL2 backend and enable integration for your Ubuntu distro, or install Docker Engine inside WSL.
4. Make sure at least 120 GB is free on the drive that holds the WSL virtual disk.

---

## Phase 1: environment (Week 1, day 1–2)

1. Verify the environment:
   - `nvidia-smi`, `docker run hello-world`, `free -g` (expect about 12 GB) and `df -h ~`;
   - Python 3.10+ and conda/miniconda (install Miniconda if it's missing).
2. Create the workspace:
   ```
   ~/capstone/
     iris/        # git clone https://github.com/iris-sast/iris (default branch v2)
     cueinv/      # our package
     patches/  runs/  results/  docs/  kaggle/
   ```
   Record the IRIS commit SHA in `docs/VERSIONS.md`.
3. Follow IRIS's own setup docs (`docs/environment-setup/`, README) for the conda env and CodeQL 2.23.2 / queries 1.8.1. Record the versions.
4. Install Ollama inside WSL (`curl -fsSL https://ollama.com/install.sh | sh`). Configure it through a systemd override, or export these before `ollama serve`:
   ```
   OLLAMA_NUM_PARALLEL=1
   OLLAMA_MAX_LOADED_MODELS=1
   OLLAMA_FLASH_ATTENTION=1
   OLLAMA_KV_CACHE_TYPE=q8_0
   ```
   Then pull the models: `ollama pull qwen2.5-coder:7b-instruct-q4_K_M` and `ollama pull qwen3:4b`. Record the `ollama --version` output and each model digest.
5. Run a VRAM check. Send one 4K-token prompt with `num_ctx=8192`, confirm with `nvidia-smi` that the model stays fully on the GPU, and log tokens/s. If it spills to the CPU, drop `num_ctx` to 6144.
6. Run IRIS's README quickstart end-to-end on `perwendel__spark_CVE-2018-9159_2.7.1`, using the Ollama backend at temperature 0 and with `--skip-posthoc-filter`. Use the container route if the native build fails.

**Done when:** the quickstart produces CodeQL results for spark, and `docs/VERSIONS.md` is filled in.

---

## Phase 2: code reconnaissance (Week 1, day 2–3) — read-only

Write `docs/RECON.md` with exact file:line references. It must answer:

1. Where external and internal candidates are extracted (the CodeQL queries, e.g. `fetch_func_params.ql`), and the CSV files and columns they produce.
2. How the README summary is produced and cached (`readme_head.txt`?) and whether a summary model is involved.
3. Where `API_LABELLING_*` and `FUNC_PARAM_LABELLING_*` prompts are built (`src/prompts.py`, `src/iris.py`). Record:
   - batch sizes (expected 30 external, 20 internal) and the CLI vs constructor defaults;
   - the JavaDoc truncation (expected `MAX_DOC_LENGTH = 50`);
   - which fields go into each row;
   - whether the project owner/name appears.
4. How LLM responses are parsed, and how labels become CodeQL specs (the `.qll`/`.ql` files generated). Note where on disk labels are cached and the flags that reuse or overwrite them.
5. The Ollama backend defaults (expected temperature 0.8, seed 0) and how to override them.
6. Whether `--filter-by-module(-large)` is off by default. It must stay off: it uses fix locations.
7. How detection is scored: fix-location matching and the output files.
8. **The cleanest interception point.** The preferred design is that our harness builds internal-label prompts itself (reusing IRIS's templates by import), calls the LLM, and writes labels in exactly the format IRIS expects. IRIS then resumes from "labels exist" to the CodeQL stage. Name the exact files and flags that make this work.

**GATE 1:** show the user RECON.md and the proposed interception design. Wait for approval.

---

## Phase 3: subset selection (Week 1, day 3)

`cueinv/select_subset.py` should:
1. Read `iris/data/project_info.csv` and `build_info.csv`, and keep CWE-22 and CWE-78 CVEs whose build succeeded.
2. Compute SLOC per project with `cloc` (installed via apt), and read build durations from `data/IRIS_docker_build_durations.csv` where present.
3. Filter to SLOC ≤ 150K and build ≤ 15 min, excluding known giants (wildfly, camel, flink, keycloak, glassfish, nifi, aws-sdk-java, …).
4. Write `results/subset.csv` with: slug, CWE, CVE year, SLOC, build minutes, and an in-annotated-set flag (whether the CVE is in `fix_info_source_sink.csv`).
5. Pick 5 pilot projects of different sizes, including both CWEs, and write them to `results/pilot.csv`.

Docker policy: pull one project image, build its CodeQL DB, then `docker rmi` it. Keep the DBs and delete the images.

---

## Phase 4: deterministic LLM client + baseline labelling (Week 1, day 3–5)

`cueinv/llm.py`:
- Calls the Ollama HTTP API with these options: `temperature=0`, `top_k=1`, `top_p=1`, fixed `seed`, fixed `num_ctx`, and `num_predict=2048`.
- Uses `format=<JSON schema>` (structured outputs) that matches IRIS's expected label JSON.
- Sends one warm-up call after each model load, and discards it.
- For `qwen3:4b`, disables thinking (`/no_think` or the API `think:false`).
- Retries once on a parse failure, then records `parse_failed`.
- Logs prompt and completion token counts.

`cueinv/label.py`:
- `label_external(project)` runs once per project and model, uses IRIS's external prompt verbatim, and is cached and reused by all conditions. Internal renaming leaves external prompts byte-identical, so assert that.
- `label_internal(project, condition, rep, model)` builds internal prompts through the perturbation layer (Phase 6), calls the LLM, maps labels back, and writes them in IRIS's format.

---

## Phase 5: PILOT GATE (end of Week 1)

On the 5 pilot projects, run:
- baseline rep 1;
- baseline rep 2 (the repeat control);
- reshuffle (seeded permutation of internal candidates into new batches).

`cueinv/pilot_report.py` writes `results/pilot_report.md` with, per project:
- the number of external and internal candidates, and the internal share. Nobody has published this, so it is a key output;
- baseline internal source labels, the repeat flip rate and the reshuffle flip rate;
- parse failure rate, mean seconds per call and VRAM peak;
- projected totals for the full subset, and projected machine-hours per condition.

Then rerun the power simulation (`cueinv/power.py`): a project-clustered bootstrap, with null retention taken from the pilot and true drops of 5, 10 and 15 points.

**GATE 2 (go/no-go):**
- **Go:** about 200 or more baseline internal source labels projected across the subset, and power ≥ 0.8 for a 10-point drop.
- **Otherwise:** pool the channels (combine rename variants and doc variants), widen the subset to all buildable CWE-22/78 CVEs, and/or add CWE-79. Report this to the user and wait.

---

## Phase 6: perturbation layer (Week 2)

`cueinv/perturb.py` turns the internal-candidate rows, README summary and project identity for a given `(condition, seed)` into perturbed prompt inputs and a **bijective rename map**.

| ID | Condition | Kind |
|---|---|---|
| BASE | original, reps 1 and 2 | control |
| SHUF | batch reshuffle, seeded | control |
| R_SYN | synonym-preserving rename (e.g. `readFile` → `loadFile`) via a fixed curated synonym table | realistic |
| R_NEU | neutral rename, length- and token-count-matched (`m_a1b2`), scheme A | stress |
| R_NEU2 | neutral rename, scheme B (different vocabulary) | stress |
| R_MIS | misleading-benign rename (e.g. `sanitizeInput`-style names on unrelated methods) | stress |
| D_STRIP | JavaDoc removed | realistic |
| D_MIS | JavaDoc replaced by a benign-sounding but wrong 50-char description | stress |
| M_README | README summary removed | realistic |
| M_ID | owner/name replaced by `projectX` | exploratory (contamination probe, demoted) |
| COMBO | R_NEU + D_STRIP | for the detection endpoint |
| H1C | vote of BASE, R_NEU and D_STRIP: source if ≥ 2 of 3 | mitigation |

Requirements:
- Rename the class, method and parameter names in the rows **and** every occurrence of them inside the JavaDoc and README text, or the doc channel leaks them. Keep Java keywords, JDK types and external types untouched.
- The rename map is per project and deterministic from the seed, and is saved to `runs/<run_id>/rename_map.json`.
- Mapping back: an LLM output that names an identifier not in the batch counts as a parse failure, and the failure rate is reported per condition.
- Log the prompt token delta against BASE for every batch, for the regression covariate.
- Tests (pytest), all of which must pass:
  - the rename → inverse round trip is the identity on 100% of the pilot candidates;
  - external prompts are byte-identical across conditions;
  - no original identifier string survives in a perturbed prompt (scan for it);
  - SHUF changes only batch membership and order.

---

## Phase 7: pre-registration (end of Week 2, BEFORE the main runs)

Write `docs/PREREG.md` containing:
- the hypotheses;
- the endpoint definitions (Phase 9);
- the conditions table;
- the decision rule (≥ 10 / ±3 points, Holm across channels);
- the exclusion rules;
- the pilot results;
- the analysis code path.

Commit it, `git tag prereg-v1`, and push it to a public GitHub repo, or upload it to OSF, so it has a timestamp. **GATE 3:** the user approves and confirms it is public.

---

## Phase 8: main runs (Weeks 3–6, overnight in tmux)

`cueinv/run.py --conditions ... --projects results/subset.csv --model ... --rep ...` is resumable and processes one project at a time.

1. Primary model: external labels once per project, then internal labels for every condition in the table, with BASE run twice.
2. Second model (`qwen3:4b`), internal labels only: BASE ×2, SHUF, R_NEU, D_STRIP, M_README.
3. Secondary detection, running IRIS's CodeQL stage with `--skip-posthoc-filter` for:
   - BASE, SHUF, COMBO, H1C;
   - plus an ablation: BASE with every internal source removed (external specs only).
4. A nightly `results/progress.md` showing projects done and failed, hours used, and the parse failure rate.

Estimated time is about 40–70 machine-hours in total; the pilot gives the real number.

---

## Phase 9: analysis (Weeks 7–8)

`cueinv/analysis.py` writes everything to `results/` and generates the tables from data.

Definitions, per project *p*, with S = the set of internal parameters labelled source:
- retention(c) = |S_BASE1 ∩ S_c| / |S_BASE1|
- spurious(c) = |S_c \ S_BASE1| / #internal candidates
- noise floor: null(p) = min(retention(BASE2), retention(SHUF))
- effect Δ(c) = mean over projects of [null(p) − retention_p(c)], in percentage points

Statistics:
- Paired project-level bootstrap (10,000 resamples) for the CI of Δ(c).
- A wild-cluster / CR2 check reported alongside.
- Holm correction across the realistic plus stress channels.
- Apply the decision rule to each channel.
- Regression: retention ~ condition + token_delta, with project-clustered SEs.
- Detection: per-CVE detected yes/no, compared with exact McNemar for BASE vs COMBO and BASE vs H1C. Report the share of baseline detections lost when internal sources are removed (the ablation).
- Ground-truth recall: for annotated CVEs in the subset, the share of true sources still labelled under each condition.
- H1c trade-off: source-label stability vs detections and alert-path counts (an FDR proxy).

Figures (matplotlib, saved as PNG and PDF):
1. Δ per condition with CIs, with realistic and stress conditions grouped.
2. Per-project retention distributions.
3. The H1c robustness–utility plot.
4. Retention vs token delta.

---

## Phase 10: Kaggle anchor (Week 7, in parallel)

1. `cueinv/export_prompts.py` dumps the BASE, R_NEU and D_STRIP internal prompts for the subset to `kaggle/prompts.jsonl`.
2. Write `kaggle/run_dsc7b.ipynb`. It installs Ollama or llama.cpp on a T4, loads DeepSeekCoder-7B-instruct (Q4_K_M) with the same decoding settings, and writes `responses.jsonl`. Keep each session under 12 h, chunked and resumable.
3. `cueinv/import_responses.py` feeds the responses through the same parsing and mapping-back path.

The user uploads the notebook and data to Kaggle. Claude Code only prepares the files.

---

## Phase 11: write-up support (Weeks 9–12)

- `paper/` holds the LaTeX skeleton (ACM sigconf) and the auto-generated tables and figures.
- Threats to validity to cover:
  - nondeterminism, handled by the repeat floor;
  - realism, handled by the realistic vs stress split;
  - construct validity, handled by the ablation;
  - one language and two CWEs;
  - toolchain drift: the paper used patched CodeQL 2.15.3, we use stock 2.23.2.
- Related work must cite:
  - IRIS;
  - Li et al. TSE 2026;
  - Cao et al. arXiv 2611.17123 (the analyzer-side counterpart);
  - AdaTaint and QLPro (voting);
  - FormalBench (spec robustness);
  - Zendel et al. CHIIR 2024 (batch effects);
  - Face/Off (name reliance);
  - SecLLMHolmes and ALIBI.
- Target venue: an FSE 2027 workshop (suggested deadline 11 Feb 2027; check each workshop's CFP). Post an arXiv preprint at submission.

---

## Week-by-week checklist

| Week | Deliverable | Gate |
|---|---|---|
| 1 | Env, RECON.md, subset, pilot report | GATE 1, GATE 2 |
| 2 | Perturbation layer + tests, PREREG | GATE 3 |
| 3–6 | Main runs (primary + second model) | — |
| 7 | Detection runs, ablation, Kaggle anchor | — |
| 8 | Analysis + figures | — |
| 9–12 | Paper draft, preprint, submission | — |

Start with Phase 1. Report what you did and any differences from this plan after each phase.
