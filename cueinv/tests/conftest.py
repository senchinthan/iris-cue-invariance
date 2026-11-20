import sys

from cueinv import IRIS_ROOT

# IRIS mixes `src.models.*` and `models.*` imports (see docs/RECON.md §8), so both roots must be importable.
for p in (str(IRIS_ROOT), str(IRIS_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)
