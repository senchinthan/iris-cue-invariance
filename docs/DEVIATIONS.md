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

## D-3 — custom build command for the two spring-cloud-config CVEs (2026-11-22, before any Phase 8 data)

* **What:** CodeQL's autobuild in the IRIS container fails for spring-cloud-config CVE-2020-5405 (both attempts, 09:51 and
  09:54): the parent `spring-cloud-build` runs Checkstyle even with `-Dcheckstyle.skip`, and the project pom sets
  `maven-checkstyle-plugin.failsOnViolation=true` with test sources included. Three `JavadocVariable` warnings in test files stop
  the build. CVE-2020-5410 (the same repository) is expected to fail the same way.
* **Fix:** a row per slug in IRIS's `data/build_cmds.csv` (`patches/0003`). Each row is autobuild's own Maven command plus
  `-Dmaven-checkstyle-plugin.failsOnError=false -Dmaven-checkstyle-plugin.failsOnViolation=false`, so the same code is compiled
  and only the style check stops failing the build. IRIS's script already reads this file (`build_codeql_dbs.py:35,197`).

## D-4 — Gradle distribution pre-cached in the local venice image (2026-11-22, before any Phase 8 data)

* **What:** venice CVE-2022-36007 failed all 6 build attempts (09:39–09:43 and 12:54–12:58). Inside the container, the Gradle
  wrapper could not download `gradle-7.4.1-bin.zip`: `services.gradle.org` → `github.com` → `release-assets.githubusercontent.com`
  ended in `SocketTimeoutException: connect timed out`. Cause: on this network, the GitHub CDN address `185.199.109.133` is
  unreachable while `.108/.110/.111` work (tested with `curl --resolve`). Java's `HttpURLConnection` only tries the first
  address DNS returns; curl falls back to the others.
* **Fix (local environment only):** in a container from the venice image, add a temporary `/etc/hosts` entry pointing to `.110`,
  then run `./gradlew --version` so the wrapper caches Gradle 7.4.1 in `~/.gradle/wrapper/dists`. The zip's SHA-256
  `e5444a57cda4a95f90b0c9446a9e1b47d3d7f69057765bfb54bd4f482542d548` equals the official checksum. Then `docker commit`
  the container over the same tag, keeping its entrypoint, command and working directory. Docker bind-mounts `/etc/hosts`, so the
  entry is not saved in the image. Image ID before: `sha256:02b94f86…40ad7`; after: `sha256:0192654b…14075`.
* **Effect:** the build no longer downloads Gradle. The sources, Gradle version and build command are unchanged. To undo:
  `docker rmi` the tag and pull it again.
