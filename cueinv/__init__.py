"""cueinv: cue-invariance harness for LLM-inferred taint specs in IRIS.

Our code lives here; IRIS core under ../iris is never modified (see docs/PATCHES.md).
"""

from pathlib import Path

CAPSTONE_ROOT = Path(__file__).resolve().parent.parent
IRIS_ROOT = CAPSTONE_ROOT / "iris"
RUNS_DIR = CAPSTONE_ROOT / "runs"
RESULTS_DIR = CAPSTONE_ROOT / "results"
