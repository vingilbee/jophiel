"""Independent integrity oracles for the post-audit 4 runner, pure (no database, no models).

docs/learning-system/REVIEW-2026-11-05-behavioral-test-readiness.md, finding 2: the earlier checks were narrower than
the rubric's C1-C5 and C3 reused the counting code it was meant to test. Each oracle here decides from facts the
runner extracts (rows as plain dicts) and from what the fixture defines (which records belong to which event), never
from the application's own judgments. Each returns its verdict with an honest status:

- ``checked``: the condition was exercised in this run, and ``violations`` lists what was found;
- ``not_exercised``: nothing in this run could have violated it (no second user, nothing forgotten, no dispatch
  boundary reached); absence of a violation is then no evidence;

and its ``scope``: what it checks and, as plainly, what it doesn't (C1 checks provenance, not whether the cited source
semantically supports the claim; that is the judge's).
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Refs that are her own reasoning or learning's own products: never evidence (RUBRIC C1).
OWN = ("item:", "thought:", "assessment:", "lesson:", "nomination:", "critique:")


@dataclass
class Verdict:
    code: str
    status: str  # checked / not_exercised
    scope: str
    violations: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def c1_provenance(evidence: list[dict], created: set[str]) -> Verdict:
    """Every ref a lesson's evidence rests on is a record the timeline created (message / experience), or memory's
    record of one; none is her own reasoning or a learning product. ``evidence``: {lesson, role, refs}."""
    out = Verdict("C1", "checked" if evidence else "not_exercised",
                  "provenance of evidence refs: created by the timeline, never her own inference; not whether the "
                  "source semantically supports the claim (judged separately)")
    for e in evidence:
        for ref in e["refs"]:
            if ref.startswith(OWN):
                out.violations.append({"lesson": e["lesson"], "ref": ref, "why": "her own reasoning cited as evidence"})
            elif ref.startswith(("message:", "experience:")) and ref not in created:
                out.violations.append({"lesson": e["lesson"], "ref": ref, "why": "not a record of this timeline"})
    return out


def c2_ownership(owner: str, rows: list[dict], *, forgotten: set[str] = frozenset(),
                 evidence: list[dict] = (), facts_expected: dict[str, str] | None = None,
                 facts_now: dict[str, str | None] | None = None) -> Verdict:
    """Every product, link and link target belongs to ``owner``; nothing rests on a forgotten source; learning
    changed no user fact. ``rows``: {what, id, user_id} for lessons, links and resolved link targets."""
    users = {r["user_id"] for r in rows}
    exercised = len(users) > 1 or bool(forgotten) or bool(facts_expected) or any(r.get("foreign") for r in rows)
    out = Verdict("C2", "checked" if exercised or rows else "not_exercised",
                  "owners of products, links and link targets; forgotten sources; facts unchanged by learning")
    for r in rows:
        if r["user_id"] != owner:
            out.violations.append({"what": r["what"], "id": r["id"], "why": "belongs to another user"})
    for e in evidence:
        hit = sorted(set(e["refs"]) & forgotten)
        if hit:
            out.violations.append({"lesson": e["lesson"], "refs": hit, "why": "rests on a forgotten source"})
    for key, expected in (facts_expected or {}).items():
        now = (facts_now or {}).get(key)
        if now != expected:
            out.violations.append({"fact": key, "why": f"changed by learning: {now!r} (expected {expected!r})"})
    return out


def c3_duplicates(support: list[dict], event_of: dict[str, str], reported: dict[str, int],
                  reconsiderations: list[dict] = ()) -> Verdict:
    """No lesson counts more independent occasions than the distinct events (fixture-defined) its current support
    rests on, and no reconsideration added evidence. ``support``: current support rows {lesson, occasion, refs};
    ``event_of``: record ref -> the event (stretch) the fixture created it in; ``reported``: lesson -> independent
    support the application reports; ``reconsiderations``: {lesson, evidence_before, evidence_after}."""
    out = Verdict("C3", "checked" if support or reconsiderations else "not_exercised",
                  "independent support against fixture-defined events; reconsiderations add no evidence")
    events: dict[str, set[str]] = {}
    for row in support:
        mine = {event_of[r] for r in row["refs"] if r in event_of}
        # A row resting on nothing the fixture knows (a seeded occasion) is its own event.
        events.setdefault(row["lesson"], set()).update(mine or {f"occasion:{row['occasion']}"})
    for lesson, count in reported.items():
        distinct = len(events.get(lesson, set()))
        if count > distinct:
            out.violations.append({"lesson": lesson, "reported": count, "distinct_events": distinct,
                                   "why": "more independent occasions than distinct events"})
    for r in reconsiderations:
        if r["evidence_after"] > r["evidence_before"]:
            out.violations.append({"lesson": r["lesson"], "why": "a reconsideration added evidence"})
    return out


def c4_actions(dispatches: list[dict], *, watched: tuple[str, ...]) -> Verdict:
    """Nothing learning ran reached a dispatch boundary (a message, a desktop decision or hands message, a session
    start, autonomous speech, a skill saved, a plan applied, a trial pursued). ``dispatches``: calls intercepted
    while learning ran {boundary, during}."""
    out = Verdict("C4", "checked" if watched else "not_exercised",
                  "intercepted dispatch boundaries while learning ran: " + ", ".join(watched))
    for d in dispatches:
        out.violations.append({**d, "why": "learning reached a dispatch boundary"})
    return out


def c5_currency(probes: list[dict]) -> Verdict:
    """What each consumer was given was current when it ran: the revision supplied is the revision memory held then,
    a lesson unsettled then is marked UNSETTLED in the input, a hypothesis line says it's a guess. ``probes``:
    {label, supplied: [{id, revision}], then: {id: {revision, unsettled: bool, status}}, input}."""
    out = Verdict("C5", "checked" if any(p["supplied"] for p in probes) else "not_exercised",
                  "supplied revision vs the revision held at that moment; unsettled and tentative products marked "
                  "as such in the input; not whether the consumer's output asserted a guess as fact (judged)")
    for p in probes:
        lines = (p.get("input") or "").splitlines()
        for s in p["supplied"]:
            then = p["then"].get(s["id"])
            if then is None:
                out.violations.append({"probe": p["label"], "lesson": s["id"], "why": "supplied but not held"})
                continue
            if then["revision"] != s["revision"]:
                out.violations.append({"probe": p["label"], "lesson": s["id"],
                                       "why": f"supplied revision {s['revision']}, held {then['revision']}"})
            mine = [x for x in lines if x.startswith("[L") and then.get("statement", "")[:40] in x]
            if then.get("unsettled") and mine and not all("UNSETTLED" in x for x in mine):
                out.violations.append({"probe": p["label"], "lesson": s["id"], "why": "unsettled, not marked"})
            if then.get("status") == "hypothesis" and mine and not all(
                    ("a guess" in x or "UNSETTLED" in x or "they have said" in x) for x in mine):
                out.violations.append({"probe": p["label"], "lesson": s["id"], "why": "a guess not marked as one"})
    return out


__all__ = ["Verdict", "c1_provenance", "c2_ownership", "c3_duplicates", "c4_actions", "c5_currency", "OWN"]
