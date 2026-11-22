# Deviations after pre-registration (`prereg-v1`)

Changes made after the pre-registration was frozen. Each entry says when it was made and whether any main-run (Phase 8)
data existed at the time. `docs/PREREG.md` §10 lists the deviations from the plan that were decided before registration.

## D-1 — package-names fallback for conductor (2026-11-22, before any Phase 8 data)

* **What:** IRIS decides which code is "internal" using `data/package-names/<slug>.txt`. For projects without that file we
  generate it with IRIS's own helpers, which keep the DB packages that start with the Maven/Gradle groupId.
  Conductor's groupId (`org.conductoross`) matches none of its packages (its code is in `com.netflix.conductor`), so the file
  came out empty. With an empty file, IRIS would find no internal candidates, and the project would be excluded under E2.
* **Rule added:** when the groupId matches nothing, use the most common 3-segment package prefix in the DB, provided it covers
  at least 50 % of the DB's packages (`cueinv/build_dbs.py: dominant_prefix`). IRIS's own `get_packages_codeql.py` has an
  `--internal-package` option for this case. The rule looks at the DB alone, never at the fix location.
* **Effect:** conductor gets 143 internal packages (`com.netflix.conductor.*`). As a sanity check after the fact, both
  fix-location packages (`core.events`, `sdk.workflow.def.tasks`) are in the set. No other project in the subset has needed it so far.
* **Confirmatory analysis code:** unchanged.

## D-2 — groupId from a Gradle `buildSrc` plugin for djl (2026-11-22, before any Phase 8 data)

* **What:** djl declares `group = "ai.djl"` in a convention plugin (`buildSrc/src/main/kotlin/ai/djl/javaBase.gradle.kts`).
  IRIS's `find_gradle_group_id` only reads the top-level `build.gradle[.kts]`, so it found no groupId, and the package-names
  file was not generated.
* **Rule added:** if IRIS's helpers find no groupId, search `buildSrc/**/*.gradle*` with IRIS's own regex
  (`cueinv/build_dbs.py: buildsrc_group_id`). This finds the project's real groupId.
* **Effect:** djl gets 171 internal packages (`ai.djl.*`), including `ai.djl.util`, where the fix is (sanity check only).
