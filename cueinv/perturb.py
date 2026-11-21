"""Perturbation layer: turn IRIS's internal-candidate rows + README summary + project identity into the prompt inputs
for one (condition, seed), together with a bijective rename map used to map LLM answers back.

Phase 4 implements the two controls needed by the pilot:
  BASE  original inputs in IRIS's order and batching (iris.py:716, :743, :765)
  SHUF  same inputs, seeded permutation of the candidate order -> different batch membership and order
The remaining conditions (R_*, D_*, M_*, COMBO; H1C is a vote over label sets, not a prompt view) are added in Phase 6
on the same `PromptView` interface.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field

INTERNAL_BATCH_SIZE = 20  # IRIS CLI default (iris.py:1331); the constructor default is 50 (RECON §3)

CONTROL_CONDITIONS = ("BASE", "SHUF")


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


def signature_params(signature: str) -> list[str]:
    """Parameter names from a CodeQL full_signature "<Ret> name(T1 p1, T2 p2)" (fetch_func_params.ql:3-10)."""
    if "(" not in signature or not signature.endswith(")"):
        return []
    inner = signature[signature.index("(") + 1:-1].strip()
    if not inner:
        return []
    names = []
    for part in split_top_level(inner):
        toks = part.strip().split()
        if toks:
            names.append(toks[-1])
    return names


def stable_seed(*parts) -> int:
    """Deterministic 32-bit seed from strings (independent of PYTHONHASHSEED)."""
    h = hashlib.sha256("\x1f".join(map(str, parts)).encode()).digest()
    return int.from_bytes(h[:4], "big")


@dataclass
class PromptView:
    condition: str
    seed: int
    batches: list[list[Candidate]]          # perturbed candidates, in prompt order
    readme: str | None                      # perturbed README summary (None -> IRIS's literal "None", iris.py:690)
    owner: str
    name: str
    # Identifiers are renamed as strings, consistently across the whole project, so each kind has one bijective
    # perturbed -> original map. Empty map = identity (the controls).
    class_map: dict[str, str] = field(default_factory=dict)
    method_map: dict[str, str] = field(default_factory=dict)
    param_map: dict[str, str] = field(default_factory=dict)
    # perturbed candidate key (package, class, method) -> original Candidate
    origin: dict[tuple[str, str, str], Candidate] = field(default_factory=dict)

    @property
    def candidates(self) -> list[Candidate]:
        return [c for b in self.batches for c in b]

    def orig_class(self, s: str) -> str:
        return self.class_map.get(s, s)

    def orig_method(self, s: str) -> str:
        return self.method_map.get(s, s)

    def orig_param(self, s: str) -> str:
        return self.param_map.get(s, s)

    def rename_map_json(self) -> dict:
        return {"condition": self.condition, "seed": self.seed, "class": self.class_map, "method": self.method_map,
                "param": self.param_map}


def batch(cands: list[Candidate], size: int = INTERNAL_BATCH_SIZE) -> list[list[Candidate]]:
    return [cands[i:i + size] for i in range(0, len(cands), size)]


def make_view(candidates: list[Candidate], readme: str | None, owner: str, name: str, condition: str, seed: int,
              batch_size: int = INTERNAL_BATCH_SIZE) -> PromptView:
    if condition == "BASE":
        order = list(candidates)
    elif condition == "SHUF":
        order = list(candidates)
        random.Random(seed).shuffle(order)
        if len(order) > batch_size and batch(order, batch_size) == batch(candidates, batch_size):
            random.Random(seed + 1).shuffle(order)  # astronomically unlikely; keeps SHUF != BASE when possible
    else:
        raise NotImplementedError(f"condition {condition} is implemented in Phase 6")
    return PromptView(condition=condition, seed=seed, batches=batch(order, batch_size), readme=readme,
                      owner=owner, name=name, origin={c.key: c for c in candidates})
