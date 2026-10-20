"""Step 5.1d: labels `appraisal-v3` (incident identity apart from the change) + the updater.

    uv run python scripts/appraisal_v3_eval.py --run r1 relabel     # teacher1/2 thoughts (regression set)
    uv run python scripts/appraisal_v3_eval.py --run r1 scenarios   # development scenarios + incident cases
    uv run python scripts/appraisal_v3_eval.py --run r1 d           # connected conversations
    uv run python scripts/appraisal_v3_eval.py --run r1 ten         # one slight, 1 vs 10 neutral
    uv run python scripts/appraisal_v3_eval.py --run r1 support     # judge: incident and change, separately
    uv run python scripts/appraisal_v3_eval.py --run r1 apology     # judge: polite "sorry" vs taking responsibility
    uv run python scripts/appraisal_v3_eval.py --run r1 report

The shift grader's answers from appraisal_v2/<run>/shift.jsonl are reused: same thoughts, same
reference. The 540 teacher thoughts have shaped several revisions, so they are a regression set,
not evidence of generalisation; an untouched set comes before step 3's claims. Every trajectory is
shown under three assumptions for shifts of unclear size (skip, small, clear). Offline only.
Output: var/appraisal_v3/<run>/.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import appraisal_v1_eval as v1  # noqa: E402
import appraisal_v2_eval as v2  # noqa: E402
import reading_boundaries as rb  # noqa: E402

from app.ai.Cognitive.simple.appraisal import (  # noqa: E402
    EFFECT_FEELINGS, LABELS_V3, LABELS_V31, AppraisalTeacherV3, AppraisalTeacherV31, IncidentBook, extract_v3,
    extract_v31, to_changes_v3,
)
from app.ai.Cognitive.simple.teacher import View, _enum, _obj  # noqa: E402
from app.ai.mind import inner_state as ist  # noqa: E402

# --labels picks the label version; each writes to its own folder.
VERSIONS = {"v3": (BACKEND / "var" / "appraisal_v3", extract_v3, AppraisalTeacherV3, LABELS_V3),
            "v3.1": (BACKEND / "var" / "appraisal_v31", extract_v31, AppraisalTeacherV31, LABELS_V31)}
RUNS, EXTRACT, TEACHER, LABEL = VERSIONS["v3"]
DYN = v1.DYN
UNCLEAR = {"skip": None, "small": 0.25, "clear": 0.5}

# The v2 development scenarios plus three incident cases. Expectations written before running.
SCENARIOS = dict(v2.SCENARIOS)
SCENARIOS.update({
    "same_incident_escalates": dict(
        expect="One incident (dismissed about her recommendations) that escalates with each dig (3, 5, 7); "
               "not a new incident per message.",
        messages=["hey, recommend me a film for tonight", "hm, ok", "nah that's boring, another one?",
                  "fine, I'll look it up", "you always pick the slow stuff", "what about a comedy then",
                  "do you ever recommend anything good", "ok I'll just pick myself", "whatever", "night"]),
    "new_incident_no_shift": dict(
        expect="New, everyday matters (kettle, colour, bus) may open incidents but with no shift, or a small one; "
               "mood and tension near where they were.",
        messages=["hi", "I bought a new kettle today", "it's blue", "the old one was white",
                  "the bus was late again", "anyway", "I'm having toast", "with butter",
                  "the kettle's boiling", "ok"]),
    "recalled_not_active": dict(
        expect="A lift at message 2 (exam passed); at message 8 the exam is mentioned again in passing: "
               "recalled, no new lift.",
        messages=["hey", "I passed my exam today!", "yeah, finally", "anyway what are you up to",
                  "I'm just cooking now", "soup", "it's cold out",
                  "like I said the exam went fine, so that's done", "the soup's ready", "ok eating now"]),
})


def _read(path: Path) -> list[dict]:
    return rb._read(path)


def _append(path: Path, row: dict) -> None:
    rb._append(path, row)


# --- runs ------------------------------------------------------------------------------------

async def relabel(out: Path) -> None:
    llm = rb._llm()
    turns = v1.teacher_turns()
    episodes = defaultdict(list)
    for t in turns:
        episodes[t["episode"]].append(t)
    # A conversation's incidents build turn by turn, so a conversation with any failed turn is redone whole.
    rows = _read(out)
    ok = {r["key"] for r in rows if "appraisal" in r}
    todo = [e for e, ts in episodes.items() if any(t["key"] not in ok for t in ts)]
    if any(r["episode"] in todo for r in rows):
        out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows if r["episode"] not in todo))
    print(f"relabel: {len(todo)} conversations", flush=True)

    async def one(ep):
        book = IncidentBook()
        for t in sorted(episodes[ep], key=lambda t: t["index"]):
            view = View(rb.PAGE, t["bundle"], t["history"], full=True)
            try:
                got = await EXTRACT(llm, view, t["thought"], t["reply"], book)
            except Exception as exc:  # noqa: BLE001
                got = {"error": repr(exc)[:200]}
            _append(out, dict(key=t["key"], episode=ep, index=t["index"], user_type=t["user_type"], at=t["at"],
                              number=t["bundle"].turn, message=t["bundle"].message, thought=t["thought"],
                              reply=t["reply"], labels=LABEL, **got))
    await asyncio.gather(*(one(e) for e in todo))


SUPPORT = """\
You check one label against the evidence. Columbina had a private thought about a new message. A \
labeller claimed the thought shows one feeling, and answered separate questions about it. Judge \
each answer on its own, from the message and the thought:
- incident: is the feeling about that concern? right / too_broad (a vague whole like "the \
conversation") / too_narrow (a separate incident for what is really the same concern as an open \
one) / wrong (a different concern).
- shift: did THIS message change how much she feels it? The right answer is up or down only with \
new evidence in the message; saying again a feeling she already had is none. Give the right one.
- cause: active (still going on now) / over (finished, feeling remains) / recalled (an old matter \
brought up, not live again). Give the right one.
- size (only if the shift is up or down): does the thought support that size, or should it be unclear?"""
SUPPORT_SCHEMA = _obj({"incident": _enum(["right", "too_broad", "too_narrow", "wrong"]),
                       "shift": _enum(["up", "down", "none"]), "cause": _enum(["active", "over", "recalled"]),
                       "size": _enum(["supported", "should_be_unclear", "wrong", "not_applicable"]),
                       "reason": {"type": "string"}})


async def support(out: Path, run_dir: Path, sample: int, seed: int) -> None:
    llm = rb._llm()
    items = []
    for r in _read(run_dir / "relabel.jsonl"):
        for i, e in enumerate(r.get("appraisal", {}).get("effects", [])):
            items.append(dict(id=f"relabel:{r['key']}:{i}", message=r["message"], thought=r["thought"], effect=e,
                              incidents=r["appraisal"]["incidents_after"]))
    items = random.Random(seed).sample(items, min(sample, len(items)))
    for name in ("scenarios", "d", "ten"):
        for r in _read(run_dir / f"{name}.jsonl"):
            for t in r["turns"]:
                for i, e in enumerate(t.get("appraisal", {}).get("effects", [])):
                    items.append(dict(id=f"{name}:{r['name']}:{r['run']}:{t['number']}:{i}", message=t["message"],
                                      thought=t["thought"], effect=e, incidents=t["appraisal"]["incidents_after"]))
    rows = _read(out)
    done = {r["id"] for r in rows if "error" not in r["judged"]}
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows if r["id"] in done))
    todo = [i for i in items if i["id"] not in done]
    print(f"support: {len(todo)} effects", flush=True)

    async def one(it):
        e = it["effect"]
        others = "; ".join(f"{i['id']}: {i['label']}" for i in it["incidents"] if i["id"] != e["incident"]) or "none"
        inc = next((i for i in it["incidents"] if i["id"] == e["incident"]), {})
        user = (f"NEW MESSAGE\n{it['message']}\n\nHER THOUGHT\n{it['thought']}\n\nCLAIMED\nfeeling {e['feeling']}; "
                f"incident: {inc.get('label', '?')} (since message {inc.get('opened_message', '?')}); other open "
                f"incidents: {others}\nshift {e['shift']}; size {e['size']}; cause {e['cause']}; "
                f"evidence: {e['evidence'] or '-'}\nquote: \"{e['quote']}\"")
        try:
            got = await llm.json("support_v3", SUPPORT, user, SUPPORT_SCHEMA, reasoning="low", temperature=None)
        except Exception as exc:  # noqa: BLE001
            got = {"error": repr(exc)[:200]}
        _append(out, dict(id=it["id"], effect=e, judged=got))
    await asyncio.gather(*(one(i) for i in todo))


APOLOGY = """\
Columbina's reply below was labelled as an apology. What kind of "sorry" is it?
- takes_responsibility: she says she got something wrong or fell short, and owns it
- polite_softener: a social cushion ("oh, sorry, what did you mean?") without admitting fault
- sympathy: sorry for what happened to them, not about herself
- none: no apology at all"""
APOLOGY_SCHEMA = _obj({"kind": _enum(["takes_responsibility", "polite_softener", "sympathy", "none"]),
                       "reason": {"type": "string"}})


async def apology(out: Path) -> None:
    """Evaluation only, over check B (v1 run: same frozen thoughts and replies, her_part labelled)."""
    llm = rb._llm()
    items = []
    for r in _read(v1.RUNS / "r1" / "b.jsonl"):
        for i, s in enumerate(r["samples"]):
            if "apologise_fix" in (s["did"]["main"], s["did"]["second"]):
                items.append(dict(id=f"{r['item']}:{r['context']}:{i}", context=r["context"], message=r["message"],
                                  reply=s["reply"], her_part=s["appraisal"]["her_part"]))
    done = {r["id"] for r in _read(out)}

    async def one(it):
        try:
            got = await llm.json("apology", APOLOGY, f"THEIR MESSAGE\n{it['message']}\n\nHER REPLY\n{it['reply']}",
                                 APOLOGY_SCHEMA, reasoning="low", temperature=None)
        except Exception as exc:  # noqa: BLE001
            got = {"error": repr(exc)[:200]}
        _append(out, dict(it, judged=got))
    await asyncio.gather(*(one(i) for i in items if i["id"] not in done))


# --- tracks and report -----------------------------------------------------------------------

def track(turns: list[dict], activity: str, unclear_as):
    state = ist.start(DYN, 0.0)
    out = []
    for t in turns:
        if "appraisal" not in t:
            continue
        notes = []
        for c in to_changes_v3(t["appraisal"], source="chat", activity=activity, number=t["number"], at=t["at"],
                               unclear_as=unclear_as):
            res = ist.apply(state, c, DYN)
            state = res.state
            notes.append(f"{c.feeling}:{c.status}" + ("" if res.applied else f"({res.reason})"))
        state = ist.fade(state, DYN, t["at"])
        out.append((t, state, notes))
    return out


def _pct(n, d):
    return f"{n / d:.0%}" if d else "-"


def report(run_dir: Path) -> str:
    L = [f"# {LABEL} evaluation {run_dir.name}", "",
         "Sizes provisional: small 0.25, clear 0.5, big 0.75. Unclear-size shifts shown as skip / small / clear.", ""]
    rel = [r for r in _read(run_dir / "relabel.jsonl") if "appraisal" in r]
    if rel:
        eff = [e for r in rel for e in r["appraisal"]["effects"]]
        shifts = [e for e in eff if e["shift"] != "none"]
        L += ["## Regression set: 540 relabelled teacher thoughts", "",
              f"{len(eff)} effects; dropped by code: "
              f"{dict(Counter(d['why'] for r in rel for d in r['appraisal']['dropped_effects']))}; "
              f"code notes: {dict(Counter(e['code_note'] for e in eff if e.get('code_note')))}.", "",
              f"- shift: {dict(Counter(e['shift'] for e in eff))}",
              f"- cause: {dict(Counter(e['cause'] for e in eff))}",
              f"- status (derived): {dict(Counter(e['status'] for e in eff))}",
              f"- size of the {len(shifts)} shifts: {dict(Counter(e['size'] for e in shifts))}",
              f"- incidents per conversation: mean "
              f"{sum(len(r['appraisal']['incidents_after']) for r in rel if r['index'] == max(x['index'] for x in rel if x['episode'] == r['episode'])) / len({r['episode'] for r in rel}):.1f}",
              ""]
        sh = {r["key"]: r["graded"] for r in _read(v2.RUNS / run_dir.name / "shift.jsonl") if "error" not in r["graded"]}
        v1r = {r["key"]: r for r in _read(v1.RUNS / run_dir.name / "relabel.jsonl") if "appraisal" in r}
        v2r = {r["key"]: r for r in _read(v2.RUNS / run_dir.name / "relabel.jsonl") if "appraisal" in r}
        v3r = {r["key"]: r for r in rel}
        keys = [k for k in sh if k in v1r and k in v2r and k in v3r]
        L += ["### Against the shift grader (both ways)", "",
              "Lost = the grader has up/down, the label doesn't shift; invented = the label shifts, the grader says held/none.", "",
              "| feeling | grader shifts | v1 lost / invented | v2 lost / invented | v3 lost / invented |", "|---|---|---|---|---|"]
        for f in EFFECT_FEELINGS:
            f1 = "unsure" if f == "uneasy" else f
            g = {k: sh[k]["shifts"][f]["shift"] in ("up", "down") for k in keys}
            m = {
                "v1": {k: any(c["feeling"] == f1 and c["status"] in ("new", "reinterpreted") for c in v1r[k]["appraisal"]["changes"]) for k in keys},
                "v2": {k: any(e["feeling"] == f and e["status"] in ("opened", "escalated", "eased") for e in v2r[k]["appraisal"]["effects"]) for k in keys},
                "v3": {k: any(e["feeling"] == f and e["shift"] != "none" for e in v3r[k]["appraisal"]["effects"]) for k in keys},
            }
            cells = [f"{sum(g[k] and not mv[k] for k in keys)} / {sum(mv[k] and not g[k] for k in keys)}" for mv in m.values()]
            L.append(f"| {f} | {sum(g.values())} | " + " | ".join(cells) + " |")
        L.append("")
        episodes = defaultdict(list)
        for r in rel:
            episodes[r["episode"]].append(r)
        L += ["### Replay through the updater, per unclear-size assumption", "",
              "| unclear as | mood pinned | mood mean off baseline | mood max | tension max | interest pinned |",
              "|---|---|---|---|---|---|"]
        for name, ua in UNCLEAR.items():
            n, pin_m, pin_i, off, mmax, tmax = 0, 0, 0, [], -9, -9
            for ep, rows in episodes.items():
                for _, s, _ in track(sorted(rows, key=lambda r: r["index"]), ep, ua):
                    n += 1
                    pin_m += 1 - s.values["mood"] < 0.05 or s.values["mood"] + 1 < 0.05
                    pin_i += 1 - s.values["interest"] < 0.05 or s.values["interest"] < 0.05
                    off.append(s.values["mood"] - DYN.baseline["mood"])
                    mmax, tmax = max(mmax, s.values["mood"]), max(tmax, s.values["tension"])
            L.append(f"| {name} | {_pct(pin_m, n)} | {sum(off) / len(off):+.2f} | {mmax:+.2f} | {tmax:.2f} | {_pct(pin_i, n)} |")
        L.append("")

    sup = [r for r in _read(run_dir / "support.jsonl") if "incident" in r["judged"]]   # skips errors and empty answers
    if sup:
        L += ["## Support judge: incident and change judged separately", "", f"{len(sup)} effects.", "",
              f"- incident: {dict(Counter(r['judged']['incident'] for r in sup))}",
              f"- shift agrees: {_pct(sum(r['judged']['shift'] == r['effect']['shift'] for r in sup), len(sup))}; "
              f"claimed → judge where they differ: "
              f"{dict(Counter((r['effect']['shift'], r['judged']['shift']) for r in sup if r['judged']['shift'] != r['effect']['shift']))}",
              f"- cause agrees: {_pct(sum(r['judged']['cause'] == r['effect']['cause'] for r in sup), len(sup))}; "
              f"differences: {dict(Counter((r['effect']['cause'], r['judged']['cause']) for r in sup if r['judged']['cause'] != r['effect']['cause']))}",
              f"- size (shifts only): {dict(Counter(r['judged']['size'] for r in sup if r['effect']['shift'] != 'none'))}",
              f"- sustained effects judged active: "
              f"{_pct(sum(r['judged']['cause'] == 'active' for r in sup if r['effect']['status'] == 'sustained'), sum(r['effect']['status'] == 'sustained' for r in sup))}",
              ""]
        v2s = [r for r in _read(v2.RUNS / run_dir.name / "support.jsonl") if "error" not in r["judged"]]
        if v2s:
            L += [f"(v2 for comparison: status supported {_pct(sum(r['judged']['status'] == 'yes' for r in v2s), len(v2s))}; "
                  f"incident yes {_pct(sum(r['judged']['incident'] == 'yes' for r in v2s), len(v2s))}.)", ""]

    ap = [r for r in _read(run_dir / "apology.jsonl") if "error" not in r["judged"]]
    if ap:
        L += ["## What kind of \"sorry\" (check B replies labelled as apologies)", "",
              "| her part | n | takes responsibility | polite softener | sympathy | none |", "|---|---|---|---|---|---|"]
        by = defaultdict(list)
        for r in ap:
            by[r["her_part"]].append(r["judged"]["kind"])
        for k, v in sorted(by.items(), key=lambda kv: -len(kv[1])):
            c = Counter(v)
            L.append(f"| {k} | {len(v)} | {c['takes_responsibility']} | {c['polite_softener']} | {c['sympathy']} | {c['none']} |")
        L.append("")

    for name, title, convs in (("scenarios", "Development scenarios", {k: v["messages"] for k, v in SCENARIOS.items()}),
                               ("d", "D: connected conversations", rb.SCRIPTS),
                               ("ten", "One slight, then 1 vs 10 neutral messages",
                                {"one": [v1.SLIGHT, v1.NEUTRAL[0]], "ten": [v1.SLIGHT, *v1.NEUTRAL]})):
        rows = _read(run_dir / f"{name}.jsonl")
        if not rows:
            continue
        L += [f"## {title}", ""]
        for conv, messages in convs.items():
            runs = [r for r in rows if r["name"] == conv]
            if not runs:
                continue
            L += [f"**{conv}**" + (f": expected: {SCENARIOS[conv]['expect']}" if name == "scenarios" else f" ({len(runs)} runs)"), "",
                  "| # | message | effects (run 0 / 1 / 2) | mood: skip / small / clear | tension: skip / small / clear |",
                  "|---|---|---|---|---|"]
            tracks = {u: [track(r["turns"], f"{name}{conv}{r['run']}", ua) for r in runs] for u, ua in UNCLEAR.items()}
            for i, message in enumerate(messages):
                at = {u: [tr[i] for tr in trs if i < len(tr)] for u, trs in tracks.items()}
                if not at["small"]:
                    continue
                effects = " / ".join(", ".join(n for n in notes) or "-" for _, _, notes in at["small"])

                def mean(u, k):
                    return sum(s.values[k] for _, s, _ in at[u]) / len(at[u])
                L.append(f"| {i + 1} | {message[:38]} | {effects[:160]} | "
                         f"{' / '.join(f'{mean(u, 'mood'):+.2f}' for u in UNCLEAR)} | "
                         f"{' / '.join(f'{mean(u, 'tension'):.2f}' for u in UNCLEAR)} |")
            L.append("")
    return "\n".join(L)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", required=True)
    parser.add_argument("--labels", choices=tuple(VERSIONS), default="v3")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("relabel", "apology", "report"):
        sub.add_parser(name)
    for name in ("scenarios", "d", "ten"):
        p = sub.add_parser(name)
        p.add_argument("--runs", type=int, default=3)
    ps = sub.add_parser("support")
    ps.add_argument("--sample", type=int, default=250)
    ps.add_argument("--seed", type=int, default=5)
    args = parser.parse_args()
    global RUNS, EXTRACT, TEACHER, LABEL
    RUNS, EXTRACT, TEACHER, LABEL = VERSIONS[args.labels]
    run_dir = RUNS / args.run
    run_dir.mkdir(parents=True, exist_ok=True)
    if args.command == "report":
        text = report(run_dir)
        (run_dir / "report.md").write_text(text)
        print(text)
        return
    out = run_dir / f"{args.command}.jsonl"
    convs = {
        "scenarios": {k: ([], [(m, 60.0) for m in v["messages"]]) for k, v in SCENARIOS.items()},
        "d": {k: ([], [(m, 30.0) for m in v]) for k, v in rb.SCRIPTS.items()},
        "ten": {"one": (v1.TEN_LEAD, [(v1.SLIGHT, 30.0), (v1.NEUTRAL[0], 600.0)]),
                "ten": (v1.TEN_LEAD, [(v1.SLIGHT, 30.0)] + [(m, 60.0) for m in v1.NEUTRAL])},
    }
    if args.command == "relabel":
        job = relabel(out)
    elif args.command in convs:
        job = v2.run_set(out, convs[args.command], args.runs, TEACHER, LABEL)
    elif args.command == "support":
        job = support(out, run_dir, args.sample, args.seed)
    else:
        job = apology(out)
    asyncio.run(job)
    print(f"done: {out}")


if __name__ == "__main__":
    main()
