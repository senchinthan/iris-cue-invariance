"""Phase 6 requirements (plan): round-trip identity, external prompts byte-identical, no surviving identifiers,
SHUF only reorders; plus bijectivity, determinism and the doc/meta conditions. Runs on the 5 pilot projects."""
import csv
import importlib.util
import json
import re
from pathlib import Path

import pytest

from cueinv import perturb
from cueinv.perturb import (Candidate, PerturbContext, IDENT, PROSE_MIN_LEN, PROJECT_ID_MASK, make_view, stable_seed)

FIX = Path(__file__).parent / "fixtures" / "pilot"
PROJECTS = sorted(p.name for p in FIX.iterdir()) if FIX.exists() else []
RENAMES = ("R_SYN", "R_NEU", "R_NEU2", "R_MIS", "COMBO")
ALL = perturb.PROMPT_CONDITIONS
needs_tok = pytest.mark.skipif(importlib.util.find_spec("transformers") is None, reason="needs the iris env")
_CACHE = {}


def load(slug):
    if slug not in _CACHE:
        d = FIX / slug
        cands = [Candidate(r["package"], r["clazz"], r["func"], r["signature"], r["doc"])
                 for r in csv.DictReader(open(d / "candidates.csv"))]
        cj = json.loads((d / "context.json").read_text())
        ctx = PerturbContext(frozenset(cj["project_classes"]), frozenset(cj["reserved"]))
        _CACHE[slug] = (cands, (d / "readme_head.txt").read_text(), cj["owner"], cj["name"], ctx)
    return _CACHE[slug]


_VIEWS = {}


def view(slug, cond, seed=None):
    key = (slug, cond, seed)
    if key not in _VIEWS:
        cands, readme, owner, name, ctx = load(slug)
        s = stable_seed(slug, "R_NEU" if cond == "COMBO" else cond) if seed is None else seed
        _VIEWS[key] = make_view(cands, readme, owner, name, cond, s, ctx=ctx)
    return _VIEWS[key]


def test_fixtures_present():
    assert len(PROJECTS) == 5


# ---- requirement 1: rename -> inverse round trip is the identity on 100 % of pilot candidates ------------------------
@needs_tok
@pytest.mark.parametrize("slug", PROJECTS)
@pytest.mark.parametrize("cond", ALL)
def test_round_trip_identity(slug, cond):
    cands = load(slug)[0]
    v = view(slug, cond)
    pert = v.candidates
    by_orig = {v.origin[p.key].key: p for p in pert}
    assert set(by_orig) == {c.key for c in cands}          # every candidate present exactly once
    for c in cands:
        p = by_orig[c.key]
        assert (c.package, v.orig_class(p.clazz), v.orig_method(p.func)) == c.key
        assert [v.orig_param(x) for x in p.params] == c.params
        assert p.package == c.package


@needs_tok
@pytest.mark.parametrize("slug", PROJECTS)
@pytest.mark.parametrize("cond", RENAMES)
def test_map_back_recovers_every_parameter(slug, cond):
    from cueinv.label import map_back

    cands = load(slug)[0]
    v = view(slug, cond)
    got = {}
    for b in v.batches:
        answer = [{"package": p.package, "class": p.clazz, "method": p.func, "signature": p.signature,
                   "tainted_input": p.params} for p in b]
        found, ctr = map_back(v, b, answer)
        assert ctr["items_unmapped"] == 0 and ctr["params_unmapped"] == 0
        got.update(found)
    assert got == {c.key: set(c.params) for c in cands}


# ---- requirement 2: external prompts are byte-identical across conditions -----------------------------------------
def _capture_external_prompts(tmp, candidate_apis_csv):
    from cueinv.iris_bridge import ensure_iris_importable
    ensure_iris_importable()
    from src.iris import SAPipeline

    class L:
        def info(self, *a, **k): pass
        error = info

    class Capture:
        def __init__(self): self.prompts = []
        def predict(self, prompts, batch_size=0, no_progress_bar=False):
            self.prompts.extend(prompts)
            return ["[]"] * len(prompts)

    tmp.mkdir(parents=True, exist_ok=True)
    p = SAPipeline.__new__(SAPipeline)
    p.project_logger, p.query, p.cwe_id, p.label_api_batch_size, p.num_threads = L(), "cwe-022wLLM", "022", 30, 1
    p.overwrite = p.overwrite_labelled_apis = p.overwrite_llm_cache = p.skip_huge_project = p.test_run = False
    p.candidate_apis_csv_path = str(candidate_apis_csv)
    p.llm_labelled_source_apis_path = str(tmp / "src.json")
    p.llm_labelled_sink_apis_path = str(tmp / "snk.json")
    p.llm_labelled_taint_prop_apis_path = str(tmp / "tp.json")
    p.api_labels_cache_path = str(tmp / "cache.json")
    p.label_api_log_path = str(tmp)
    p.model = Capture()
    p.query_gpt_for_api_src_tp_sink_batched()
    return [json.dumps(m) for m in p.model.prompts]


@needs_tok
def test_external_prompts_byte_identical_across_conditions(tmp_path):
    csv_path = Path("runs/quickstart-spark/iris_outputs/candidate_apis.csv")
    before = _capture_external_prompts(tmp_path / "before", csv_path)
    for slug in PROJECTS:
        for cond in ALL:
            view(slug, cond)  # build every perturbed view; none may touch external inputs
    after = _capture_external_prompts(tmp_path / "after", csv_path)
    assert before == after and len(before) == 19
    # and they equal what IRIS itself wrote in the quickstart, when that output is present
    iris_dump = Path("iris/output/perwendel__spark_CVE-2018-9159_2.7.1/quickstart-spark/analysis/cwe-022wLLM/logs/label_apis")
    if iris_dump.exists():
        for i, m in enumerate(before):
            sent = (iris_dump / f"raw_user_prompt_{i * 30}.txt").read_text()
            assert json.loads(m)[1]["content"] + "\n" == sent


# ---- requirement 3: no original identifier string survives in a perturbed prompt ---------------------------------
def _tokens(text):
    return set(re.findall(IDENT, text or ""))


@needs_tok
@pytest.mark.parametrize("slug", PROJECTS)
@pytest.mark.parametrize("cond", RENAMES)
def test_no_renamed_identifier_survives(slug, cond):
    from cueinv.label import build_messages

    v = view(slug, cond)
    assert v.renamed, "a rename condition must rename something"
    struct, prose = set(), _tokens(v.readme)
    for p in v.candidates:                       # the package field is kept verbatim by design and not scanned
        struct |= _tokens(p.clazz) | _tokens(p.signature)
        prose |= _tokens(p.doc)
    for orig in v.renamed:
        assert orig not in struct, f"{orig} survives in a row/signature"
        if len(orig) >= PROSE_MIN_LEN:
            assert orig not in prose, f"{orig} survives in JavaDoc/README prose"
    # and the full user prompt contains every perturbed row verbatim
    msgs = build_messages(v, v.batches[0])
    assert all(p.row() in msgs[1]["content"] for p in v.batches[0])


# ---- requirement 4: SHUF changes only batch membership and order; every other view keeps BASE's batches -----------
@needs_tok
@pytest.mark.parametrize("slug", PROJECTS)
def test_batch_composition(slug):
    base = view(slug, "BASE")
    shuf = view(slug, "SHUF")
    assert sorted(shuf.candidates, key=lambda c: c.key) == sorted(base.candidates, key=lambda c: c.key)
    assert [len(b) for b in shuf.batches] == [len(b) for b in base.batches]
    assert (shuf.readme, shuf.owner, shuf.name) == (base.readme, base.owner, base.name)
    if len(base.candidates) > perturb.INTERNAL_BATCH_SIZE:
        assert [[c.key for c in b] for b in shuf.batches] != [[c.key for c in b] for b in base.batches]
    for cond in ALL:
        if cond in ("BASE", "SHUF"):
            continue
        v = view(slug, cond)
        assert [[v.origin[p.key].key for p in b] for b in v.batches] == [[c.key for c in b] for b in base.batches]


# ---- bijectivity, collisions, determinism -------------------------------------------------------------------------
@needs_tok
@pytest.mark.parametrize("slug", PROJECTS)
@pytest.mark.parametrize("cond", RENAMES)
def test_rename_is_bijective_and_collision_free(slug, cond):
    cands, _, _, _, ctx = load(slug)
    v = view(slug, cond)
    new = list(v.renamed.values())
    assert len(new) == len(set(new))
    inv = perturb.inventory(cands, ctx)
    originals = set().union(*inv.values())
    assert not (set(new) & originals)
    assert not (set(new) & (set(ctx.reserved) | perturb.JAVA_KEYWORDS | perturb.JDK_COMMON))
    assert not (set(v.renamed) & (set(ctx.reserved) | perturb.JAVA_KEYWORDS | perturb.JDK_COMMON))
    assert all(re.fullmatch(IDENT, n) for n in new)


@needs_tok
def test_rename_is_deterministic_given_seed():
    slug = PROJECTS[1]
    cands, readme, owner, name, ctx = load(slug)
    a = make_view(cands, readme, owner, name, "R_NEU", 7, ctx=ctx)
    b = make_view(cands, readme, owner, name, "R_NEU", 7, ctx=ctx)
    c = make_view(cands, readme, owner, name, "R_NEU", 8, ctx=ctx)
    assert a.renamed == b.renamed and [p.row() for p in a.candidates] == [p.row() for p in b.candidates]
    assert a.renamed != c.renamed


@needs_tok
@pytest.mark.parametrize("slug", PROJECTS)
def test_neutral_renames_are_token_and_length_matched(slug):
    from cueinv.tokenizer import count_tokens

    for cond, min_exact in (("R_NEU", 0.9), ("R_NEU2", 0.7)):
        v = view(slug, cond)
        exact = sum(count_tokens(o) == count_tokens(n) for o, n in v.renamed.items()) / len(v.renamed)
        assert exact >= min_exact, (cond, exact)
        assert v.stats["mean_abs_len_delta"] <= 6


def test_r_syn_uses_curated_synonyms_only():
    assert perturb._syn_candidates("readFile", "method") == ["loadFile"]
    assert perturb._syn_candidates("ArchivedFileSet", "class") == []      # verbs only at a method's leading word
    assert perturb._syn_candidates("getSourceDir", "method")[0] == "fetchOriginFolder"


@needs_tok
@pytest.mark.parametrize("slug", PROJECTS)
def test_r_mis_renames_methods_and_params_to_benign_names(slug):
    v = view(slug, "R_MIS")
    cands = load(slug)[0]
    assert all(p.clazz == c.clazz for p, c in zip(v.candidates, cands))           # classes kept
    benign = tuple(perturb.MIS_VERBS) + tuple(perturb.MIS_ADJS)
    assert all(n.startswith(benign) for n in v.renamed.values())


# ---- doc / meta conditions --------------------------------------------------------------------------------------
@needs_tok
@pytest.mark.parametrize("slug", PROJECTS)
def test_doc_and_meta_conditions(slug):
    cands, readme, owner, name, _ = load(slug)
    assert all(p.doc == "" for p in view(slug, "D_STRIP").candidates)
    assert all(p.doc == "" for p in view(slug, "COMBO").candidates)
    dm = view(slug, "D_MIS").candidates
    for p, c in zip(dm, cands):
        if not c.doc:
            assert p.doc == ""
        else:
            assert p.doc != c.doc and p.doc[:50] in [d[:50] for d in perturb.BENIGN_DOCS]
            assert p.doc.endswith("...") == c.doc.endswith("...")
    assert view(slug, "M_README").readme == ""
    mid = view(slug, "M_ID")
    assert (mid.owner, mid.name) == (PROJECT_ID_MASK, PROJECT_ID_MASK)
    low = (mid.readme or "").lower()
    for part in mid.stats["masked"]:
        assert not re.search(r"(?<![A-Za-z0-9_$])" + re.escape(part.lower()) + r"(?![A-Za-z0-9_$])", low)
    # COMBO = R_NEU's rename + D_STRIP
    assert view(slug, "COMBO").renamed == view(slug, "R_NEU").renamed
    # doc/meta conditions do not rename
    for cond in ("D_STRIP", "D_MIS", "M_README", "M_ID"):
        assert [(p.clazz, p.func, p.signature) for p in view(slug, cond).candidates] == \
               [(c.clazz, c.func, c.signature) for c in cands]


def test_h1c_vote():
    from cueinv.vote import vote, vote_found

    a, b, c = {("p", "C", "m", "x"), ("p", "C", "m", "y")}, {("p", "C", "m", "x")}, {("p", "C", "n", "z")}
    assert vote([a, b, c], 2) == {("p", "C", "m", "x")}
    assert vote_found([a, b, c], 2) == {("p", "C", "m"): {"x"}}
    assert vote([a, b, c], 1) == a | b | c
