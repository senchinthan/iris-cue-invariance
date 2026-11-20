# RECON — IRIS internals relevant to the cue-invariance study

IRIS clone: `iris/` at **`d2059f299a80a68ea4a0b0315eec2f42b8743776`** (branch `v2`, 2026-11-07, "Update codeql_vul.py (#81)").
All `file:line` references are relative to `iris/` at that SHA. Facts marked **[verified]** were confirmed by running the
spark quickstart (Phase 1.6); everything else is from reading the code.

---

## 1. Candidate extraction (external APIs and internal function parameters)

Both candidate sets come from CodeQL queries run by `CodeQLQueryRunner.run()` (`src/modules/codeql_query_runner.py:27-84`):
the query is copied into `output/<proj>/<run_id>/myqueries/<query>/`, `codeql pack install` is run, then
`codeql query run` → `results.bqrs` → `codeql bqrs decode --format=csv` → `output/<proj>/<run_id>/<query>/results.csv`.

### 1a. External APIs — `src/queries/fetch_external_apis.ql`
* Selects every `Call` whose callee is not in junit/hamcrest/mockito (`:3-10`), has a non-empty signature and is not on `Object` (`:46-48`).
* Raw CSV `output/<proj>/<run_id>/fetch_external_apis/results.csv` (`iris.py:196`), columns (`fetch_external_apis.ql:50-61`):
  `callstr, package, clazz, full_signature, internal_signature, func, is_static, file, location, parameter_types, return_type, doc`.
* Filtering in `collect_invoked_external_apis()` (`iris.py:343-373`):
  1. `keep_external_packages()` drops rows whose `package` is listed in `data/package-names/<proj>.txt` (`iris.py:287-290`).
  2. `api_is_candidate()` (`iris.py:332-341`): blacklist (`:323-330`), then non-trivial parameter **or** non-trivial return (`:303-321`, `PRIMITIVE_TYPES` at `:45-53`).
  3. Keeps `package, clazz, func, full_signature` and `drop_duplicates()` (`:365`).
* Filtered CSV: `output/<proj>/<run_id>/analysis/common/candidate_apis.csv` (`iris.py:197`, written `:371`).

### 1b. Internal function parameters — `src/queries/fetch_func_params.ql`
* Selects callables that are `fromSource()`, `isPublic()`, have ≥1 parameter, **are called from `src/test`** (`isTested`, `:12-17`)
  and **are not called from any non-test code** (`isNotInvokedByInternalFunction`, `:19-24`) (`:40-47`).
  → The internal candidate set is "public, test-exercised, never internally called" methods, i.e. the library's API surface.
* `full_signature` = `"<RetType> <name>(<T1> <p1>, <T2> <p2>, …)"` (constructors omit the return type) — **parameter names are in the signature** (`:3-10`).
  Types are **simple names** (`getType().getName()`), e.g. `void attribute(String attribute, Object value)`; type variables appear as `T` **[verified]**.
* `doc` = all Javadoc child texts joined by a space (`:31-38`).
* Raw CSV `output/<proj>/<run_id>/fetch_func_params/results.csv` (`iris.py:200`), columns (`:48-57`):
  `package, clazz, func, full_signature, internal_signature, location, parameter_types, return_type, doc`.
* Filtering in `collect_internal_function_parameters()` (`iris.py:396-426`):
  1. `keep_internal_packages()` keeps rows whose package **is** in `package-names/<proj>.txt` (`:292-295`).
  2. `func_parameter_is_candidate()` (`:388-394`): drop `isEqual/toString/equals/canConvert/compareTo/compare` and `src/test` locations (`:380-386`), require ≥1 non-primitive param (`:375-378`).
  3. Keeps `package, clazz, func, full_signature, doc` (`:418`).
* Filtered CSV: `output/<proj>/<run_id>/analysis/common/source_func_param_candidates.csv` (`iris.py:201`, written `:424`).
* **Prompt-time dedup** (`fetch_func_param_src_candidates()`, `iris.py:700-723`): one row per `(package, clazz, func)`; overloads collapse
  to the one with a non-empty doc, else the one with the longest signature. Order = first-occurrence order in the CSV.
  → The unit the LLM labels is a *method name*, not an overload. Our "internal candidate" = this deduplicated row; our "internal
  parameter" = a `(package, class, method, param_name)` tuple from the chosen signature.

**Spark quickstart numbers [verified]** (`runs/quickstart-spark/quickstart_iris.log`): 5265 raw external calls → **553** external
candidates (19 batches of 30); 138 raw internal rows → **106** candidates → **79** after dedup (71 with a doc) → 4 batches of 20.
Internal share of LLM-labelled rows = 79 / (553 + 79) = 12.5 %; internal share of *calls* = 4 / 23.

**Prerequisite:** `data/package-names/<proj>.txt` must exist or both stages crash (`open()` at `iris.py:288,293`). It exists for
126/213 projects overall and **65 of 67** "CWE-022/CWE-078" slugs (see §9). `scripts/get_packages_codeql.py` regenerates it,
but it expects the DB at `data/codeql-dbs/<proj>` (no `-docker` suffix) and sources at `data/project-sources/<proj>` (`:118-121`).

## 2. README summary

* `fetch_project_description_from_readme()` (`iris.py:661-690`): **no LLM / summary model is involved.** It downloads the README over
  HTTP from `raw.githubusercontent.com/<github_username>/<github_repository_name>/<commit>/<README.md|README.adoc|README|readme.md|readme>`
  (`:639`), trying each **fix** commit in `fix_commit_ids` order (`:677-681`), then `master` (`:684`).
  Note: README comes from the **fix** commit, not the buggy commit.
* Summary = `first_project_description_paragraph()` (`:615-635`): a heuristic that keeps lines starting with a letter, collapses blank
  runs, and stops after 10 kept lines (no truncation by characters).
* Cache: `output/<proj>/<run_id>/analysis/<query>/logs/label_func_params/readme_head.txt` (and the full `readme.txt`) (`:650-653`).
  Reused when present unless `--overwrite` (`:663`). The cache is **per run_id**, so every new run_id re-downloads it.
* If every fetch fails, the function returns `None` and the prompt literally contains `None` as the summary (`:690`, used at `:750`).
* **[verified] Leak risk:** for spark the first fix commit's README head says *"Important - There is a vulnerability in older versions
  of Spark (versions lower than 2.5.2). Please upgrade to the latest version."* — a post-fix README can carry vulnerability cues into the
  BASE internal prompt. This matters for the `M_README` channel (removing the README also removes the leak); see GATE 1 question Q2.
* The head can include code lines (spark's contains `import static spark.Spark.*;`, `public class HelloWorld {`) because the filter
  only requires a line to start with a letter.
* `--no-summary-model` (`iris.py:858`) is unrelated: it disables the LLM *taint-propagator* summaries in `MySummaries.qll`.

## 3. Prompt construction

Templates: `src/prompts.py`. Builders: `src/iris.py`.

| | External (`API_LABELLING_*`, `prompts.py:1-32`) | Internal (`FUNC_PARAM_LABELLING_*`, `prompts.py:34-67`) |
|---|---|---|
| Builder | `query_gpt_for_api_src_tp_sink_batched()` `iris.py:528-613`, batch fn `:558-576` | `query_gpt_for_func_param_src()` `iris.py:725-785`, batch fn `:741-759` |
| Row fields | `package,clazz,func,full_signature` joined by `,` (`:561`) | `package,clazz,full_signature,doc` (`:744`) — note: the header says `Package,Class,Method,Doc` but the "Method" column is the **full signature with parameter names**; the bare method name is *not* a separate field |
| Other fields | CWE `long_desc`, `examples` (JSON), `desc`, `cwe_id` from `src/queries.py` (`:553-555`) | `project_username`, `project_name`, `project_readme_summary` (`:730-732`, `:747-751`) |
| Project identity | **absent** | **present twice**: `"You are analyzing the Java package {owner}/{name}"` and `"…dependent on {name}"` (`prompts.py:54,60`). `owner = slug.split("_")[0]`, `name = slug.split("_")[2]` (`iris.py:731-732`) — breaks on repos whose name contains `_` |
| README | absent | present (§2) |
| Order | `sorted(set(candidates) − cached)` (`:440`) — **alphabetical**, and depends on the cross-project cache (§4) | CSV first-occurrence order after dedup (`:716`) |
| Batch size | CLI `--label-api-batch-size` default **30** (`:1330`); constructor default 30 (`:64`) | CLI `--label-func-param-batch-size` default **20** (`:1331`); **constructor default 50** (`:65`) — the CLI and the class disagree, so any harness that builds `SAPipeline` directly must pass 20 explicitly |
| Prompt dump | `analysis/<query>/logs/label_apis/raw_user_prompt_<i>.txt` (`:570`) | `analysis/<query>/logs/label_func_params/raw_user_prompt_<i>.txt` (`:752`) |

* JavaDoc truncation: `MAX_DOC_LENGTH = 50` (`iris.py:55`); `extract_doc()` keeps the first 50 characters and appends `"..."` (`:692-698`).
  Example row **[verified]**: `spark,Request,String params(String param),Returns the value of the provided route pattern pa...`
* Both use `[system, user]` message pairs; the system prompts contain the JSON output format.
* Sizes **[verified, spark]**: external user prompts ≤ 4.5 KB; internal user prompts 3.2–3.8 KB (≈ half of it README).
  Ollama reported 1120–2121 total tokens (prompt + completion) per call, all `truncated = 0` — IRIS's unset `num_ctx` (4096)
  happened to suffice for spark, but nothing guarantees it for projects with long READMEs or signatures.

## 4. Response parsing, label storage, and spec generation

### Parsing
* `parse_json()` (`iris.py:484-526`): strips ```` ```json ```` fences, `\'`, literal `\n`, **everything after `//` on a line**, and `""`;
  takes the first greedy `\[[\s\S]*\]` match and `json.loads` it; on failure falls back to `json.loads` on every `{[^}]*}`.
  Failure → `[]` (silently: that batch contributes no labels).
* External results: `filter_invalid_entries()` (needs non-empty `method, class, package, signature`, `:787-791`) then
  `merge_llm_labeled_apis_and_cache()` (`:443-457`) keeps **only items whose `(package,class,method,signature)` exactly matches a
  candidate** → hallucinated or re-worded signatures are dropped.
* Internal results: **no validation against the batch.** Every dict the model returns is dumped (`:776-783`) and later turned into a
  CodeQL source if it has `method/class/package/signature` (`:802`). Hallucinated methods silently become (inert or wrong) specs.
* `query_gpt_for_func_param_src` writes `response + "\n"` (`:772`) — a `None` response (Ollama error, `ollama.py:65-67`) crashes the stage.

### Label files (per project, per run_id, per query)
`output/<proj>/<run_id>/analysis/<query>/` (`iris.py:183-211`):
* `llm_labelled_source_apis.json`, `llm_labelled_sink_apis.json`, `llm_labelled_taint_prop_apis.json` — lists of
  `{package, class, method, signature, sink_args, type}` (`:605-607`).
* `llm_labelled_source_func_params.json` — list of `{package, class, method, signature, tainted_input: [<param names>]}` (`:783`).
* Cross-project external cache: `output/common/<run_id>/cwe-<id>/api_labels_<llm>.json` (`:258-261`), read at `:428-441`,
  written at `:459-482`. Items not returned by the LLM are cached as `type: "none"`. **The cache is shared by all projects that
  use the same run_id**, so external batch composition depends on which projects ran earlier.

### Reuse / overwrite flags
| Artefact | Reused when it exists unless… |
|---|---|
| raw `fetch_external_apis` / `candidate_apis.csv` | `--overwrite` or `--overwrite-api-candidates` (`:347,355`) |
| raw `fetch_func_params` / `source_func_param_candidates.csv` | `--overwrite` or `--overwrite-func-param-candidates` (`:400,408`) |
| external label JSONs | `--overwrite` or `--overwrite-labelled-apis` (`:532`); `--overwrite-llm-cache` ignores the common cache and then batches in CSV order instead of sorted order (`:543-546`) |
| **`llm_labelled_source_func_params.json`** | `--overwrite` or `--overwrite-labelled-func-param` (`:727`) |
| README head | `--overwrite` (`:663`) |
| `results.sarif` | `--overwrite` or `--overwrite-cwe-query-result` (`:1020`) |

### Spec generation (`build_project_specific_query()`, `iris.py:992-1014`)
`__main__` forces `use_exhaustive_qll = True` (`iris.py:1361`), so:
* `output/<proj>/<run_id>/myqueries/<query>/MySources.qll` — `isGPTDetectedSource` = OR of
  `QL_METHOD_CALL_SOURCE_BODY_ENTRY` (external source APIs: callee name + `hasQualifiedName(package, class)`) and
  `QL_FUNC_PARAM_SOURCE_ENTRY` (internal: `p.getCallable().getName() = method`, declaring type `hasQualifiedName(package, class)`,
  `p.getName() = "<tainted_input name>"`) (`iris.py:793-846`, templates `src/codeql_queries.py:43-59`). Split into
  300-entry sub-predicates if large (`:822-835`).
  → **Internal sources are matched by the real parameter *name*, method name and class name**, so any rename must be mapped back exactly.
* `MySinks.qll` (`:876-930`): sink APIs with `sink_args` parsed as `pN`/`this` only; anything else → `1 = 0` (inert).
* `MySummaries.qll` (`:855-874`): taint-propagator steps.
* `specs.model.yml` (`:939-990`) is written but **inert**: the generated `qlpack.yml` (`:264-276`) declares no `dataExtensions`, and the
  code says `# NOT WORKING YAML` (`:1012`). (It also assumes `pN` parameter names, `:962`, which conflicts with the QLL.)
* The CWE query (`src/cwe-queries/cwe-022/cwe-022wLLM.ql` + `MyTaintedPathQuery.qll:18-37`; CWE-78:
  `cwe-078/CommandInjectionRuntimeExecwLLM.ql` + `MyCommandInjectionRuntimeExec.qll:10-29`) uses exactly
  `isGPTDetectedSource / isGPTDetectedSink / isGPTDetectedStep`, plus fixed barriers (primitive/boxed/number types; CWE-22 adds `PathInjectionSanitizer`).

## 5. Ollama backend defaults

`src/models/ollama.py`:
* `_OLLAMA_DEFAULT_OPTIONS = {temperature: 0.8, num_predict: -1, stop: None, seed: 0}` (`:18-23`) — **matches the expectation (0.8, seed 0).**
* Constructor overwrites keys that appear in kwargs (`:35-37`), mutating the module-level dict. `SAPipeline.get_model()` passes
  `{"seed": self.seed, "max_new_tokens": 2048}` (`iris.py:280`), so the **effective seed is 1234** (`--seed`, `iris.py:1329`) and
  **temperature stays 0.8**; there is **no CLI flag for temperature**. `max_new_tokens` is not an Ollama key and is ignored.
* **`num_ctx` is never set** → the server default applies. Ollama 0.40.16 on this GPU logs
  `vram-based default context … default_num_ctx=4096` **[verified, `logs/ollama_serve.log`]**. Prompts above 4096 tokens are
  truncated by the server, without any error on the client side.
* No `top_k/top_p`, no `format`, no thinking control, no `num_gpu` → with the default fit only 25/29 layers of the 7B model are
  offloaded on this GPU (19 % CPU) **[verified, `docs/VERSIONS.md` VRAM table]**. Our client sets `num_gpu=99`.
* Model names are a closed map (`:8-14`): `ollama-qwen-coder → qwen2.5-coder:latest`, etc. `qwen2.5-coder:7b-instruct-q4_K_M`
  and `qwen3:4b` are not listed. The `--llm` value must start with `ollama` (`src/models/llm.py:237`).
* `OLLAMA_HOST` must be set or the client is never created (`:29-32`).
* Requests are sent via `thread_map(..., max_workers=num_threads)` (`:42-48`, `--num-threads` default 3); with
  `OLLAMA_NUM_PARALLEL=1` the server serialises them. Output order is preserved.
* `src/models/llm.py:1-3` imports `transformers` and `torch` at module load even for Ollama, so the IRIS env needs them.
* Override options for us: we do **not** need to patch this file (see §8). For a plain-IRIS run at temperature 0, a wrapper can set
  `models.ollama._OLLAMA_DEFAULT_OPTIONS["temperature"] = 0` before `get_model()`. (The module is imported as `models.ollama`, not
  `src.models.ollama`, because `llm.py:238` uses the `src/`-relative path.)

## 6. `--filter-by-module(-large)`

* Both are `store_true` (default **off**) on the CLI (`iris.py:1335-1336`) and `False` in the constructor (`:74-75`).
* When on, they keep only candidates located in the fixed module(s), derived from `data/fix_info.csv` (`:158-168`, `:297-301`,
  used at `:334-336` and `:390`) — i.e. they use ground-truth fix locations. **Our harness passes `False` explicitly** and asserts it.
* Other uses of fix info: the evaluation (§7) and the posthoc filter only with `--posthoc-filtering-skip-fp`
  (`contextual_analysis_pipeline.py:689-691`). We skip the posthoc filter.

## 7. Detection scoring

* `find_vulnerability()` (`iris.py:1016-1076`): `codeql database analyze --rerun` with search paths `codeql/qlpacks` and the custom
  pack → `output/<proj>/<run_id>/<query>/results.sarif` and `results.csv`.
* `post_process_cwe_query_result()` (`:1156-1189`): drops code flows with any node in `src/test`, or whose sink source line contains
  `.println(`/`.print(` (read from `data/project-sources/<proj>`; **if sources are absent the println check silently passes**, `:1120-1133`);
  drops alarms with no flows → `results_pp.sarif`.
* Stage 8 posthoc filter is skipped by `--skip-posthoc-filter` (`:1193-1195`).
* `evaluate_result()` → `EvaluationPipeline.evaluate_sarif_result()` (`src/modules/evaluation_pipeline.py:193-251`) on
  `results_pp.sarif` (`iris.py:1239`): fix locations = rows of `data/fix_info.csv` for this slug, excluding `src/test` (`:204-209`).
  A code flow (excluding flows whose first/last message contains `toString`/`println`, `:147-191`) is a true positive when
  * `recall_file`: any node's file is a fixed file;
  * `recall_method`: any node's enclosing `file:class:method` (from `fetch_class_locs` / `fetch_func_locs`) is a fixed method.
  Detection for a CVE = `vanilla_result.recall_method == True` (the paper's metric; `recall_file` is also stored).
* Output: `output/<proj>/<run_id>/<query>-final/results.json` = `{statistics, vanilla_result{num_results, num_paths, recall_file,
  num_tp_paths_file, num_tp_results_file, recall_method, num_tp_paths_method, num_tp_results_method}}` (`evaluation_pipeline.py:279-310`).
* Bug: `self.overwrite = overwrite,` (`evaluation_pipeline.py:51`) makes a 1-tuple, so `not self.overwrite` is always `False`. Harmless here
  (it just always recomputes).
* **[verified, spark]** Files produced: `cwe-022wLLM/results.sarif`, `results_pp.sarif`, `results.csv`; `cwe-022wLLM-final/results.json` =
  `{"statistics": {...}, "vanilla_result": {"num_results": 2, "num_paths": 5, "recall_file": false, "recall_method": false, ...}}`.
  The qwen2.5-coder-7B baseline does **not** detect CVE-2018-9159 (0 of 5 paths pass the fixed method). `fetch_func_locs` and
  `fetch_class_locs` are computed lazily in stages 7 and 9 (≈ 22 s each on spark).
* CodeQL warns on every `pack install` that the generated `qlpack.yml` will be rejected in a future strict mode (no `--no-strict-mode`
  needed for 2.23.2).

## 8. Proposed interception design (no IRIS core changes)

**Principle:** IRIS stays the source of truth for candidate extraction, prompt templates, the external prompt path, spec generation,
CodeQL and scoring. Our harness (`cueinv/`) owns the LLM calls and the internal prompts, and it talks to IRIS only through
(a) Python import, (b) two instance attributes, and (c) the label files IRIS already knows how to resume from.

```
cueinv.iris_bridge.make_pipeline(slug, cwe, run_id)            # SAPipeline(..., use_container=True,
   │                                                           #   label_api_batch_size=30, label_func_param_batch_size=20,
   │                                                           #   filter_by_module=False, filter_by_module_large=False,
   │                                                           #   skip_posthoc_filter=True, seed=…)
   ├─ Stage 1-2 (IRIS, once per project, run_id = "<slug>/shared"):
   │    p.collect_invoked_external_apis(); p.collect_internal_function_parameters()
   │    → candidate_apis.csv, source_func_param_candidates.csv (+ fetch_func_locs / fetch_class_locs for scoring)
   ├─ External labels (IRIS code path, our model, once per project × model):
   │    p.model = cueinv.llm.IrisModelAdapter(client, condition="EXT", …)   # SAPipeline.get_model() returns self.model if set (iris.py:278-281)
   │    p.query_gpt_for_api_src_tp_sink_batched()                          # IRIS builds the prompts verbatim and parses with its own parse_json/merge
   │    → llm_labelled_{source,sink,taint_prop}_apis.json  (common cache isolated per project so batches don't depend on run order)
   ├─ Internal labels (ours, per condition × rep × model):
   │    rows   = p.fetch_func_param_src_candidates()                       # IRIS dedup + extract_doc (MAX_DOC_LENGTH=50)
   │    readme = p.fetch_project_description_from_readme()                 # cached once per project, then reused
   │    perturbed rows/readme/identity + rename map  ← cueinv.perturb
   │    prompt = FUNC_PARAM_LABELLING_USER_PROMPT.format(…)                # imported from src.prompts, never copied
   │    response → parse (strict + IRIS parse_json fallback) → validate vs batch → inverse-rename
   │    → write llm_labelled_source_func_params.json in IRIS's exact schema
   └─ Detection (IRIS, only for BASE / SHUF / COMBO / H1C / ablation):
        per-condition run_id dir pre-seeded with the shared stage 1-2 CSVs + external label JSONs + our internal JSON
        p.build_project_specific_query(); p.find_vulnerability(); p.post_process_cwe_query_result(); p.evaluate_result()
        → <query>-final/results.json (recall_method)
```

Why this works without patches:
* `query_gpt_for_func_param_src()` skips itself when `llm_labelled_source_func_params.json` exists (`iris.py:727`), and
  `build_source_qll_with_enumeration()` reads it back (`:802-817`). Writing that file is the whole interface for internal labels.
* `get_model()` only builds an IRIS model when `self.model is None` (`:278-281`), so injecting an adapter with a
  `predict(prompts, batch_size, no_progress_bar)` method sends IRIS's own external prompts through our deterministic, cached client.
  We never touch `_OLLAMA_DEFAULT_OPTIONS`, `num_ctx` defaults or the closed model-name map.
* We call stage methods directly instead of `run()`, which avoids its `exit(1)` on cached results (`:1290`). `SystemExit` raised by IRIS
  helpers (`codeql_query_runner.py:37-80`) is caught and recorded as a project failure.
* Per-condition isolation comes from the run_id → `output/<proj>/<run_id>/…` (`iris.py:171`); with `use_container=True` IRIS appends
  `-docker` to both the run_id and the DB name (`:103`, `:174`).
* Ablation (external specs only) = write `[]` to `llm_labelled_source_func_params.json` for that run_id.
* Required env: `iris/` and `iris/src/` on `sys.path` (IRIS mixes `src.models.*` and `models.*` imports), `OLLAMA_HOST` unset is fine
  for us (we don't use the IRIS Ollama class), conda env with `torch`/`transformers` importable.

What the harness must replicate exactly (and test):
* The internal row format `",".join([package, clazz, full_signature, doc])` (`iris.py:744`) and batching
  `candidates[i:i+20]` (`:743`, `:765`).
* The output JSON schema `{package, class, method, signature, tainted_input}` with **original** names.

Evidence that the premises hold — `cueinv/tests/test_recon_invariants.py` (all passing):
* `test_reproduce_iris_internal_prompts_byte_for_byte`: rebuilding the 4 spark internal prompts from IRIS's
  `fetch_func_param_src_candidates()` + `FUNC_PARAM_LABELLING_USER_PROMPT` reproduces the prompts IRIS actually sent, **byte for byte**.
* `test_get_model_returns_injected_model`, `test_internal_stage_skips_when_label_file_exists`,
  `test_source_qll_consumes_our_label_schema`: the three hooks above behave as described.
* `test_batch_size_defaults_constructor_vs_cli`, `test_doc_truncation_is_50_plus_ellipsis`, `test_prompt_template_fields`,
  `test_ollama_backend_defaults_from_source`, `test_filter_by_module_off_by_default`: the facts in §3, §5, §6.

Build route: the plan's Docker policy (pull the v2 image → `scripts/build_codeql_dbs.py --use-container` → `docker rmi`) for every
subset project, plus a native shallow clone (`scripts/fetch_one.py`, no container) for cloc and the println post-filter. The native
build also worked for spark (JDK 8 / Maven 3.5.0), but other projects need JDK 11/17 and Maven/Gradle versions not installed here.

## 9. Data-file quirks found (affect Phase 3)

* **Pool size**: `project_info.csv` labels CWEs inconsistently: `CWE-022`×55, `CWE-078`×12, plus `CWE-22`×5 and `CWE-78`×1
  (2025 additions). Total **73**, which matches the plan once both spellings are counted.
* **The 6 newer slugs are inconsistent across files**: `project_info.csv` uses `jena__CVE-2025-49656_jena-5.4.0` (double underscore,
  no repo part), but `build_info.csv`, `IRIS_docker_build_durations.csv`, `fix_info_source_sink.csv` and the Docker Hub tags use
  `jena_CVE-2025-49656_jena-5.4.0`. With either spelling `SAPipeline` derives the CVE as `slug.split("_")[3]` (`iris.py:148`), which is
  not a CVE ID for these slugs, so `.iloc[0]` at `:154` fails. None of the six has `package-names/<slug>.txt`. They need build tools
  not in `dep_configs.json` (Maven 3.9.2/3.9.10, Gradle 8.13), i.e. container route only.
* **Annotated set overlap**: `fix_info_source_sink.csv` covers 50 CVEs, but only **4** are CWE-22/78 (jena CVE-2025-49656,
  DSpace CVE-2025-53622, conductor CVE-2025-26074, solon CVE-2025-46096), and all four are in the inconsistent-slug group above.
  The ground-truth recall endpoint therefore has n ≤ 4 and needs slug handling (see GATE 1 question).
* `fetch_one.py --from-container` runs `docker pull irissast/cwe-bench-java-containers:<slug>` (**v1** repo, `:41-47`) and then
  `ensure_image(...-v2:<slug>)` (`:53-57`): two images per project. It also returns early without writing `data/project-sources/<slug>`.
  We will call `scripts/build_codeql_dbs.py --use-container` directly (v2 image only) and fetch sources natively (`fetch_one.py` without
  `--from-container`, a shallow git clone) for cloc and for the println post-filter.
* `build_codeql_dbs.py --use-container` copies the whole CodeQL bundle into the container on every call (`:321`) and re-clones the repo
  inside the container (`:325-343`).

## 10. Differences between the plan and the repo

| Plan says | Repo says | Action |
|---|---|---|
| Internal batch 20 | CLI 20, **constructor 50** | Pass 20 explicitly |
| `MAX_DOC_LENGTH = 50` | 50 (+ `"..."` suffix) | Same |
| Ollama defaults temp 0.8, seed 0 | Same in `ollama.py`, but IRIS passes seed 1234 | Our client sets every option |
| README summary maybe via a summary model | Heuristic first-paragraph extraction, no LLM; fetched from the fix commit | Cache once per project |
| 60 + 13 = 73 CWE-22/78 CVEs | 73, with two CWE spellings and 6 inconsistent slugs | Handle in `select_subset.py` |
| CodeQL 2.23.2 / queries 1.8.1 | `config.py:38` = 1.8.1; needs the **bundle** (qlpacks), not the bare CLI zip in `Dockerfile` | Installed bundle v2.23.2 |
| `qwen2.5-coder:7b-instruct-q4_K_M` | Not in IRIS's Ollama name map | Irrelevant for our client; for the plain quickstart, alias via `ollama cp` |

## 11. Decisions needed at GATE 1

**Q1 — The 4 annotated CWE-22/78 CVEs crash `SAPipeline.__init__`.** All four (jena, DSpace, conductor, solon — §9) have slugs for
which `slug.split("_")[3]` is not a CVE ID (`iris.py:148`). Options:
* **(a, recommended)** one-line hook in `patches/0002-cve-id-from-slug.patch`: derive `cve_id` with `re.search(r"CVE-\d{4}-\d+", slug)`
  and use the `build_info.csv` / Docker-tag spelling (`jena_CVE-…`) as the slug. Plus generate their `package-names/*.txt` with
  `scripts/get_packages_codeql.py`. Keeps the ground-truth recall endpoint (n = 4).
* (b) drop the 6 inconsistent-slug CVEs and report ground-truth recall as not estimable for CWE-22/78.

**Q2 — README comes from the fix commit and can leak the vulnerability** (§2; spark's says "There is a vulnerability in older versions").
* **(a, recommended)** keep IRIS's behaviour for BASE (fidelity to IRIS as deployed) and pre-register a per-project covariate
  `readme_mentions_vuln` (keyword scan: vulnerab|CVE|security|exploit|upgrade), reported alongside the `M_README` result.
* (b) fetch the README from the buggy commit in every condition (cleaner, but no longer IRIS's prompt).

**Q3 — Approve the interception design in §8** (no IRIS core edits; model injection for external labels; our own internal prompts;
IRIS resumes from `llm_labelled_source_func_params.json`).

**For information (no decision needed):** `num_ctx=8192` with `num_gpu=99` (§5, `docs/VERSIONS.md`); the GPU is throttled by
Windows (`0x24` = SW power cap + SW thermal), so please put the laptop on AC power with a performance profile before the pilot.
