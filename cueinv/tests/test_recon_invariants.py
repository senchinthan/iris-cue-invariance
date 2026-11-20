"""Pin the IRIS behaviours that docs/RECON.md and the interception design (RECON §8) rely on.

If IRIS is updated and one of these fails, re-read the code and update RECON.md before trusting any run.
"""

import ast
import importlib.util
import inspect
import json
import re
import string
from pathlib import Path

import pytest

from cueinv import CAPSTONE_ROOT, IRIS_ROOT

pytestmark = pytest.mark.skipif(
    not all(importlib.util.find_spec(m) for m in ("torch", "transformers", "pandas")),
    reason="needs the `iris` conda env",
)

QS = CAPSTONE_ROOT / "runs" / "quickstart-spark" / "iris_outputs"
SPARK = "perwendel__spark_CVE-2018-9159_2.7.1"


class _Log:
    def __init__(self):
        self.lines = []

    def info(self, m, *a, **k):
        self.lines.append(m)

    error = info

    def print(self, *a, **k):
        pass


def _bare_pipeline(**attrs):
    """An SAPipeline instance without running __init__ (which needs a CodeQL DB)."""
    from src.iris import SAPipeline

    p = SAPipeline.__new__(SAPipeline)
    p.project_logger = _Log()
    for k, v in attrs.items():
        setattr(p, k, v)
    return p


def _fields(template: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(template) if f}


# --- §3 prompt construction -------------------------------------------------------------------------------

def test_prompt_template_fields():
    from src import prompts

    assert _fields(prompts.FUNC_PARAM_LABELLING_USER_PROMPT) == {
        "project_username", "project_name", "project_readme_summary", "methods"}
    assert _fields(prompts.API_LABELLING_USER_PROMPT) == {
        "cwe_long_description", "cwe_examples", "cwe_description", "cwe_id", "methods"}
    assert "Package,Class,Method,Doc" in prompts.FUNC_PARAM_LABELLING_USER_PROMPT
    assert "Package,Class,Method,Signature" in prompts.API_LABELLING_USER_PROMPT
    assert '"tainted_input"' in prompts.FUNC_PARAM_LABELLING_SYSTEM_PROMPT


def test_doc_truncation_is_50_plus_ellipsis():
    from src import iris

    assert iris.MAX_DOC_LENGTH == 50
    p = _bare_pipeline()
    assert p.extract_doc("x" * 50) == "x" * 50
    assert p.extract_doc("y" * 51) == "y" * 50 + "..."
    assert p.extract_doc(None) == ""


def test_batch_size_defaults_constructor_vs_cli():
    from src.iris import SAPipeline

    sig = inspect.signature(SAPipeline.__init__).parameters
    assert sig["label_api_batch_size"].default == 30
    assert sig["label_func_param_batch_size"].default == 50  # differs from the CLI (20) — pass 20 explicitly
    assert sig["seed"].default == 1234
    src = (IRIS_ROOT / "src" / "iris.py").read_text()
    assert re.search(r'"--label-api-batch-size", type=int, default=30', src)
    assert re.search(r'"--label-func-param-batch-size", type=int, default=20', src)


def test_filter_by_module_off_by_default():
    from src.iris import SAPipeline

    sig = inspect.signature(SAPipeline.__init__).parameters
    assert sig["filter_by_module"].default is False
    assert sig["filter_by_module_large"].default is False
    src = (IRIS_ROOT / "src" / "iris.py").read_text()
    assert '"--filter-by-module", action="store_true"' in src
    assert '"--filter-by-module-large", action="store_true"' in src


# --- §5 Ollama backend ------------------------------------------------------------------------------------

def test_ollama_backend_defaults_from_source():
    tree = ast.parse((IRIS_ROOT / "src" / "models" / "ollama.py").read_text())
    defaults = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "_OLLAMA_DEFAULT_OPTIONS":
            defaults = ast.literal_eval(node.value)
    assert defaults == {"temperature": 0.8, "num_predict": -1, "stop": None, "seed": 0}
    assert "num_ctx" not in defaults and "num_gpu" not in defaults


# --- §8 interception premises -----------------------------------------------------------------------------

def test_get_model_returns_injected_model():
    sentinel = object()
    p = _bare_pipeline(model=sentinel)
    assert p.get_model() is sentinel


def test_internal_stage_skips_when_label_file_exists(tmp_path):
    label = tmp_path / "llm_labelled_source_func_params.json"
    label.write_text("[]")

    class Boom:
        def predict(self, *a, **k):
            raise AssertionError("IRIS must not query the LLM when our label file exists")

    p = _bare_pipeline(model=Boom(), llm_labelled_source_func_params_path=str(label), overwrite=False,
                       overwrite_labelled_func_param=False)
    p.query_gpt_for_func_param_src()
    assert label.read_text() == "[]"


def test_source_qll_consumes_our_label_schema(tmp_path):
    src_apis = tmp_path / "src_apis.json"
    src_apis.write_text("[]")
    params = tmp_path / "params.json"
    params.write_text(json.dumps([{"package": "spark", "class": "Request", "method": "params",
                                   "signature": "String params(String param)", "tainted_input": ["param"]}]))
    p = _bare_pipeline(llm_labelled_source_apis_path=str(src_apis), llm_labelled_source_func_params_path=str(params))
    qll = p.build_source_qll_with_enumeration()
    assert 'p.getCallable().getName() = "params"' in qll
    assert 'hasQualifiedName("spark", "Request")' in qll
    assert 'p.getName() = "param"' in qll


@pytest.mark.skipif(not (QS / "source_func_param_candidates.csv").exists(), reason="quickstart outputs not archived")
def test_reproduce_iris_internal_prompts_byte_for_byte():
    """Rebuild the spark internal prompts from IRIS's own pieces and compare with what IRIS sent (RECON §3, §8)."""
    from src.prompts import FUNC_PARAM_LABELLING_USER_PROMPT

    p = _bare_pipeline(source_func_param_candidates_path=str(QS / "source_func_param_candidates.csv"))
    rows = p.fetch_func_param_src_candidates()
    assert len(rows) == 79
    readme = (QS / "label_func_params" / "readme_head.txt").read_text()
    owner, name = SPARK.split("_")[0], SPARK.split("_")[2]
    assert (owner, name) == ("perwendel", "spark")
    bs = 20
    starts = list(range(0, len(rows), bs))
    assert starts == [0, 20, 40, 60]
    for i in starts:
        methods = "\n".join(",".join([r[0], r[1], r[3], r[4]]) for r in rows[i:i + bs])
        ours = FUNC_PARAM_LABELLING_USER_PROMPT.format(project_username=owner, project_name=name,
                                                      project_readme_summary=readme, methods=methods)
        sent = (QS / "label_func_params" / f"raw_user_prompt_{i}.txt").read_text()
        assert ours + "\n" == sent, f"batch {i} differs"
