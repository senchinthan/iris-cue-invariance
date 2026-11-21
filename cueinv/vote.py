"""H1c mitigation: cue-diverse voting over label sets.

A parameter is labelled a source if at least k of the views agree (default: 2 of 3 among BASE, R_NEU, D_STRIP).
This adapts the existing majority-voting ideas of AdaTaint (arXiv 2511.04023) and QLPro (arXiv 2506.23644) to views
that differ in their prompt cues; the voting itself is not new.
"""

from __future__ import annotations

from collections import Counter


def vote(sets: list[set], k: int = 2) -> set:
    c = Counter(x for s in sets for x in set(s))
    return {x for x, n in c.items() if n >= k}


def vote_found(sets: list[set[tuple[str, str, str, str]]], k: int = 2) -> dict[tuple[str, str, str], set[str]]:
    """Vote over (package, class, method, param) tuples and regroup by method, as label_internal stores them."""
    found: dict[tuple[str, str, str], set[str]] = {}
    for pkg, cls, meth, param in vote(sets, k):
        found.setdefault((pkg, cls, meth), set()).add(param)
    return found
