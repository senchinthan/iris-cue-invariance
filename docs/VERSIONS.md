# Versions and environment

Recorded 2026-11-20 (Phase 1). Every run additionally logs SHAs, Ollama version and model digest in
`runs/<run_id>/config.json` (`cueinv/runlog.py`).

## Code
| Component | Version |
|---|---|
| IRIS | `https://github.com/iris-sast/iris`, branch `v2`, commit **`d2059f299a80a68ea4a0b0315eec2f42b8743776`** (2026-11-07, "Update codeql_vul.py (#81)") |
| IRIS local changes | `patches/0001-dep-configs-local-toolchain-paths.patch` only (see `docs/PATCHES.md`) |
| cueinv | this repo (`git rev-parse HEAD`) |

## Host
| Item | Value |
|---|---|
| OS | WSL2, kernel `6.18.58.1-microsoft-standard-WSL2`, Ubuntu (Python 3.12.3 system) |
| `.wslconfig` | `memory=11GB, processors=12, swap=16GB` (plan asked for 12 GB / 6 / 8 GB; see note) |
| RAM visible | 10954 MiB total (`free -m`); swap 16384 MiB |
| GPU | NVIDIA GeForce RTX 3060 Laptop GPU, 6144 MiB, compute 8.6; driver/KMD 617.42, CUDA UMD 13.4 |
| Disk | 1007 GB volume, 898 GB free at setup |
| Docker | 29.9.3 (Docker Desktop, WSL2 backend), 10.7 GiB / 12 CPUs visible to the daemon |
| sudo | not available non-interactively → all tools installed user-space |

Note on `.wslconfig`: the user's file has 11 GB RAM / 12 processors / 16 GB swap rather than the plan's 12 / 6 / 8.
This gives ~1 GB less RAM than planned. It is enough for one CodeQL analysis plus Ollama at a time; revisit only if CodeQL
OOMs on larger projects.

## Toolchain
| Tool | Version | Location / source |
|---|---|---|
| JDK 8 | Temurin `1.8.0_514-b01` | `~/opt/jdk8u514-b01` (Adoptium API tarball) |
| Maven | 3.5.0 (sha1 verified against archive.apache.org) | `~/opt/apache-maven-3.5.0` |
| CodeQL | **2.23.2** (`codeql version`); bundle sha256 `0478926e…e177b` matches the release `.checksum.txt` | `iris/codeql/` (codeql-action release `codeql-bundle-v2.23.2`, `codeql-bundle-linux64.tar.gz`) |
| CodeQL Java queries | `codeql/java-queries` **1.8.1**, `codeql/java-all` 7.7.1 | `iris/codeql/qlpacks/codeql/…` (matches `src/config.py:38`) |
| Conda | Miniconda3 `py314_26.9.4-1`, conda 26.9.4; channels: conda-forge only (Anaconda `defaults` removed → no ToS acceptance) | `~/miniconda3`; `conda init` **not** run — use `source ~/miniconda3/etc/profile.d/conda.sh && conda activate iris` |
| Env `iris` | python 3.10.22, pytorch 2.5.1 **CPU** (`cpuonly`), transformers 5.17.0, accelerate 1.14.0, pandas 2.3.3, numpy 2.2.6, scipy 1.15.2, statsmodels 0.15.0, matplotlib 3.10.9, pytest 9.2.0, ollama-py 0.6.3, docker-py 7.2.0 | spec `env/iris-cueinv.yml`, full freeze `env/iris-cueinv.lock.yml` |

## Ollama
| Item | Value |
|---|---|
| Version | `0.40.16` (client and server) |
| Launch | tmux session `ollama`, `ollama serve` (no systemd in this WSL) — log `logs/ollama_serve.log` |
| Env | `OLLAMA_NUM_PARALLEL=1 OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 OLLAMA_HOST=127.0.0.1:11434` (confirmed in the server's config log line) |
| Server default context | `default_num_ctx=4096` (VRAM-based, logged at start-up) |
| `qwen2.5-coder:7b-instruct-q4_K_M` | digest `dae161e27b0e90dd1856c8bb3209201fd6736d8eb66298e75ed87571486f4364`, 7.6B, Q4_K_M, 4,683,087,561 bytes |
| `qwen3:4b` | digest `359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7`, 4.0B, Q4_K_M, 2,497,293,931 bytes |

## VRAM check (Phase 1.5) — `results/vram_check*.json`
| Model | num_ctx | Offload | Prompt tokens | GPU fraction | Prompt tok/s | Gen tok/s |
|---|---|---|---|---|---|---|
| qwen2.5-coder 7B | 8192 | Ollama default fit | 2856 | 0.81 (25/29 layers) | 562 | 8.3 |
| qwen2.5-coder 7B | 6144 | Ollama default fit | 2856 | 0.81 | 526 | 10.5 |
| qwen2.5-coder 7B | **8192** | **`num_gpu=99`** | 4026 | **1.00** | 411 | 9.9 (steady-state 12–12.7) |
| qwen3:4b | 8192 | Ollama default fit | 4007 | 1.00 | 124 | 14.1 |

* Ollama/llama.cpp keeps a **1024 MiB free-VRAM margin** (`common_params_fit_impl: cannot meet free memory target of 1024 MiB`);
  full offload of the 7B is projected at 4555 MiB (ctx 8192) vs 5118 MiB free, so it fits physically but not with the margin.
  Dropping `num_ctx` to 6144 does not help (KV cache at q8_0 is only ~180–240 MiB). **Decision: `num_ctx=8192`, `num_gpu=99`.**
  `cueinv/llm.py` must log `/api/ps` GPU fraction per model load so silent spills are detected.
* Throughput is limited by Windows-side throttling: during generation `nvidia-smi` reports 97 % utilisation, ~40 W, P3,
  `clocks_throttle_reasons.active = 0x24` (SW power cap + SW thermal slowdown). Plugging in AC power and selecting a
  performance profile (Windows power mode / Armoury Crate) should raise this; re-run `python -m cueinv.vram_check --num-gpu 99`
  afterwards and before the pilot timing.
