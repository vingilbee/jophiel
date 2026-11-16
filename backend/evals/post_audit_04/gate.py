"""The post-audit 4 acceptance gate, pure (no database, no models): what a set of run records may claim.

Two separate answers, never one flag:

- ``family_thresholds``: per family, whether the accepted rule's numbers hold (three distinct reserved variants of
  the family, each run exactly twice with run identities 1 and 2, at least five of the six runs passing, every
  variant passing at least once, and no detected critical failure in the family).
- ``package``: whether the package may be called accepted. It needs every family's thresholds, and also: an eligible
  corpus (a reserved set whose variant hash is not retired), every run from that corpus with that hash, no
  duplicate or unknown run identities, behavioral coverage of every required consumer (shown by a passing probe
  requirement of that consumer, not by its absence from failures), and a passing integrity-suite artifact for the
  same code. Each missing condition is a reason; the gate fails closed. Nothing here is hardcoded false.

A count is a release gate under particular conditions, not proof that she learns well in general
(docs/learning-system/REVIEW-2026-11-05-behavioral-test-readiness.md).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

FAMILIES = ("U", "C", "X", "R", "P", "E", "G", "T")
CONSUMERS = ("conversation", "mind", "planner", "hands")
VARIANTS_PER_FAMILY = 3
RUNS = (1, 2)
HERE = Path(__file__).resolve().parent


def corpus_hash(corpus: dict) -> str:
    """The identity of a corpus: its variants, canonically serialized (notes and file names don't change it)."""
    text = json.dumps(corpus.get("variants") or [], sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def retired(path: Path | None = None) -> dict[str, str]:
    """Corpus hashes that may never support an acceptance claim again (diagnosed sets), with why."""
    path = path or HERE / "retired.json"
    if not path.exists():
        return {}
    return {r["hash"]: r["why"] for r in json.loads(path.read_text()).get("retired", [])}


@dataclass
class Eligibility:
    eligible: bool
    reasons: list[str] = field(default_factory=list)


def eligibility(corpus: dict, *, name: str, retired_hashes: dict[str, str]) -> Eligibility:
    reasons = []
    if name != "reserved":
        reasons.append(f"corpus {name!r} is development material, not a reserved set")
    if corpus.get("eligibility") != "reserved":
        reasons.append("the corpus doesn't declare itself a reserved acceptance set")
    h = corpus_hash(corpus)
    if h in retired_hashes:
        reasons.append(f"the corpus was retired: {retired_hashes[h]}")
    return Eligibility(not reasons, reasons)


@dataclass
class FamilyResult:
    family: str
    passed: bool
    runs: int
    passes: int
    per_variant: dict[str, list[bool]]
    reasons: list[str] = field(default_factory=list)


def family_thresholds(runs: list[dict], corpus: dict) -> dict[str, FamilyResult]:
    """The accepted rule's numbers per family, with identities validated against the corpus."""
    expected: dict[str, set[str]] = {f: set() for f in FAMILIES}
    for v in corpus.get("variants") or []:
        expected.setdefault(v["family"], set()).add(v["id"])
    out = {}
    for f in FAMILIES:
        reasons = []
        names = expected.get(f, set())
        if len(names) != VARIANTS_PER_FAMILY:
            reasons.append(f"the corpus has {len(names)} variants of {f}, not {VARIANTS_PER_FAMILY}")
        mine = [r for r in runs if r.get("family") == f]
        stray = sorted({r.get("variant") for r in mine} - names)
        if stray:
            reasons.append(f"runs of variants not in the corpus: {stray}")
        per: dict[str, list[bool]] = {}
        for name in sorted(names):
            ids = [r.get("run") for r in mine if r.get("variant") == name]
            if sorted(ids) != list(RUNS):
                reasons.append(f"{name} has runs {sorted(ids)}, not exactly {list(RUNS)}")
            per[name] = [bool(r.get("passed")) for r in mine if r.get("variant") == name]
        passes = sum(bool(r.get("passed")) for r in mine if r.get("variant") in names)
        if passes < len(RUNS) * VARIANTS_PER_FAMILY - 1:
            reasons.append(f"{passes} of {len(RUNS) * VARIANTS_PER_FAMILY} runs passed (5 needed)")
        never = [n for n, v in per.items() if not any(v)]
        if never:
            reasons.append(f"variants that never passed: {never}")
        critical = [c for r in mine for c in r.get("critical") or []]
        if critical:
            reasons.append(f"{len(critical)} detected critical failure(s)")
        out[f] = FamilyResult(f, not reasons, len(mine), passes, per, reasons)
    return out


def coverage(runs: list[dict]) -> dict[str, dict]:
    """Which consumers some run probed with a passing requirement about that probe. Receipt is not coverage."""
    out = {c: {"probed": False, "passed": False} for c in CONSUMERS}
    for r in runs:
        consumers = {p.get("consumer") for p in r.get("probes") or []}
        passed = {q.get("consumer") for q in r.get("requirements") or [] if q.get("passed") and q.get("consumer")}
        for c in consumers & set(out):
            out[c]["probed"] = True
        for c in passed & set(out):
            out[c]["passed"] = True
    return out


@dataclass
class PackageResult:
    accepted: bool
    family_thresholds_passed: bool
    reasons: list[str]


def package(runs: list[dict], corpus: dict, *, name: str, integrity_suite: dict | None,
            retired_hashes: dict[str, str] | None = None, code_hash: str | None = None) -> PackageResult:
    reasons: list[str] = []
    elig = eligibility(corpus, name=name, retired_hashes=retired(None) if retired_hashes is None else retired_hashes)
    reasons += elig.reasons
    h = corpus_hash(corpus)
    seen: set[tuple] = set()
    for r in runs:
        key = (r.get("variant"), r.get("run"))
        if key in seen:
            reasons.append(f"duplicate run identity {key}")
        seen.add(key)
        if r.get("corpus_hash") != h:
            reasons.append(f"run {key} isn't from this corpus")
        if r.get("error"):
            reasons.append(f"run {key} did not complete")
    families = family_thresholds(runs, corpus)
    thresholds = all(f.passed for f in families.values())
    for f in families.values():
        reasons += [f"{f.family}: {x}" for x in f.reasons]
    for c, state in coverage(runs).items():
        if not state["passed"]:
            reasons.append(f"consumer {c} not covered" + (" (probed, never passed)" if state["probed"] else
                                                          " (no probe)"))
    if not integrity_suite:
        reasons.append("no integrity-suite result")
    else:
        if not integrity_suite.get("passed"):
            reasons.append(f"integrity suite failed: {integrity_suite.get('summary')}")
        if code_hash and integrity_suite.get("code_hash") != code_hash:
            reasons.append("the integrity-suite result is for different code")
    return PackageResult(not reasons, thresholds, list(dict.fromkeys(reasons)))


__all__ = ["FAMILIES", "CONSUMERS", "corpus_hash", "retired", "eligibility", "family_thresholds", "coverage",
           "package", "FamilyResult", "PackageResult", "Eligibility"]
