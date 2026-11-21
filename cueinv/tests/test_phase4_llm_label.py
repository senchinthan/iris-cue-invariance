import importlib.util
import json
from collections import Counter

import pytest

from cueinv import llm, perturb
from cueinv.perturb import Candidate, make_view, signature_params

QS_CSV = "runs/quickstart-spark/iris_outputs/source_func_param_candidates.csv"
needs_iris = pytest.mark.skipif(not all(importlib.util.find_spec(m) for m in ("torch", "transformers", "pandas")),
                                reason="needs the `iris` conda env")


# ---- fake Ollama ------------------------------------------------------------------------------------------------
class FakeOllama:
    def __init__(self, contents):
        self.contents = list(contents)
        self.bodies = []

    def __call__(self, body):
        self.bodies.append(body)
        if body["options"].get("num_predict") == 8:  # warm-up
            return {"message": {"content": "[]"}, "done_reason": "stop", "load_duration": 2_000_000_000}
        c = self.contents.pop(0)
        return {"message": {"content": c}, "done_reason": "stop", "prompt_eval_count": 100, "eval_count": 10,
                "prompt_eval_duration": 10**8, "eval_duration": 10**9}


@pytest.fixture(autouse=True)
def _no_gpu_probe(monkeypatch):
    monkeypatch.setattr(llm, "gpu_snapshot", lambda m: {"gpu_fraction": 1.0, "vram_used_mib": 4700})


MSGS = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]


def test_options_are_the_plan_decoding_settings(tmp_path):
    c = llm.OllamaClient("qwen2.5-coder:7b-instruct-q4_K_M", "r", runs_dir=tmp_path, transport=FakeOllama(["[]"]))
    o = c.options
    assert (o["temperature"], o["top_k"], o["top_p"], o["num_ctx"], o["num_predict"], o["num_gpu"]) == (0, 1, 1, 8192, 2048, 99)
    assert c.think is None
    assert llm.OllamaClient("qwen3:4b", "r2", runs_dir=tmp_path).think is False


def test_cache_hit_skips_the_call_and_is_keyed_by_condition_and_rep(tmp_path):
    fake = FakeOllama(['[{"a": 1}]', '[{"b": 2}]'])
    c = llm.OllamaClient("m", "r", runs_dir=tmp_path, transport=fake)
    r1 = c.chat(MSGS, schema=llm.INTERNAL_SCHEMA, condition="BASE", rep=1)
    assert (r1.parse_status, r1.cached, r1.parsed) == ("ok", False, [{"a": 1}])
    # resumable: a fresh client on the same run dir reads the jsonl and does not call the model
    c2 = llm.OllamaClient("m", "r", runs_dir=tmp_path, transport=FakeOllama([]))
    r1b = c2.chat(MSGS, schema=llm.INTERNAL_SCHEMA, condition="BASE", rep=1)
    assert r1b.cached and r1b.parsed == [{"a": 1}]
    r2 = c.chat(MSGS, schema=llm.INTERNAL_SCHEMA, condition="BASE", rep=2)  # different rep -> new call
    assert not r2.cached and r2.parsed == [{"b": 2}]
    lines = [json.loads(l) for l in open(tmp_path / "r" / "llm_calls.jsonl")]
    kinds = Counter(l["kind"] for l in lines)
    assert kinds == {"warmup": 1, "call": 2}
    call = [l for l in lines if l["kind"] == "call"][0]
    for k in ("prompt_sha256", "model", "condition", "rep", "response", "parsed", "parse_status", "prompt_tokens",
              "completion_tokens"):
        assert k in call


def test_mixing_settings_in_one_run_id_raises(tmp_path):
    llm.OllamaClient("m", "r", runs_dir=tmp_path, transport=FakeOllama(["[]"])).chat(
        MSGS, schema=None, condition="BASE", rep=1)
    c = llm.OllamaClient("m", "r", runs_dir=tmp_path, options={"num_ctx": 4096}, transport=FakeOllama(["[]"]))
    with pytest.raises(RuntimeError, match="different request settings"):
        c.chat(MSGS, schema=None, condition="BASE", rep=1)


def test_parse_failure_retries_once_then_records(tmp_path):
    fake = FakeOllama(["not json", "still not json"])
    c = llm.OllamaClient("m", "r", runs_dir=tmp_path, transport=fake)
    r = c.chat(MSGS, schema=None, condition="BASE", rep=1)
    assert r.parse_status == "parse_failed"
    assert [a["attempt"] for a in r.record["attempts"]] == [1, 2]
    fake2 = FakeOllama(["oops", "[]"])
    r2 = llm.OllamaClient("m", "r2", runs_dir=tmp_path, transport=fake2).chat(MSGS, schema=None, condition="B", rep=1)
    assert r2.parse_status == "ok" and len(r2.record["attempts"]) == 2


def test_parse_json_list_strict():
    assert llm.parse_json_list('[{"x": 1}]') == ([{"x": 1}], "ok")
    assert llm.parse_json_list("```json\n[]\n```")[1] == "parse_failed"
    assert llm.parse_json_list('{"x": 1}')[1] == "parse_failed"
    assert llm.parse_json_list(None)[1] == "no_response"


def test_iris_adapter_returns_strings_in_order(tmp_path):
    one, two = '[{"i": 1}]', '[{"i": 2}]'
    c = llm.OllamaClient("m", "r", runs_dir=tmp_path, transport=FakeOllama([one, two]))
    a = llm.IrisModelAdapter(c, condition="EXT", schema=None)
    m2 = [{"role": "system", "content": "s"}, {"role": "user", "content": "v"}]
    assert a.predict([MSGS, m2], batch_size=3) == [one, two]
    assert a.predict(MSGS) == one  # single prompt, now served from the cache
    assert [r.cached for r in a.results] == [False, False, True]


# ---- perturbation controls ----------------------------------------------------------------------------------------
def test_signature_params():
    assert signature_params("void attribute(String attribute, Object value)") == ["attribute", "value"]
    assert signature_params("Map<String,Object> f(Map<String, List<Integer>> m, int[] xs)") == ["m", "xs"]
    assert signature_params("Foo(String a)") == ["a"]
    assert signature_params("void f()") == []


def _spark_candidates():
    pd = pytest.importorskip("pandas")
    from cueinv.iris_bridge import ensure_iris_importable
    ensure_iris_importable()
    from src.iris import SAPipeline

    class L:
        def info(self, *a, **k):
            pass

    p = SAPipeline.__new__(SAPipeline)
    p.project_logger = L()
    p.source_func_param_candidates_path = QS_CSV
    return [Candidate(*r) for r in p.fetch_func_param_src_candidates()]


@needs_iris
def test_base_view_reproduces_iris_prompts_byte_for_byte():
    from pathlib import Path
    from cueinv.label import build_messages

    cands = _spark_candidates()
    readme = Path("runs/quickstart-spark/iris_outputs/label_func_params/readme_head.txt").read_text()
    view = make_view(cands, readme, "perwendel", "spark", "BASE", seed=0)
    assert [len(b) for b in view.batches] == [20, 20, 20, 19]
    for bi, b in enumerate(view.batches):
        sent = Path(f"runs/quickstart-spark/iris_outputs/label_func_params/raw_user_prompt_{bi * 20}.txt").read_text()
        assert build_messages(view, b)[1]["content"] + "\n" == sent


@needs_iris
def test_shuf_changes_only_membership_and_order():
    cands = _spark_candidates()
    base = make_view(cands, "R", "o", "n", "BASE", seed=0)
    shuf = make_view(cands, "R", "o", "n", "SHUF", seed=perturb.stable_seed("spark", "SHUF"))
    assert sorted(shuf.candidates, key=lambda c: c.key) == sorted(base.candidates, key=lambda c: c.key)
    assert [c.row() for c in shuf.candidates] != [c.row() for c in base.candidates]
    assert [len(b) for b in shuf.batches] == [len(b) for b in base.batches]
    assert (shuf.readme, shuf.owner, shuf.name) == (base.readme, base.owner, base.name)
    again = make_view(cands, "R", "o", "n", "SHUF", seed=perturb.stable_seed("spark", "SHUF"))
    assert [c.key for c in again.candidates] == [c.key for c in shuf.candidates]


@needs_iris
def test_map_back_validates_against_batch():
    from cueinv.label import map_back

    cands = _spark_candidates()
    view = make_view(cands, "R", "o", "n", "BASE", seed=0)
    b = view.batches[0]
    c0 = b[0]
    other_batch = view.batches[1][0]
    parsed = [
        {"package": c0.package, "class": c0.clazz, "method": c0.func, "signature": "whatever",
         "tainted_input": [c0.params[0], "notAParam"]},
        {"package": other_batch.package, "class": other_batch.clazz, "method": other_batch.func,
         "signature": "", "tainted_input": other_batch.params},                       # real, but not in this batch
        {"package": "x", "class": "Y", "method": "z", "signature": "", "tainted_input": ["a"]},  # hallucinated
    ]
    found, ctr = map_back(view, b, parsed)
    assert found == {c0.key: {c0.params[0]}}
    assert ctr == {"items": 3, "items_unmapped": 2, "params": 2, "params_unmapped": 1}


# ---- free-form mode (GATE 2 D1a) ------------------------------------------------------------------------------------
@needs_iris
def test_iris_parse_statuses():
    from cueinv.iris_bridge import iris_parse

    assert iris_parse('```json\n[{"package": "p"}]\n```') == ([{"package": "p"}], "ok")
    assert iris_parse("[]") == ([], "ok")                          # a genuine empty answer is not a failure
    assert iris_parse("I cannot find any.")[1] == "parse_failed"   # no JSON array at all
    items, status = iris_parse('[{"a": 1} {"b": 2}]')             # missing comma: IRIS's fallback salvages both objects
    assert (items, status) == ([{"a": 1}, {"b": 2}], "recovered")
    assert iris_parse('[{"a": 1}, {"b": 2,}]') == ([], "parse_failed")  # IRIS's fallback is all-or-nothing
    assert iris_parse(None) == (None, "no_response")


@needs_iris
def test_output_settings_and_free_mode_sends_no_format(tmp_path):
    from cueinv import label

    assert label.DEFAULT_OUTPUT == "free"
    fmt, parser = label.output_settings("free", llm.INTERNAL_SCHEMA)
    assert fmt is None and parser is not None
    fmt, parser = label.output_settings("schema", llm.INTERNAL_SCHEMA)
    assert fmt is llm.INTERNAL_SCHEMA and parser is None
    with pytest.raises(ValueError):
        label.output_settings("xml", {})

    fake = FakeOllama(['```json\n[{"x": 1}]\n```'])
    c = llm.OllamaClient("m", "r", runs_dir=tmp_path, transport=fake)
    fmt, parser = label.output_settings("free", llm.INTERNAL_SCHEMA)
    r = c.chat(MSGS, schema=fmt, condition="BASE", rep=1, parser=parser)
    assert r.parse_status == "ok" and r.parsed == [{"x": 1}]
    assert "format" not in fake.bodies[-1]


def test_truncated_output_is_retried_and_flagged(tmp_path):
    class Trunc(FakeOllama):
        def __call__(self, body):
            r = super().__call__(body)
            if body["options"].get("num_predict") != 8:
                r["done_reason"] = "length"
            return r

    c = llm.OllamaClient("m", "r", runs_dir=tmp_path, transport=Trunc(['[{"a": 1}]', '[{"a": 1}]']))
    r = c.chat(MSGS, schema=None, condition="BASE", rep=1)
    assert r.parse_status == "truncated" and len(r.record["attempts"]) == 2 and r.parsed == [{"a": 1}]
