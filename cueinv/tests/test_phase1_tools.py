import importlib.util
import json

import pytest

from cueinv import vram_check, runlog


def test_gpu_fraction():
    assert vram_check.gpu_fraction({"size": 100, "size_vram": 100}) == 1.0
    assert vram_check.gpu_fraction({"size": 100, "size_vram": 25}) == 0.25
    assert vram_check.gpu_fraction({}) == 0.0


def test_tokens_per_second():
    assert vram_check.tokens_per_second(100, 2_000_000_000) == 50.0
    assert vram_check.tokens_per_second(None, 5) is None
    assert vram_check.tokens_per_second(5, 0) is None


def test_build_prompt_scales_with_target():
    small, big = vram_check.build_prompt(500), vram_check.build_prompt(4000)
    assert len(big) > 6 * len(small)
    assert big.startswith("You are a security expert.")


def test_write_config_has_required_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(runlog, "ollama_version", lambda: "0.0.test")
    monkeypatch.setattr(runlog, "ollama_model_digest", lambda m: "sha256:abc")
    p = runlog.write_config("r1", runs_dir=tmp_path, model="m", condition="BASE", rep=2, seed=7)
    cfg = json.loads(p.read_text())
    for k in ("iris_git_sha", "cueinv_git_sha", "ollama_version", "model_digest", "condition", "rep", "seed",
              "timestamp"):
        assert k in cfg
    assert (cfg["condition"], cfg["rep"], cfg["seed"], cfg["model_digest"]) == ("BASE", 2, 7, "sha256:abc")
    assert cfg["iris_git_sha"] and cfg["iris_git_sha"].startswith("d2059f2")


_has_iris_deps = all(importlib.util.find_spec(m) is not None for m in ("torch", "transformers", "ollama", "pandas"))


@pytest.mark.skipif(not _has_iris_deps, reason="needs the `iris` conda env")
def test_quickstart_overrides_patch_the_module_iris_uses():
    from cueinv import iris_quickstart

    om = iris_quickstart.install_overrides(temperature=0.0)
    assert om._OLLAMA_DEFAULT_OPTIONS["temperature"] == 0.0
    assert om._model_name_map[iris_quickstart.MODEL_ALIAS] == iris_quickstart.MODEL_TAG
    # IRIS's factory imports `models.ollama`; it must be the very object we patched.
    import sys
    assert sys.modules["models.ollama"] is om
