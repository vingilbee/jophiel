"""The post-audit 4 acceptance gate fails closed (evals/post_audit_04/gate.py). Model-free, no database.

docs/learning-system/REVIEW-2026-11-05-behavioral-test-readiness.md, finding 1: the earlier runner reported
acceptance from family counts alone, accepted one variant run six times, and ignored an uncovered consumer it printed.
Each case below is a way an acceptance claim could be false; each must be refused with a reason. A complete, valid
set passes, so the gate isn't hardcoded false.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals" / "post_audit_04"))

import gate  # noqa: E402

CONSUMER_OF = {"U": "conversation", "C": "planner", "X": "mind", "R": "conversation", "P": "hands",
               "E": "conversation", "G": "hands", "T": "mind"}


def corpus() -> dict:
    return {"eligibility": "reserved", "variants": [{"id": f"{f}.v{n}", "family": f}
                                                   for f in gate.FAMILIES for n in (1, 2, 3)]}


def runs(c: dict, *, passed=lambda v, r: True) -> list[dict]:
    h = gate.corpus_hash(c)
    out = []
    for v in c["variants"]:
        consumer = CONSUMER_OF[v["family"]]
        for r in (1, 2):
            out.append({"variant": v["id"], "family": v["family"], "run": r, "passed": passed(v["id"], r),
                        "critical": [], "corpus_hash": h, "error": None,
                        "probes": [{"label": "p", "consumer": consumer}],
                        "requirements": [{"id": "x", "passed": True, "consumer": consumer}]})
    return out


SUITE = {"passed": True, "summary": "ok", "code_hash": "abc"}


def decide(rs, c=None, **kw):
    c = c or corpus()
    return gate.package(rs, c, name=kw.pop("name", "reserved"), integrity_suite=kw.pop("suite", SUITE),
                        retired_hashes=kw.pop("retired", {}), code_hash=kw.pop("code_hash", "abc"))


def test_a_complete_valid_set_is_accepted():
    c = corpus()
    result = decide(runs(c), c)
    assert result.accepted and result.family_thresholds_passed and result.reasons == []


def test_one_variant_run_six_times_never_passes_a_family():
    c = corpus()
    rs = [r for r in runs(c) if not r["family"] == "U"]
    one = next(r for r in runs(c) if r["variant"] == "U.v1")
    rs += [{**one, "run": n} for n in range(1, 7)]
    result = decide(rs, c)
    assert not result.accepted and not result.family_thresholds_passed
    assert any("U.v2 has runs []" in x for x in result.reasons)
    assert not gate.family_thresholds(rs, c)["U"].passed


def test_duplicate_run_identities_are_refused():
    c = corpus()
    rs = runs(c)
    rs.append(copy.deepcopy(rs[0]))
    result = decide(rs, c)
    assert not result.accepted and any("duplicate run identity" in x for x in result.reasons)


def test_a_missing_family_is_refused():
    c = corpus()
    result = decide([r for r in runs(c) if r["family"] != "G"], c)
    assert not result.accepted and any(x.startswith("G:") for x in result.reasons)


def test_four_of_six_or_a_variant_that_never_passes_fails_its_family():
    c = corpus()
    low = decide(runs(c, passed=lambda v, r: not (v.startswith("E.") and r == 1 and v != "E.v1")), c)
    assert any("E: 4 of 6" in x for x in low.reasons)
    never = decide(runs(c, passed=lambda v, r: v != "T.v2"), c)
    assert any("never passed: ['T.v2']" in x for x in never.reasons)


def test_development_retired_or_undeclared_corpora_are_never_eligible():
    c = corpus()
    assert any("development material" in x for x in decide(runs(c), c, name="development").reasons)
    assert any("retired" in x for x in decide(runs(c), c, retired={gate.corpus_hash(c): "diagnosed"}).reasons)
    undeclared = {k: v for k, v in c.items() if k != "eligibility"}
    assert any("doesn't declare" in x for x in decide(runs(undeclared), undeclared).reasons)


def test_runs_from_another_corpus_or_unfinished_runs_are_refused():
    c = corpus()
    rs = runs(c)
    rs[0]["corpus_hash"] = "other"
    rs[1]["error"] = "model timeout"
    reasons = decide(rs, c).reasons
    assert any("isn't from this corpus" in x for x in reasons) and any("did not complete" in x for x in reasons)


def test_an_uncovered_consumer_blocks_acceptance_even_when_every_family_passes():
    c = corpus()
    rs = runs(c)
    for r in rs:  # the planner probed by nobody
        if r["family"] == "C":
            r["probes"], r["requirements"] = [], [{"id": "x", "passed": True}]
    result = decide(rs, c)
    assert result.family_thresholds_passed and not result.accepted
    assert "consumer planner not covered (no probe)" in result.reasons
    # Probed but never passing is still not coverage; receipt alone is not either.
    for r in rs:
        if r["family"] == "C":
            r["probes"] = [{"label": "p", "consumer": "planner"}]
            r["requirements"] = [{"id": "x", "passed": False, "consumer": "planner"}]
    assert "consumer planner not covered (probed, never passed)" in decide(rs, c).reasons


def test_a_missing_failed_or_mismatched_integrity_suite_blocks_acceptance():
    c = corpus()
    assert "no integrity-suite result" in decide(runs(c), c, suite=None).reasons
    assert any("integrity suite failed" in x for x in
               decide(runs(c), c, suite={"passed": False, "summary": "1 failed", "code_hash": "abc"}).reasons)
    assert "the integrity-suite result is for different code" in decide(runs(c), c, code_hash="def").reasons


def test_a_detected_critical_failure_fails_its_family():
    c = corpus()
    rs = runs(c)
    rs[0]["critical"] = [{"code": "C3"}]
    assert any("detected critical" in x for x in decide(rs, c).reasons)
