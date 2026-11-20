# Changes applied to the IRIS checkout

IRIS core (`iris/src/**`, `iris/scripts/**`) is **not modified**. Our harness integrates through import and
instance-attribute injection only (see `docs/RECON.md` §8). The only files touched in `iris/` are per-machine
configuration files that IRIS's own README tells users to edit. Each change is kept as a patch in `patches/`
so a fresh clone can be reproduced with `git -C iris apply ../patches/<file>`.

| Patch | File | Why | Effect on results |
|---|---|---|---|
| `0001-dep-configs-local-toolchain-paths.patch` | `iris/dep_configs.json` | No sudo in WSL, so JDK 8 (Temurin 8u514-b01) and Maven 3.5.0 are installed user-space under `~/opt/` instead of `/usr/lib/jvm` and `/opt/maven` (README "Step 2: Configure Java build tools"). | None. Only used by the **native** build route (`scripts/build_one.py`, `scripts/build_codeql_dbs.py` without `--use-container`). The container route ignores it. |

Not patches, but local, untracked additions inside `iris/` (all git-ignored by IRIS or simply untracked):
* `iris/codeql/` — CodeQL bundle v2.23.2 (README "Step 3: Configure CodeQL"; `src/config.py:7` expects it there).
* `iris/data/project-sources/`, `iris/data/codeql-dbs/`, `iris/data/build-info/`, `iris/output/`, `iris/log/` — created by IRIS's scripts at run time.
