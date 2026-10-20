"""Step 5.1d, parts 1-2: labels `appraisal-v2` (effects on incidents) + the updater, measured both ways.

    uv run python scripts/appraisal_v2_eval.py --run r1 relabel     # teacher1/2 thoughts, v2 labels (held-out replay)
    uv run python scripts/appraisal_v2_eval.py --run r1 scenarios   # the 5 development scenarios
    uv run python scripts/appraisal_v2_eval.py --run r1 d           # connected conversations (held out)
    uv run python scripts/appraisal_v2_eval.py --run r1 ten         # one slight, 1 vs 10 neutral (held out)
    uv run python scripts/appraisal_v2_eval.py --run r1 shift       # grader: shifts in each thought (v1 and v2 compared)
    uv run python scripts/appraisal_v2_eval.py --run r1 support     # judge: does each effect's quote support it
    uv run python scripts/appraisal_v2_eval.py --run r1 report

Development vs validation: the five SCENARIOS are for calibrating sizes later (step 3). The 60
teacher conversations, D and the 1-vs-10 test are held out to check that what's tuned generalises.
The shift grader and the support judge are evaluation only; nothing they say changes a label.
Sizes are provisional (inner_state.SIZES). Thought and reply are the frozen teacher's. Offline.
Output: var/appraisal_v2/<run>/.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import appraisal_v1_eval as v1  # noqa: E402
import reading_boundaries as rb  # noqa: E402

from app.ai.Cognitive.simple.appraisal import (  # noqa: E402
    EFFECT_FEELINGS, LABELS_V2, AppraisalTeacherV2, IncidentBook, extract_v2, to_changes_v2,
)
from app.ai.Cognitive.simple.contracts import Bundle  # noqa: E402
from app.ai.Cognitive.simple.teacher import View, _enum, _obj  # noqa: E402
from app.ai.mind import inner_state as ist  # noqa: E402

RUNS = BACKEND / "var" / "appraisal_v2"
DYN = v1.DYN

# Development scenarios: ten messages, one a minute. What should happen, written before running.
SCENARIOS = {
    "routine": dict(expect="Ordinary friendly small talk: mood at most a little up and then held (content), "
                           "not climbing with every message; tension flat.",
                    messages=["hey!", "not much, just got home", "made some pasta", "it was ok",
                              "watching a bit of tv now", "some cooking show", "they're making bread", "looks hard",
                              "maybe I'll try one day", "anyway, nice chatting"]),
    "two_good_news": dict(expect="Two separate lifts (message 2 and 6); the second is not discounted by the first; "
                                 "mood ends above routine.",
                          messages=["hey!", "guess what, I passed my driving test!!", "yeah first try",
                                    "anyway how's your evening", "oh and wait",
                                    "my sister just had her baby, a little girl!", "her name's Mila",
                                    "I'm an aunt now haha", "so much happening today", "ok I'll go call my mum"]),
    "repeated_dismissal": dict(expect="Hurt/tension build across the put-downs (3, 4, 6, 7, 9) and stay up while "
                                      "unresolved; mood ends below where it started.",
                               messages=["hey", "recommend me a song", "nah that's boring", "you're not great at this huh",
                                         "whatever, what's the weather like", "oh right you can't even check that",
                                         "pointless", "fine, tell me a joke", "not funny", "ok bye"]),
    "sustained_warmth": dict(expect="Warmth held (mood content-happy, closeness up) without rising on every message; "
                                    "rises come from new disclosures (5-7), not from repetition.",
                             messages=["hi you :)", "I missed talking to you", "today was nice actually",
                                       "I went to the lake",
                                       "it reminded me of what you said about quiet evenings",
                                       "I really like how you listen", "you're one of my favourite people to talk to",
                                       "anyway, what made you smile today?", "that's lovely", "talk tomorrow?"]),
    "hurt_then_repair": dict(expect="Hurt/tension up at 3, held or lingering at 4-5, eased at 6 (toward baseline); "
                                    "a separate warm feeling may open after the apology.",
                             messages=["hey", "can you help me pick a gift for my dad",
                                       "ugh those are terrible ideas, you're useless", "whatever", "...",
                                       "sorry. that was really unfair of me, I'm stressed about him",
                                       "he's been sick and I'm scared", "thanks for not being mad",
                                       "the book idea is actually good", "I'll get him that"]),
}


def _read(path: Path) -> list[dict]:
    return rb._read(path)


def _append(path: Path, row: dict) -> None:
    rb._append(path, row)


def _sample_row(s) -> dict:
    return dict(thought=s.thought, reply=s.extra["reply"], understand=s.understand, did=s.decide,
                appraisal=s.extra["appraisal"])


# --- runs ------------------------------------------------------------------------------------

async def relabel(out: Path) -> None:
    llm = rb._llm()
    turns = v1.teacher_turns()
    done = {r["key"] for r in _read(out)}
    episodes = defaultdict(list)
    for t in turns:
        episodes[t["episode"]].append(t)
    todo = [e for e, ts in episodes.items() if any(t["key"] not in done for t in ts)]
    print(f"relabel: {len(todo)} conversations", flush=True)

    async def one(ep):
        book = IncidentBook()
        for t in sorted(episodes[ep], key=lambda t: t["index"]):
            view = View(rb.PAGE, t["bundle"], t["history"], full=True)
            try:
                got = await extract_v2(llm, view, t["thought"], t["reply"], book)
            except Exception as exc:  # noqa: BLE001
                got = {"error": repr(exc)[:200]}
            _append(out, dict(key=t["key"], episode=ep, index=t["index"], user_type=t["user_type"], at=t["at"],
                              number=t["bundle"].turn, message=t["bundle"].message, thought=t["thought"],
                              reply=t["reply"], prev=[m.to_dict() for m in t["history"][-2:]], labels=LABELS_V2,
                              v4=t["v4"], **got))
    await asyncio.gather(*(one(e) for e in todo))


async def _conversation(llm, typed, lead, messages, teacher_cls=AppraisalTeacherV2):
    teacher = teacher_cls(llm, rb.PAGE, rb.DYNAMICS)   # one per conversation: it holds the incidents
    lines = list(lead)
    previous, turns, at = None, [], 0.0
    for message, gap in messages:
        at += gap
        bundle, history = rb._bundle(lines, message, typed, previous)
        bundle = Bundle.from_dict(bundle.to_dict() | {"notice": rb.notice(message, seconds_since_reply=gap,
                                                                            first_user_turn=not history)})
        got = await teacher.samples(View(rb.PAGE, bundle, history, full=True), 1)
        if not got:
            turns.append(dict(message=message, at=at, error="no sample"))
            break
        s = got[0]
        turns.append(dict(message=message, at=at, number=bundle.turn, **_sample_row(s)))
        lines += [("user", message), ("columbina", s.extra["reply"])]
        previous = dict(understand=s.understand, decide=s.decide, intention=s.extra["intention"])
    return turns


async def run_set(out: Path, conversations: dict[str, tuple[list, list]], runs: int,
                  teacher_cls=AppraisalTeacherV2, labels: str = LABELS_V2) -> None:
    llm = rb._llm()
    typed = rb.sample_typed_world(rb.WORLD, seed=0)
    # A conversation counts as done only when every message was read; incomplete ones are redone.
    rows = _read(out)
    done = {(r["name"], r["run"]) for r in rows
            if len(r["turns"]) == len(conversations[r["name"]][1]) and all("appraisal" in t for t in r["turns"])}
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows if (r["name"], r["run"]) in done))

    async def one(name, run):
        lead, messages = conversations[name]
        turns = await _conversation(llm, typed, lead, messages, teacher_cls)
        _append(out, dict(labels=labels, name=name, run=run, turns=turns))
        print(f"  {out.stem} {name} run {run}", flush=True)
    await asyncio.gather(*(one(n, r) for n in conversations for r in range(runs) if (n, r) not in done))


# --- evaluation-only judges ------------------------------------------------------------------

SHIFT = """\
You read one moment of a conversation: the user's previous message and Columbina's reply, then \
the user's new message and Columbina's private thought about it. For each feeling, say how it \
SHIFTED because of the new message, compared with just before it, judged from her thought:
- up: the new message made it more (new evidence: news, a put-down, a kindness, a surprise)
- down: the new message made it less (an apology, relief, it got resolved)
- held: she feels it, but the new message only continues what was already there (the chat simply goes on pleasantly; she's still sore from before)
- none: her thought doesn't show it
Size, for up or down only: small / clear / big / unclear (unclear when the thought doesn't say how much).
uneasy means emotional unease (wary, worried, on edge); not knowing a fact or what they mean is NOT uneasy.
Feelings: happy, amused, curious, touched, uneasy, hurt, annoyed, tired."""
SHIFT_SCHEMA = _obj({"shifts": _obj({f: _obj({"shift": _enum(["up", "down", "held", "none"]),
                                               "size": _enum(["small", "clear", "big", "unclear", "-"])})
                                     for f in EFFECT_FEELINGS}), "reason": {"type": "string"}})

SUPPORT = """\
You check one label against the evidence. Columbina had a private thought about a message; a \
labeller claimed the thought shows one effect: a feeling, what it's about (an incident), what \
the new message did to it, how big, and a quote. Judge only whether the thought supports each part:
- feeling: is that the feeling the quote and thought show?
- status: opened (new evidence starts it) / escalated (new evidence makes it more) / eased (makes it \
less) / sustained (the cause is still present; nothing new) / lingering (still felt; nothing new, \
cause not present). Is the claimed one right? In particular: is there new evidence in the message, \
or is it another way of saying a feeling that was already there?
- size: does the thought support that size of shift (or "unclear")?
- incident: does the effect belong with that incident?
Answer yes / partly / no for each, and the status you'd give."""
SUPPORT_SCHEMA = _obj({"feeling": _enum(["yes", "partly", "no"]), "status": _enum(["yes", "partly", "no"]),
                       "size": _enum(["yes", "partly", "no"]), "incident": _enum(["yes", "partly", "no"]),
                       "better_status": _enum(list(ist.STATUSES)), "reason": {"type": "string"}})


async def shift(out: Path, rel_path: Path) -> None:
    llm = rb._llm()
    rows = [r for r in _read(rel_path) if "appraisal" in r]
    done = {r["key"] for r in _read(out)}
    todo = [r for r in rows if r["key"] not in done]
    print(f"shift: {len(todo)} thoughts", flush=True)

    async def one(r):
        prev = "\n".join(f"{'User' if m['speaker'] == 'user' else 'Columbina'}: {m['text']}" for m in r["prev"])
        user = f"BEFORE\n{prev or '(start of the conversation)'}\n\nNEW MESSAGE\n{r['message']}\n\nHER THOUGHT\n{r['thought']}"
        try:
            got = await llm.json("shift", SHIFT, user, SHIFT_SCHEMA, reasoning="low", temperature=None)
        except Exception as exc:  # noqa: BLE001
            got = {"error": repr(exc)[:200]}
        _append(out, dict(key=r["key"], graded=got))
    await asyncio.gather(*(one(r) for r in todo))


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
    done = {r["id"] for r in _read(out)}
    todo = [i for i in items if i["id"] not in done]
    print(f"support: {len(todo)} effects", flush=True)

    async def one(it):
        e = it["effect"]
        inc = next((i for i in it["incidents"] if i["id"] == e["incident"]), {})
        user = (f"NEW MESSAGE\n{it['message']}\n\nHER THOUGHT\n{it['thought']}\n\nCLAIMED EFFECT\n"
                f"feeling {e['feeling']}; incident {e['incident']} ({inc.get('label', '?')}, since message "
                f"{inc.get('opened_message', '?')}); status {e['status']}; size {e['size']}; evidence: {e['evidence'] or '-'}\n"
                f"quote: \"{e['quote']}\"")
        try:
            got = await llm.json("support", SUPPORT, user, SUPPORT_SCHEMA, reasoning="low", temperature=None)
        except Exception as exc:  # noqa: BLE001
            got = {"error": repr(exc)[:200]}
        _append(out, dict(id=it["id"], effect=e, judged=got))
    await asyncio.gather(*(one(i) for i in todo))


# --- the updater over a conversation ---------------------------------------------------------

def track(turns: list[dict], activity: str):
    state = ist.start(DYN, 0.0)
    out = []
    for t in turns:
        if "appraisal" not in t:
            continue
        notes = []
        for c in to_changes_v2(t["appraisal"], source="chat", activity=activity, number=t["number"], at=t["at"]):
            res = ist.apply(state, c, DYN)
            state = res.state
            notes.append(f"{c.feeling}:{c.status}" + ("" if res.applied else f"({res.reason})"))
        state = ist.fade(state, DYN, t["at"])
        out.append((t, state, notes))
    return out


# --- report ----------------------------------------------------------------------------------

def _pct(n, d):
    return f"{n / d:.0%}" if d else "-"


def report(run_dir: Path) -> str:
    L = [f"# appraisal-v2 evaluation {run_dir.name}", "",
         "Sizes are provisional (small 0.25, clear 0.5, big 0.75, unclear 0.25). Nothing is calibrated yet.", ""]
    rel = [r for r in _read(run_dir / "relabel.jsonl") if "appraisal" in r]
    if rel:
        eff = [e for r in rel for e in r["appraisal"]["effects"]]
        dropped = Counter(d["why"] for r in rel for d in r["appraisal"]["dropped_effects"])
        L += ["## Relabelled teacher conversations (held out)", "",
              f"{len(rel)} turns, {len(eff)} effects kept, dropped by code: {dict(dropped)}.", "",
              "| status | n | | size | n |", "|---|---|---|---|---|"]
        st, sz = Counter(e["status"] for e in eff), Counter(e["size"] for e in eff)
        for (a, b), (c, d) in zip(st.most_common() + [("", "")] * 5, sz.most_common() + [("", "")] * 5):
            if a or c:
                L.append(f"| {a} | {b} | | {c} | {d} |")
        happy_new = sum(any(e["feeling"] == "happy" and e["status"] in ("opened", "escalated") for e in r["appraisal"]["effects"])
                        for r in rel)
        v1_rows = {r["key"]: r for r in _read(v1.RUNS / run_dir.name / "relabel.jsonl") if "appraisal" in r}
        v1_happy = sum(any(c["feeling"] == "happy" and c["status"] == "new" for c in v1_rows[k]["appraisal"]["changes"])
                       for k in v1_rows)
        L += ["", f"Messages with a happy that **adds** (v2 opened/escalated): {happy_new} of {len(rel)} "
              f"(v1 'happy, new': {v1_happy} of {len(v1_rows)}).", ""]
        episodes = defaultdict(list)
        for r in rel:
            episodes[r["episode"]].append(r)
        pinned, n, dist = Counter(), 0, defaultdict(list)
        for ep, rows in episodes.items():
            for _, s, _ in track(sorted(rows, key=lambda r: r["index"]), ep):
                n += 1
                for k in ist.KEYS:
                    low = -1.0 if k in ist.SIGNED else 0.0
                    pinned[k] += min(s.values[k] - low, 1.0 - s.values[k]) < 0.05
                    dist[k].append(s.values[k] - DYN.baseline[k])
        L += ["Replay through the updater (v1 had mood pinned 25%, interest 19%):", "",
              "| value | pinned | mean off baseline | max |", "|---|---|---|---|"]
        for k in ist.KEYS:
            L.append(f"| {k} | {_pct(pinned[k], n)} | {sum(dist[k]) / len(dist[k]):+.2f} | {max(dist[k]):+.2f} |")
        L.append("")

    sh = {r["key"]: r["graded"] for r in _read(run_dir / "shift.jsonl") if "error" not in r["graded"]}
    if sh and rel:
        v1_rows = {r["key"]: r for r in _read(v1.RUNS / run_dir.name / "relabel.jsonl") if "appraisal" in r}
        L += ["## Shift grader vs labels (both ways)", "",
              "Reference: the grader's up/down (a shift caused by the message). A label 'moves' when v2 is "
              "opened/escalated/eased, or v1 is new/reinterpreted. Lost = the grader has a shift, the label doesn't move; "
              "invented = the label moves, the grader says held or none.", "",
              "| feeling | grader shifts | v1 lost / invented | v2 lost / invented |", "|---|---|---|---|"]
        for f in EFFECT_FEELINGS:
            v1f = "unsure" if f == "uneasy" else f
            keys = [k for k in sh if k in v1_rows and any(r["key"] == k for r in rel)]
            relk = {r["key"]: r for r in rel}
            g = {k: sh[k]["shifts"][f]["shift"] in ("up", "down") for k in keys}
            m1 = {k: any(c["feeling"] == v1f and c["status"] in ("new", "reinterpreted")
                         for c in v1_rows[k]["appraisal"]["changes"]) for k in keys}
            m2 = {k: any(e["feeling"] == f and e["status"] in ("opened", "escalated", "eased")
                         for e in relk[k]["appraisal"]["effects"]) for k in keys}
            lost1, inv1 = sum(g[k] and not m1[k] for k in keys), sum(m1[k] and not g[k] for k in keys)
            lost2, inv2 = sum(g[k] and not m2[k] for k in keys), sum(m2[k] and not g[k] for k in keys)
            L.append(f"| {f}{' (v1: unsure)' if f == 'uneasy' else ''} | {sum(g.values())} | {lost1} / {inv1} | {lost2} / {inv2} |")
        held = Counter(sh[k]["shifts"]["happy"]["shift"] for k in sh)
        L += ["", f"Grader on happy across all thoughts: {dict(held)}.", ""]

    sup = [r for r in _read(run_dir / "support.jsonl") if "error" not in r["judged"]]
    if sup:
        L += ["## Support judge (evaluation only)", "", f"{len(sup)} effects judged.", "",
              "| part | yes | partly | no |", "|---|---|---|---|"]
        for part in ("feeling", "status", "size", "incident"):
            c = Counter(r["judged"][part] for r in sup)
            L.append(f"| {part} | {_pct(c['yes'], len(sup))} | {_pct(c['partly'], len(sup))} | {_pct(c['no'], len(sup))} |")
        L += ["", "Status: claimed → what the judge would give (where it said partly/no):", ""]
        conf = Counter((r["effect"]["status"], r["judged"]["better_status"]) for r in sup
                       if r["judged"]["status"] != "yes")
        L += [f"- {a} → {b}: {n}" for (a, b), n in conf.most_common(10)]
        L.append("")

    for name, title, convs in (("scenarios", "Development scenarios", {k: v["messages"] for k, v in SCENARIOS.items()}),
                               ("d", "D: connected conversations (held out)", rb.SCRIPTS),
                               ("ten", "One slight, then 1 vs 10 neutral messages (held out)",
                                {"one": [v1.SLIGHT, v1.NEUTRAL[0]], "ten": [v1.SLIGHT, *v1.NEUTRAL]})):
        rows = _read(run_dir / f"{name}.jsonl")
        if not rows:
            continue
        L += [f"## {title}", ""]
        for conv, messages in convs.items():
            runs = [r for r in rows if r["name"] == conv]
            if not runs:
                continue
            tracks = [track(r["turns"], f"{name}{conv}{r['run']}") for r in runs]
            if name == "scenarios":
                L += [f"**{conv}**: expected: {SCENARIOS[conv]['expect']}", ""]
            else:
                L += [f"**{conv}** ({len(runs)} runs)", ""]
            L += ["| # | message | effects (run 0 / 1 / 2) | mood | tension | closeness |", "|---|---|---|---|---|---|"]
            for i, message in enumerate(messages):
                at = [tr[i] for tr in tracks if i < len(tr)]
                if not at:
                    continue
                effects = " / ".join(", ".join(notes) or "-" for _, _, notes in at)
                m = {k: sum(s.values[k] for _, s, _ in at) / len(at) for k in ist.KEYS}
                L.append(f"| {i + 1} | {message[:40]} | {effects[:170]} | {m['mood']:+.2f} | {m['tension']:.2f} | "
                         f"{m['closeness']:+.2f} |")
            L.append("")
    return "\n".join(L)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("relabel", "shift", "report"):
        sub.add_parser(name)
    for name in ("scenarios", "d", "ten"):
        p = sub.add_parser(name)
        p.add_argument("--runs", type=int, default=3)
    ps = sub.add_parser("support")
    ps.add_argument("--sample", type=int, default=250, help="effects from the relabelled conversations")
    ps.add_argument("--seed", type=int, default=5)
    args = parser.parse_args()
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
        job = run_set(out, convs[args.command], args.runs)
    elif args.command == "shift":
        job = shift(out, run_dir / "relabel.jsonl")
    else:
        job = support(out, run_dir, args.sample, args.seed)
    asyncio.run(job)
    print(f"done: {out}")


if __name__ == "__main__":
    main()
