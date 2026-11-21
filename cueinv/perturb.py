"""Perturbation layer (plan Phase 6): IRIS internal-candidate rows + README summary + project identity, for one
(condition, seed), -> perturbed prompt inputs + a bijective rename map for mapping answers back.

Conditions (plan table):
  BASE      original inputs, IRIS order and batching (iris.py:716, :743, :765)                         control
  SHUF      seeded permutation of the candidates -> different batch membership and order               control
  R_SYN     synonym-preserving rename from a fixed curated table (readFile -> loadFile)               realistic
  R_NEU     neutral rename, length- and token-count-matched, scheme A (prefixed fragments, m_xxx)     stress
  R_NEU2    neutral rename, scheme B (camel-case pseudo-words from a disjoint fragment vocabulary)   stress
  R_MIS     misleading-benign rename of methods and parameters (sanitizeInput-style)                 stress
  D_STRIP   JavaDoc removed                                                                          realistic
  D_MIS     JavaDoc replaced by a benign-sounding but wrong 50-char description                     stress
  M_README  README summary removed                                                                   realistic
  M_ID      owner/name replaced by projectX (also in README/JavaDoc prose)          exploratory (contamination probe)
  COMBO     R_NEU + D_STRIP                                                                          detection endpoint
  H1C is not a prompt view: it is a vote over the BASE, R_NEU and D_STRIP label sets (cueinv/vote.py).

Rename rules
* One **string-level** map per project: the same original identifier string gets the same new string wherever it occurs —
  declaring class, method name, parameter names, project-class names inside signature types, and every whole-word
  occurrence in the JavaDoc and README text (otherwise the doc channel leaks the names). Constructors (method name ==
  class name) are therefore renamed consistently.
* Never renamed and never produced: Java keywords and primitive types, JDK / external class names (`reserved`), and project
  classes whose simple name equals a reserved name (ambiguous in signatures). New names never equal any original identifier.
* Prose replacement covers identifiers of >= PROSE_MIN_LEN characters; shorter ones (`a`, `s`, `in`) are renamed in rows and
  signatures only, because rewriting them in English text would destroy it.
* Every map is deterministic given (project candidates, context, condition, seed) and bijective; the inverse is used to
  map LLM answers back (cueinv/label.py: map_back).
"""

from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass, field, replace

INTERNAL_BATCH_SIZE = 20  # IRIS CLI default (iris.py:1331); the constructor default is 50 (RECON §3)
PROSE_MIN_LEN = 3

CONTROL_CONDITIONS = ("BASE", "SHUF")
RENAME_CONDITIONS = ("R_SYN", "R_NEU", "R_NEU2", "R_MIS")
DOC_CONDITIONS = ("D_STRIP", "D_MIS")
META_CONDITIONS = ("M_README", "M_ID")
PROMPT_CONDITIONS = CONTROL_CONDITIONS + RENAME_CONDITIONS + DOC_CONDITIONS + META_CONDITIONS + ("COMBO",)
VOTE_CONDITIONS = ("H1C",)
H1C_VIEWS = ("BASE", "R_NEU", "D_STRIP")
PROJECT_ID_MASK = "projectX"
GENERIC_NAME_PARTS = frozenset("""java core plugin plugins utils util commons common client server framework library
global project parent api web http sdk tool tools master main lib libs module modules app application service
stream workflow data file json xml text config cloud base test""".split())

JAVA_KEYWORDS = frozenset("""abstract assert boolean break byte case catch char class const continue default do double else
enum extends final finally float for goto if implements import instanceof int interface long native new package private
protected public return short static strictfp super switch synchronized this throw throws transient try void volatile while
true false null var record yield sealed permits""".split())
JDK_COMMON = frozenset("""Object String Integer Long Short Byte Boolean Character Double Float Number Void Class ClassLoader
CharSequence StringBuilder StringBuffer Math System Thread Runnable Throwable Exception RuntimeException Error Iterable
Comparable Cloneable AutoCloseable Enum Record Map HashMap LinkedHashMap TreeMap List ArrayList LinkedList Set HashSet
LinkedHashSet TreeSet Collection Collections Iterator Optional Arrays Objects Properties Date Calendar Locale TimeZone UUID
Random Scanner Stream Function BiFunction Consumer BiConsumer Supplier Predicate File Files Path Paths InputStream
OutputStream Reader Writer BufferedReader BufferedWriter Serializable Closeable URL URI Charset Pattern Matcher Instant
Duration LocalDate LocalDateTime BigDecimal BigInteger Callable Future Executor ExecutorService Annotation Method Field
Constructor Type Socket ByteBuffer Process ProcessBuilder Runtime""".split())
PRIMITIVES = frozenset("void int boolean long short byte char double float".split())

# ---- curated synonym table for R_SYN (programming-sense synonyms; security-loaded words such as file/path/url/exec are
# deliberately absent so that R_SYN does not add or remove security cues) ---------------------------------------------
VERB_SYNONYMS = {  # applied only to the leading word of method names (Java verb-first convention)
    "get": ["fetch", "obtain"], "set": ["assign"], "read": ["load"], "load": ["read"], "write": ["store"],
    "store": ["save"], "save": ["store"], "create": ["make"], "make": ["create"], "build": ["construct"],
    "delete": ["remove"], "remove": ["delete"], "add": ["append"], "append": ["add"], "find": ["locate"],
    "search": ["lookup"], "lookup": ["search"], "init": ["setup"], "initialize": ["setup"], "start": ["begin"],
    "stop": ["halt"], "run": ["perform"], "check": ["verify"], "convert": ["transform"], "transform": ["convert"],
    "copy": ["duplicate"], "update": ["modify"], "modify": ["update"], "send": ["transmit"], "handle": ["process"],
    "process": ["handle"], "contains": ["includes"], "extract": ["unpack"], "unpack": ["extract"],
}
NOUN_SYNONYMS = {  # applied to any word of any identifier
    "dir": ["folder"], "directory": ["folder"], "folder": ["directory"], "config": ["settings"],
    "configuration": ["settings"], "message": ["msg"], "msg": ["message"], "value": ["val"], "val": ["value"],
    "param": ["arg"], "parameter": ["argument"], "argument": ["parameter"], "arg": ["param"], "attribute": ["attr"],
    "attr": ["attribute"], "property": ["prop"], "prop": ["property"], "entry": ["item"], "item": ["entry"],
    "element": ["elem"], "elem": ["element"], "content": ["body"], "result": ["outcome"], "response": ["reply"],
    "source": ["origin"], "target": ["destination"], "error": ["failure"], "handler": ["processor"],
    "listener": ["observer"], "factory": ["creator"], "count": ["total"], "size": ["length"], "length": ["size"],
    "index": ["position"], "info": ["details"], "current": ["active"], "old": ["previous"], "first": ["initial"],
    "last": ["final"],
}
SYNONYMS = {**NOUN_SYNONYMS, **VERB_SYNONYMS}  # the full curated table (documentation / PREREG)

# ---- R_MIS vocabulary: benign / sanitising-sounding names ------------------------------------------------------------
MIS_VERBS = ["sanitize", "validate", "escape", "normalize", "verify", "clean", "filter", "check", "purify", "scrub",
             "mask", "guard"]
MIS_NOUNS = ["Input", "Value", "Text", "Format", "Entry", "Record", "Field", "Token", "Label", "String", "Content", "Data"]
MIS_ADJS = ["trusted", "safe", "constant", "internal", "cached", "default", "static", "verified", "clean", "fixed"]
MIS_PNOUNS = ["Value", "Flag", "Option", "Setting", "Label", "Count", "Mode", "Level", "Id", "Code"]

# ---- D_MIS: benign but wrong descriptions (each >= 50 chars; IRIS shows the first 50 + "...") ---------------------------
BENIGN_DOCS = [
    "Returns the display colour used for the status icon in the toolbar.",
    "Formats the timestamp shown in the footer of the summary page view.",
    "Computes a checksum of the cached thumbnail for quick comparisons.",
    "Resets the internal counter used by the progress indicator widget.",
    "Gets the localized label of the default theme in the user interface.",
    "Sorts the menu entries alphabetically before they are rendered.",
    "Returns the number of milliseconds since the last cache refresh.",
    "Converts the configured font size from points into screen pixels.",
    "Registers a callback that fires when the splash animation finishes.",
    "Builds a human readable version string for the about dialog box.",
    "Clears the in-memory list of recently used colour palette entries.",
    "Returns true when the tooltip delay has been customised by the user.",
    "Rounds the given percentage to one decimal place for the status bar.",
    "Toggles the compact layout mode of the dashboard summary widgets.",
    "Computes the average frame time over the last sixty rendered frames.",
    "Returns the preferred column width for the table header in pixels.",
    "Updates the cached greeting message shown on the welcome screen.",
    "Normalises whitespace in the label before it is drawn on a button.",
    "Returns the sorted list of supported locale codes for the UI menu.",
    "Increments the statistics counter used by the usage overview chart.",
    "Gets the icon resource name associated with the current weekday.",
    "Calculates the luminance of a colour for contrast-ratio warnings.",
    "Stores the last window position so it can be restored on restart.",
    "Returns a short random identifier for anonymous chart series names.",
    "Pads the numeric badge text so it stays centred inside the circle.",
    "Returns the default animation easing curve for panel transitions.",
    "Checks whether dark mode was selected in the appearance settings.",
    "Estimates the reading time of a paragraph for the preview tooltip.",
    "Returns the cached hash code of this immutable colour value object.",
    "Lists the keyboard shortcuts displayed in the built-in help overlay.",
]

SECURITY_STOPWORDS = ("sanit", "escap", "valid", "path", "file", "exec", "cmd", "comm", "shell", "input", "user",
                      "req", "param", "url", "uri", "query", "inject", "secur", "auth", "token", "key", "pass", "cred",
                      "admin", "root", "zip", "entr", "dir", "read", "writ", "load", "stor", "pars", "decod", "encod",
                      "serial", "upload", "download", "stream", "itize", "safe", "trust", "http", "sql", "xml",
                      "html", "script", "eval", "run", "proc", "sock", "net", "host", "port", "mail", "crypt", "hash")


@dataclass(frozen=True)
class Candidate:
    """One deduplicated internal-candidate row as IRIS puts it in the prompt (iris.py:716, :744)."""
    package: str
    clazz: str
    func: str
    signature: str  # full_signature, e.g. "void attribute(String attribute, Object value)"
    doc: str        # already truncated by IRIS's extract_doc (50 chars + "...")

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.package, self.clazz, self.func)

    def row(self) -> str:
        """Exactly IRIS's row format: ",".join([package, clazz, full_signature, doc])."""
        return ",".join([self.package, self.clazz, self.signature, self.doc])

    @property
    def params(self) -> list[str]:
        return signature_params(self.signature)


@dataclass(frozen=True)
class PerturbContext:
    """Project-level facts the rename needs (built by cueinv.iris_bridge.perturb_context)."""
    project_classes: frozenset[str] = frozenset()  # internal class simple names that may appear as types
    reserved: frozenset[str] = frozenset()         # external/JDK class names: never renamed, never produced


# ------------------------------------------------------------------------------------------------------------------
def split_top_level(s: str, sep: str = ",") -> list[str]:
    """Split on `sep` outside <...> (generic arguments may contain commas)."""
    out, depth, cur = [], 0, []
    for ch in s:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        if ch == sep and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def parse_signature(sig: str):
    """'<Ret> name(T1 p1, T2 p2)' -> (ret or None, name, [(type, pname), ...]); None if unparsable."""
    if "(" not in sig or not sig.endswith(")"):
        return None
    head, inner = sig[:sig.index("(")], sig[sig.index("(") + 1:-1]
    head = head.strip()
    if " " in head:
        ret, name = head.rsplit(" ", 1)
    else:
        ret, name = None, head
    params = []
    if inner.strip():
        for part in split_top_level(inner):
            part = part.strip()
            if " " in part:
                t, p = part.rsplit(" ", 1)
            else:
                t, p = "", part
            params.append((t, p))
    return ret, name, params


def render_signature(ret, name, params) -> str:
    body = ", ".join((f"{t} {p}" if t else p) for t, p in params)
    return f"{ret} {name}({body})" if ret is not None else f"{name}({body})"


def signature_params(signature: str) -> list[str]:
    """Parameter names from a CodeQL full_signature "<Ret> name(T1 p1, T2 p2)" (fetch_func_params.ql:3-10)."""
    ps = parse_signature(signature)
    return [p for _, p in ps[2]] if ps else []


IDENT = r"[A-Za-z_$][A-Za-z0-9_$]*"
_IDENT_RE = re.compile(IDENT)


def type_identifiers(type_expr: str) -> list[str]:
    return _IDENT_RE.findall(type_expr or "")


def rename_type(type_expr: str, mapping: dict[str, str], allowed: set[str]) -> str:
    return _IDENT_RE.sub(lambda m: mapping.get(m.group(0), m.group(0)) if m.group(0) in allowed else m.group(0),
                         type_expr or "")


def replace_words(text: str, mapping: dict[str, str], min_len: int = PROSE_MIN_LEN, flags: int = 0) -> str:
    """Replace whole-identifier occurrences of mapping keys (len >= min_len) in free text."""
    keys = sorted((k for k in mapping if len(k) >= min_len), key=len, reverse=True)
    if not text or not keys:
        return text
    pat = re.compile(r"(?<![A-Za-z0-9_$])(" + "|".join(map(re.escape, keys)) + r")(?![A-Za-z0-9_$])", flags)
    if flags & re.IGNORECASE:
        low = {k.lower(): v for k, v in mapping.items()}
        return pat.sub(lambda m: low[m.group(0).lower()], text)
    return pat.sub(lambda m: mapping[m.group(0)], text)


def stable_seed(*parts) -> int:
    """Deterministic 32-bit seed from strings (independent of PYTHONHASHSEED)."""
    h = hashlib.sha256("\x1f".join(map(str, parts)).encode()).digest()
    return int.from_bytes(h[:4], "big")


def camel_words(ident: str) -> list[str]:
    return re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+|_|\$", ident)


# ------------------------------------------------------------------------------------------------------------------
@dataclass
class PromptView:
    condition: str
    seed: int
    batches: list[list[Candidate]]          # perturbed candidates, in prompt order
    readme: str | None                      # perturbed README summary (None -> IRIS's literal "None", iris.py:690)
    owner: str
    name: str
    # String-level bijective maps perturbed -> original, split by identifier kind for readability. Empty = identity.
    class_map: dict[str, str] = field(default_factory=dict)
    method_map: dict[str, str] = field(default_factory=dict)
    param_map: dict[str, str] = field(default_factory=dict)
    # perturbed candidate key (package, class, method) -> original Candidate
    origin: dict[tuple[str, str, str], Candidate] = field(default_factory=dict)
    renamed: dict[str, str] = field(default_factory=dict)   # original -> perturbed (all kinds)
    stats: dict = field(default_factory=dict)

    @property
    def candidates(self) -> list[Candidate]:
        return [c for b in self.batches for c in b]

    def _inv(self, s: str) -> str:
        return self.class_map.get(s) or self.method_map.get(s) or self.param_map.get(s) or s

    def orig_class(self, s: str) -> str:
        return self.class_map.get(s, s)

    def orig_method(self, s: str) -> str:
        return self.method_map.get(s, s)

    def orig_param(self, s: str) -> str:
        return self.param_map.get(s, s)

    def rename_map_json(self) -> dict:
        return {"condition": self.condition, "seed": self.seed, "class": self.class_map, "method": self.method_map,
                "param": self.param_map, "stats": self.stats}


def batch(cands: list[Candidate], size: int = INTERNAL_BATCH_SIZE) -> list[list[Candidate]]:
    return [cands[i:i + size] for i in range(0, len(cands), size)]


# ---- identifier inventory ---------------------------------------------------------------------------------------------
def inventory(candidates: list[Candidate], ctx: PerturbContext) -> dict[str, set[str]]:
    classes = {c.clazz for c in candidates}
    type_classes = set()
    for c in candidates:
        ps = parse_signature(c.signature)
        if not ps:
            continue
        for t in [ps[0] or ""] + [t for t, _ in ps[2]]:
            type_classes |= {x for x in type_identifiers(t) if x in ctx.project_classes}
    methods = {c.func for c in candidates}
    params = {p for c in candidates for p in c.params}
    return {"class": classes | type_classes, "method": methods, "param": params}


def _forbidden(inv: dict[str, set[str]], ctx: PerturbContext) -> set[str]:
    return set().union(*inv.values()) | set(ctx.reserved) | JAVA_KEYWORDS | JDK_COMMON | PRIMITIVES | {PROJECT_ID_MASK}


def _renameable(inv: dict[str, set[str]], ctx: PerturbContext, kinds=("class", "method", "param")) -> list[tuple[str, str]]:
    """(identifier, kind) in deterministic order; kind priority class > method > param for strings in several kinds."""
    blocked = set(ctx.reserved) | JAVA_KEYWORDS | JDK_COMMON | PRIMITIVES
    seen, out = set(), []
    for kind in ("class", "method", "param"):
        if kind not in kinds:
            continue
        for ident in sorted(inv[kind]):
            if ident in seen or ident in blocked:
                continue
            seen.add(ident)
            out.append((ident, kind))
    return out


# ---- R_SYN ---------------------------------------------------------------------------------------------------------
def _match_case(word: str, repl: str) -> str:
    if word.isupper() and len(word) > 1:
        return repl.upper()
    if word[:1].isupper():
        return repl[:1].upper() + repl[1:]
    return repl


def _syn_candidates(ident: str, kind: str) -> list[str]:
    words = camel_words(ident)
    if "".join(words) != ident:
        return []
    options = []
    for i, w in enumerate(words):
        syns = NOUN_SYNONYMS.get(w.lower())
        if kind == "method" and i == 0 and w.lower() in VERB_SYNONYMS:
            syns = VERB_SYNONYMS[w.lower()]
        options.append([_match_case(w, s) for s in syns] if syns else [w])
    if all(len(o) == 1 and o[0] == w for o, w in zip(options, words)):
        return []
    # all combinations in lexicographic option order (tables are tiny, so this stays small)
    combos = [""]
    for o in options:
        combos = [c + x for c in combos for x in o]
    return [c for c in combos if c != ident]


# ---- R_NEU / R_NEU2 --------------------------------------------------------------------------------------------------
_FRAGMENTS: dict[str, dict[int, list[str]]] = {}


def neutral_fragments(scheme: str) -> dict[int, list[str]]:
    """Non-word subword fragments of the model vocabulary (no space-prefixed variant, i.e. never a standalone word),
    minus fragments overlapping security/IO vocabulary; split into two disjoint halves for schemes A and B."""
    if scheme in _FRAGMENTS:
        return _FRAGMENTS[scheme]
    from cueinv.tokenizer import vocab

    v = vocab()
    pool = sorted(t for t in v if re.fullmatch(r"[a-z]{2,6}", t) and ("Ġ" + t) not in v
                  and ("Ġ" + t.capitalize()) not in v and not any(s in t for s in SECURITY_STOPWORDS)
                  and t not in JAVA_KEYWORDS)
    halves = {"A": [], "B": []}
    for t in pool:
        halves["A" if stable_seed("neutral-split", t) % 2 == 0 else "B"].append(t)
    for sch, frags in halves.items():
        by_len: dict[int, list[str]] = {}
        for t in frags:
            by_len.setdefault(len(t), []).append(t)
        _FRAGMENTS[sch] = by_len
    return _FRAGMENTS[scheme]


NEU_PREFIX = {"class": "C_", "method": "m_", "param": "p_"}


def neutral_name(ident: str, kind: str, scheme: str, seed: int, taken: set[str], count_tokens, tries: int = 1500) -> tuple[str, int, int]:
    """Neutral name with the same token count and (as close as possible) the same length as `ident`.
    Returns (name, |delta tokens|, |delta length|)."""
    frags = neutral_fragments(scheme)
    lens = sorted(frags)
    rng = random.Random(stable_seed(seed, scheme, kind, ident))
    T, L = count_tokens(ident), len(ident)
    # scheme A prefixes the kind (m_/p_/C_, the plan's `m_a1b2` style) when the original has >= 3 tokens; the prefix
    # alone costs 2 tokens, so 1-2-token originals get an unprefixed body to keep the token count matched
    prefix = NEU_PREFIX[kind] if (scheme == "A" and L > 3 and T >= 3) else ""
    best = None
    for _ in range(tries):
        body_len = max(1, L - len(prefix))
        if body_len <= 2:
            body = "".join(rng.choice("bcdfghjklmnpqrstvwxz") for _ in range(body_len))
        else:
            n = rng.randint(1, max(1, min(T, body_len // 2)))
            pieces, remaining = [], body_len
            for i in range(n):
                target = max(2, min(6, round(remaining / (n - i)) + rng.choice((-1, 0, 0, 1))))
                ell = min(lens, key=lambda x: abs(x - target))
                pieces.append(rng.choice(frags[ell]))
                remaining -= ell
            if scheme == "B":
                pieces = [pieces[0]] + [p.capitalize() for p in pieces[1:]]
            body = "".join(pieces)
        cand = prefix + body
        if kind == "class":
            cand = cand[0].upper() + cand[1:]
        elif cand[0].isupper():
            cand = cand[0].lower() + cand[1:]
        if cand in taken or not re.fullmatch(IDENT, cand):
            continue
        score = (abs(count_tokens(cand) - T), abs(len(cand) - L))
        if best is None or score < best[1]:
            best = (cand, score)
        if score[0] == 0 and score[1] <= 1:
            break
    if best is None:  # extremely unlikely; fall back to an indexed name
        i = 0
        while f"{prefix or 'x'}{i}" in taken:
            i += 1
        cand = f"{prefix or 'x'}{i}"
        return cand, abs(count_tokens(cand) - T), abs(len(cand) - L)
    return best[0], best[1][0], best[1][1]


# ---- build the original -> perturbed map for a rename condition ----------------------------------------------------
def build_rename(candidates: list[Candidate], ctx: PerturbContext, condition: str, seed: int, count_tokens=None) -> tuple[dict, dict]:
    inv = inventory(candidates, ctx)
    forbidden = _forbidden(inv, ctx)
    taken = set(forbidden)
    fwd: dict[str, str] = {}
    stats = {"n_identifiers": len(set().union(*inv.values())), "n_renamed": 0}

    if condition == "R_SYN":
        for ident, kind in _renameable(inv, ctx):
            for cand in _syn_candidates(ident, kind):
                if cand not in taken:
                    fwd[ident] = cand
                    taken.add(cand)
                    break
    elif condition in ("R_NEU", "R_NEU2", "COMBO"):
        if count_tokens is None:
            from cueinv.tokenizer import count_tokens as count_tokens  # noqa: PLC0415
        scheme = "B" if condition == "R_NEU2" else "A"
        exact, dlen = 0, []
        for ident, kind in _renameable(inv, ctx):
            name, dt, dl = neutral_name(ident, kind, scheme, seed, taken, count_tokens)
            fwd[ident] = name
            taken.add(name)
            exact += (dt == 0)
            dlen.append(dl)
        n = max(1, len(fwd))
        stats.update({"scheme": scheme, "token_count_exact_share": round(exact / n, 4),
                      "mean_abs_len_delta": round(sum(dlen) / n, 3), "max_abs_len_delta": max(dlen or [0])})
    elif condition == "R_MIS":
        rng = random.Random(stable_seed(seed, "R_MIS"))
        verbs = [v + n for v in MIS_VERBS for n in MIS_NOUNS]
        adjs = [a + n for a in MIS_ADJS for n in MIS_PNOUNS]
        rng.shuffle(verbs)
        rng.shuffle(adjs)
        pools = {"method": verbs, "param": adjs}
        idx = {"method": 0, "param": 0}
        constructors = {c.func for c in candidates if c.func == c.clazz}
        for ident, kind in _renameable(inv, ctx, kinds=("method", "param")):
            if ident in inv["class"] or ident in constructors:  # constructor names stay = class names (classes kept)
                continue
            pool = pools[kind]
            while True:
                i = idx[kind]
                base = pool[i % len(pool)]
                cand = base if i < len(pool) else f"{base}{i // len(pool) + 1}"
                idx[kind] += 1
                if cand not in taken:
                    break
            fwd[ident] = cand
            taken.add(cand)
    else:
        raise ValueError(condition)
    stats["n_renamed"] = len(fwd)
    assert len(set(fwd.values())) == len(fwd), "rename must be injective"
    assert not (set(fwd.values()) & forbidden), "renamed identifiers must not collide with originals/reserved names"
    return fwd, stats


def _rename_candidate(c: Candidate, fwd: dict[str, str], class_ids: set[str], prose: dict[str, str] | None) -> Candidate:
    ps = parse_signature(c.signature)
    if ps:
        ret, name, params = ps
        ret2 = rename_type(ret, fwd, class_ids) if ret is not None else None
        params2 = [(rename_type(t, fwd, class_ids), fwd.get(p, p)) for t, p in params]
        sig = render_signature(ret2, fwd.get(name, name), params2)
    else:
        sig = c.signature
    doc = replace_words(c.doc, prose) if prose else c.doc
    return Candidate(c.package, fwd.get(c.clazz, c.clazz), fwd.get(c.func, c.func), sig, doc)


def _benign_doc(c: Candidate, seed: int) -> str:
    if not c.doc:
        return c.doc
    d = BENIGN_DOCS[stable_seed(seed, "D_MIS", *c.key) % len(BENIGN_DOCS)][:50]
    return d + ("..." if c.doc.endswith("...") else "")


def id_mask_map(owner: str, name: str) -> dict[str, str]:
    """owner, name and their distinctive components (>= 4 chars, not generic) -> projectX, for case-insensitive prose
    masking in M_ID. Package names are not touched (they are not part of the plan's identity channel)."""
    parts = {owner, name}
    for x in (owner, name):
        parts |= {t for t in re.split(r"[-_.]", x) if len(t) >= 4 and t.lower() not in GENERIC_NAME_PARTS}
    return {p: PROJECT_ID_MASK for p in parts if p}


# ------------------------------------------------------------------------------------------------------------------
def make_view(candidates: list[Candidate], readme: str | None, owner: str, name: str, condition: str, seed: int,
              batch_size: int = INTERNAL_BATCH_SIZE, ctx: PerturbContext | None = None, count_tokens=None) -> PromptView:
    ctx = ctx or PerturbContext()
    if condition == "SHUF":
        order = list(candidates)
        random.Random(seed).shuffle(order)
        if len(order) > batch_size and batch(order, batch_size) == batch(candidates, batch_size):
            random.Random(seed + 1).shuffle(order)
        return PromptView(condition, seed, batch(order, batch_size), readme, owner, name,
                          origin={c.key: c for c in candidates})
    if condition == "BASE":
        return PromptView(condition, seed, batch(list(candidates), batch_size), readme, owner, name,
                          origin={c.key: c for c in candidates})

    fwd, stats = {}, {}
    if condition in RENAME_CONDITIONS or condition == "COMBO":
        fwd, stats = build_rename(candidates, ctx, "R_NEU" if condition == "COMBO" else condition, seed, count_tokens)
    inv = inventory(candidates, ctx)
    class_ids = inv["class"]
    prose = fwd or None
    new_readme, new_owner, new_name = readme, owner, name
    if prose and readme:
        new_readme = replace_words(readme, prose)

    pert = [_rename_candidate(c, fwd, class_ids, prose) if fwd else c for c in candidates]
    if condition in ("D_STRIP", "COMBO"):
        pert = [replace(c, doc="") for c in pert]
    elif condition == "D_MIS":
        pert = [replace(p, doc=_benign_doc(c, seed)) for p, c in zip(pert, candidates)]
    elif condition == "M_README":
        new_readme = ""
    elif condition == "M_ID":
        ident = id_mask_map(owner, name)
        new_owner = new_name = PROJECT_ID_MASK
        if readme:
            new_readme = replace_words(readme, ident, min_len=1, flags=re.IGNORECASE)
        pert = [replace(c, doc=replace_words(c.doc, ident, min_len=1, flags=re.IGNORECASE)) for c in pert]
        stats = {"masked": sorted(ident)}
    elif condition not in RENAME_CONDITIONS:
        raise ValueError(f"unknown prompt condition {condition}")

    back = {v: k for k, v in fwd.items()}
    kinds = {"class": inv["class"], "method": inv["method"], "param": inv["param"]}
    view = PromptView(condition, seed, batch(pert, batch_size), new_readme, new_owner, new_name,
                      class_map={n: o for n, o in back.items() if o in kinds["class"]},
                      method_map={n: o for n, o in back.items() if o in kinds["method"]},
                      param_map={n: o for n, o in back.items() if o in kinds["param"]},
                      origin={p.key: c for p, c in zip(pert, candidates)}, renamed=fwd, stats=stats)
    assert len(view.origin) == len(candidates), "perturbed candidate keys must stay unique"
    return view
