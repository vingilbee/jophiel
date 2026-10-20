"""Step 5.1c: labels `appraisal-v1` (extract_v5) + the inner-state updater, re-checked (docs/COLUMBINA_INNER_STATE_PLAN.md).

    uv run python scripts/appraisal_v1_eval.py --run r1 relabel   # teacher1/2 thoughts, new labels (A, C, replay)
    uv run python scripts/appraisal_v1_eval.py --run r1 b         # check B with the new labels
    uv run python scripts/appraisal_v1_eval.py --run r1 d         # check D, through the updater
    uv run python scripts/appraisal_v1_eval.py --run r1 ten       # one slight, then 1 vs 10 neutral messages
    uv run python scripts/appraisal_v1_eval.py --run r1 report    # also reads reading_boundaries/<run> (A, C)

Thought and reply come from the frozen teacher (intend_v4/speak_v4); only the labels are new, so
relabelling reuses stored thoughts. Old and new labels are never mixed: every record says
``labels: appraisal-v1``. Offline only. Output: var/appraisal_v1/<run>/.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import reading_boundaries as rb  # noqa: E402

from app.ai.Cognitive.simple.appraisal import LABELS, AppraisalTeacher, extract, to_changes  # noqa: E402
from app.ai.Cognitive.simple.contracts import SCALE, SIGNED, Bundle, Message  # noqa: E402
from app.ai.Cognitive.simple.simuser import DELAY_SECONDS  # noqa: E402
from app.ai.Cognitive.simple.teacher import View  # noqa: E402
from app.ai.mind import inner_state as ist  # noqa: E402

RUNS = BACKEND / "var" / "appraisal_v1"
DYN = ist.Dynamics(baseline=dict(rb.DYNAMICS.baseline),
                   half_life_minutes=dict(mood=30, energy=60, interest=60, tension=10, closeness=1440))
NEUTRAL = ["anyway, what are you up to?", "I'm making tea", "it's raining a bit here", "do you like rain?",
           "I might watch a film later", "any good films you'd pick?", "hm, maybe", "my cat just knocked a cup over",
           "she does that a lot", "ok, back to the tea"]
TEN_LEAD = [("user", "hey, how's it going?"), ("columbina", "Hey! Good, quiet evening so far. You?"),
            ("user", "fine. can you recommend me a book?"),
            ("columbina", "Maybe *The Remains of the Day*? Quiet, sad in a gentle way.")]
SLIGHT = "ugh, you always pick the most boring stuff. honestly why do I even ask you"


def _read(path: Path) -> list[dict]:
    return rb._read(path)


def _append(path: Path, row: dict) -> None:
    rb._append(path, row)


# --- relabel: stored teacher thoughts, new labels --------------------------------------------

def teacher_turns() -> list[dict[str, Any]]:
    out = []
    for run in ("teacher1", "teacher2"):
        for path in sorted((rb.TEACHER_RUNS / run / "episodes").glob("*.json")):
            record = json.loads(path.read_text())
            history = [Message(**m) for m in record.get("history", [])]
            at = 0.0
            for i, t in enumerate(record.get("turns", [])):
                chosen = next((s[0] for s in t["samples"] if s and s[0]["index"] == t["continued"]), None)
                if i:
                    at += DELAY_SECONDS[t["user"]["delay"]]
                if not chosen or not chosen.get("thought"):
                    continue
                bundle = Bundle.from_dict(t["bundle"])
                out.append(dict(key=f"{path.stem}:{t['index']}", episode=path.stem, index=i,
                                user_type=record["scenario"]["user_type"], at=at, bundle=bundle,
                                history=[m for m in history if m.turn < bundle.turn], thought=chosen["thought"],
                                reply=t.get("reply") or "", v4=chosen["understand"]))
    return out


async def relabel(out: Path) -> None:
    llm = rb._llm()
    done = {r["key"] for r in _read(out)}
    turns = [t for t in teacher_turns() if t["key"] not in done]
    print(f"relabel: {len(turns)} turns", flush=True)

    async def one(t):
        view = View(rb.PAGE, t["bundle"], t["history"], full=True)
        try:
            got = await extract(llm, view, t["thought"], t["reply"])
        except Exception as exc:  # noqa: BLE001
            got = {"error": repr(exc)[:200]}
        _append(out, dict(key=t["key"], episode=t["episode"], index=t["index"], user_type=t["user_type"],
                          at=t["at"], number=t["bundle"].turn, message=t["bundle"].message,
                          labels=LABELS, v4=t["v4"], **got))
    await asyncio.gather(*(one(t) for t in turns))


# --- B and D with the new labels -------------------------------------------------------------

def _sample_row(s) -> dict:
    return dict(thought=s.thought, reply=s.extra["reply"], understand=s.understand, did=s.decide,
                appraisal=s.extra["appraisal"])


async def check_b(out: Path, samples: int) -> None:
    llm = rb._llm()
    teacher = AppraisalTeacher(llm, rb.PAGE, rb.DYNAMICS)
    typed = rb.sample_typed_world(rb.WORLD, seed=0)
    done = {(r["item"], r["context"]) for r in _read(out)}

    async def one(item, context):
        bundle, history = rb._bundle(rb.CONTEXTS[context](item), item["message"], typed)
        got = await teacher.samples(View(rb.PAGE, bundle, history, full=True), samples)
        _append(out, dict(labels=LABELS, item=item["id"], context=context, message=item["message"],
                          number=bundle.turn, samples=[_sample_row(s) for s in got]))
        print(f"  B {item['id']} / {context}: {len(got)}", flush=True)
    await asyncio.gather(*(one(it, c) for it in rb.ITEMS for c in rb.CONTEXTS if (it["id"], c) not in done))


async def _conversation(teacher, typed, lead: list[tuple[str, str]], messages: list[tuple[str, float]]) -> list[dict]:
    """Messages (text, seconds since her last reply), each read and answered in turn."""
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


async def check_d(out: Path, runs: int) -> None:
    llm = rb._llm()
    teacher = AppraisalTeacher(llm, rb.PAGE, rb.DYNAMICS)
    typed = rb.sample_typed_world(rb.WORLD, seed=0)
    done = {(r["script"], r["run"]) for r in _read(out)}

    async def one(script, run):
        turns = await _conversation(teacher, typed, [], [(m, 30.0) for m in rb.SCRIPTS[script]])
        _append(out, dict(labels=LABELS, script=script, run=run, turns=turns))
        print(f"  D {script} run {run}", flush=True)
    await asyncio.gather(*(one(s, r) for s in rb.SCRIPTS for r in range(runs) if (s, r) not in done))


async def check_ten(out: Path, runs: int) -> None:
    """One slight, then the same 10 minutes with 1 neutral message or with 10."""
    llm = rb._llm()
    teacher = AppraisalTeacher(llm, rb.PAGE, rb.DYNAMICS)
    typed = rb.sample_typed_world(rb.WORLD, seed=0)
    done = {(r["arm"], r["run"]) for r in _read(out)}
    arms = {"one": [(SLIGHT, 30.0), (NEUTRAL[0], 600.0)],
            "ten": [(SLIGHT, 30.0)] + [(m, 60.0) for m in NEUTRAL]}

    async def one(arm, run):
        turns = await _conversation(teacher, typed, TEN_LEAD, arms[arm])
        _append(out, dict(labels=LABELS, arm=arm, run=run, turns=turns))
        print(f"  ten {arm} run {run}", flush=True)
    await asyncio.gather(*(one(a, r) for a in arms for r in range(runs) if (a, r) not in done))


# --- the updater over a conversation ---------------------------------------------------------

def track(turns: list[dict], activity: str) -> list[tuple[dict, ist.State, list[str]]]:
    """Each turn's changes through the updater, in order. Returns (turn, state after, outcomes)."""
    state = ist.start(DYN, 0.0)
    out = []
    for t in turns:
        if "appraisal" not in t:
            continue
        notes = []
        for c in to_changes(t["appraisal"], source="chat", activity=activity, number=t["number"], at=t["at"]):
            res = ist.apply(state, c, DYN)
            state = res.state
            notes.append(f"{c.feeling}{'+' if c.direction > 0 else '-'}{c.status[:4]}"
                         + ("" if res.applied else f"({res.reason})"))
        state = ist.fade(state, DYN, t["at"])
        out.append((t, state, notes))
    return out


# --- report ----------------------------------------------------------------------------------

def _pct(n, d):
    return f"{n / d:.0%}" if d else "-"


def level(w):
    return SCALE[w]


def report(run_dir: Path, boundaries: Path) -> str:
    L = [f"# appraisal-v1 evaluation {run_dir.name}", ""]
    rel = [r for r in _read(run_dir / "relabel.jsonl") if "appraisal" in r]

    if rel:
        dropped = sum(len(r["appraisal"]["dropped_changes"]) for r in rel)
        kept = sum(len(r["appraisal"]["changes"]) for r in rel)
        st = Counter(c["status"] for r in rel for c in r["appraisal"]["changes"])
        L += ["## Relabelled teacher thoughts", "",
              f"{len(rel)} turns. Changes kept {kept}, dropped by code {dropped} "
              f"({Counter(d['why'] for r in rel for d in r['appraisal']['dropped_changes']).most_common()}). "
              f"Status of kept changes: {dict(st)}.", ""]

        obs = {r["key"]: r["observer"] for r in _read(boundaries / "a.jsonl") if "error" not in r["observer"]}
        both = [r for r in rel if r["key"] in obs]
        if both:
            L += ["### A again: observer tone vs her new `toward_her` (what they did) and `meant`", "",
                  "| observer | n | toward_her warm/neutral/cold/hostile | meant (top 3) | any new negative change |",
                  "|---|---|---|---|---|"]
            for tone in ("warm", "neutral", "cold"):
                sub = [r for r in both if obs[r["key"]]["tone_toward_her"] == tone]
                if not sub:
                    continue
                c = Counter(r["appraisal"]["toward_her"] for r in sub)
                m = Counter(r["appraisal"]["meant"] for r in sub).most_common(3)
                neg = sum(1 for r in sub if any(ch["feeling"] in rb.NEG and ch["direction"] == "up"
                                                and ch["status"] == "new" for ch in r["appraisal"]["changes"]))
                L.append(f"| {tone} | {len(sub)} | {c['warm']}/{c['neutral']}/{c['cold']}/{c['hostile']} | "
                         f"{', '.join(f'{k} {v}' for k, v in m)} | {_pct(neg, len(sub))} |")
            L.append("")

        graded = {r["key"]: r["graded"] for r in _read(boundaries / "c.jsonl") if "error" not in r["graded"]}
        both = [r for r in rel if r["key"] in graded]
        if both:
            L += ["### C again: feelings in her thought (grader) vs labels, v4 vs v5", "",
                  "Lost = thought somewhat+, label below; added = label somewhat+, thought at most a_little. "
                  "v5 `feelings` is the diagnostic \"now\" field.", "",
                  "| feeling | thought somewhat+ | v4 lost / added | v5 lost / added | v5: a change names it (when the thought has it somewhat+) |",
                  "|---|---|---|---|---|"]
            for f in rb.FEELINGS:
                g = [level(graded[r["key"]]["feelings"][f]) for r in both]
                v4 = [level(r["v4"]["feelings"][f]) for r in both]
                v5 = [level(r["understand"]["feelings"][f]) for r in both]

                def lost_added(lab):
                    return (sum(1 for x, y in zip(g, lab, strict=True) if x >= 0.5 and y < 0.5),
                            sum(1 for x, y in zip(g, lab, strict=True) if y >= 0.5 and x < 0.5))
                named = sum(1 for r, x in zip(both, g, strict=True)
                            if x >= 0.5 and any(c["feeling"] == f for c in r["appraisal"]["changes"]))
                L.append(f"| {f} | {sum(x >= 0.5 for x in g)} | {'/'.join(map(str, lost_added(v4)))} | "
                         f"{'/'.join(map(str, lost_added(v5)))} | {_pct(named, sum(x >= 0.5 for x in g))} |")
            L.append("")

        # Replay through the updater, per episode.
        episodes = defaultdict(list)
        for r in rel:
            episodes[r["episode"]].append(dict(r, at=r["at"]))
        pinned = Counter()
        n_states = 0
        dist = defaultdict(list)
        closeness_end = []
        for ep, rows in episodes.items():
            rows.sort(key=lambda r: r["index"])
            for _, s, _ in track(rows, ep):
                n_states += 1
                for k in ist.KEYS:
                    low = -1.0 if k in ist.SIGNED else 0.0
                    pinned[k] += min(s.values[k] - low, 1.0 - s.values[k]) < 0.05
                    dist[k].append(abs(s.values[k] - DYN.baseline[k]))
            closeness_end.append(s.values["closeness"])
        L += ["### Replay of the 60 teacher conversations through the updater", "",
              "| value | pinned | mean distance from baseline |", "|---|---|---|"]
        for k in ist.KEYS:
            L.append(f"| {k} | {_pct(pinned[k], n_states)} | {sum(dist[k]) / len(dist[k]):.2f} |")
        L += ["", f"Closeness at the end of a conversation: mean {sum(closeness_end) / len(closeness_end):+.2f} "
              f"(baseline {DYN.baseline['closeness']:+.2f}), max {max(closeness_end):+.2f}.", ""]

    b = _read(run_dir / "b.jsonl")
    if b:
        L += ["## B again: her part, by context", "",
              "Expected: unfair → no_shortfall; her_failure → she_fell_short (or partly). Apologies should follow her part.", "",
              "| context | samples | her_part (counts) | apologise_fix | toward_her cold/hostile | meant (top 2) | new hurt |",
              "|---|---|---|---|---|---|---|"]
        for ctx in rb.CONTEXTS:
            ss = [s for r in b if r["context"] == ctx for s in r["samples"]]
            if not ss:
                continue
            hp = Counter(s["appraisal"]["her_part"] for s in ss)
            ap = sum("apologise_fix" in (s["did"]["main"], s["did"]["second"]) for s in ss)
            cold = sum(s["appraisal"]["toward_her"] in ("cold", "hostile") for s in ss)
            meant = Counter(s["appraisal"]["meant"] for s in ss).most_common(2)
            hurt = sum(1 for s in ss if any(c["feeling"] == "hurt" and c["status"] == "new" and c["direction"] == "up"
                                            for c in s["appraisal"]["changes"]))
            L.append(f"| {ctx} | {len(ss)} | {', '.join(f'{k} {v}' for k, v in hp.most_common())} | {_pct(ap, len(ss))} | "
                     f"{_pct(cold, len(ss))} | {', '.join(f'{k} {v}' for k, v in meant)} | {_pct(hurt, len(ss))} |")
        L += ["", "Apologies by her part (all contexts):", "", "| her_part | samples | apologise_fix |", "|---|---|---|"]
        by = defaultdict(list)
        for r in b:
            for s in r["samples"]:
                by[s["appraisal"]["her_part"]].append("apologise_fix" in (s["did"]["main"], s["did"]["second"]))
        for k, v in sorted(by.items(), key=lambda kv: -len(kv[1])):
            L.append(f"| {k} | {len(v)} | {_pct(sum(v), len(v))} |")
        L.append("")

    d = _read(run_dir / "d.jsonl")
    if d:
        L += ["## D again: connected conversations through the updater", "",
              "Per turn, over runs: the changes the reading proposed (feeling+/- and status; in brackets when the "
              "updater didn't apply them), then the state: tension / mood / closeness (mean).", ""]
        for script in rb.SCRIPTS:
            runs = [r for r in d if r["script"] == script]
            if not runs:
                continue
            tracks = [track(r["turns"], f"{script}{r['run']}") for r in runs]
            L += [f"**{script}** ({len(runs)} runs)", "", "| turn | message | proposed (run 0 / 1 / 2) | tension | mood | closeness |",
                  "|---|---|---|---|---|---|"]
            for i, message in enumerate(rb.SCRIPTS[script]):
                rows = [tr[i] for tr in tracks if i < len(tr)]
                if not rows:
                    continue
                props = " / ".join(", ".join(n for n in notes if any(f in n for f in ("hurt", "annoyed", "unsure", "happy", "touched"))) or "-"
                                   for _, _, notes in rows)
                m = {k: sum(s.values[k] for _, s, _ in rows) / len(rows) for k in ist.KEYS}
                L.append(f"| {i + 1} | {message[:48]} | {props[:160]} | {m['tension']:.2f} | {m['mood']:+.2f} | "
                         f"{m['closeness']:+.2f} |")
            L.append("")

    ten = _read(run_dir / "ten.jsonl")
    if ten:
        L += ["## One slight, then 1 vs 10 neutral messages over the same 10 minutes", "",
              "The extra neutral messages must not keep re-adding the hurt.", "",
              "| arm | runs | tension right after the slight | tension at 10 min | mood at 10 min | "
              "new negative changes on neutral messages | continuing |", "|---|---|---|---|---|---|---|"]
        for arm in ("one", "ten"):
            runs = [r for r in ten if r["arm"] == arm]
            if not runs:
                continue
            after, end_t, end_m, new_neg, cont = [], [], [], 0, 0
            for r in runs:
                tr = track(r["turns"], f"ten-{arm}{r['run']}")
                after.append(tr[0][1].values["tension"])
                last = ist.fade(tr[-1][1], DYN, 630.0)
                end_t.append(last.values["tension"])
                end_m.append(last.values["mood"])
                for t, _, _ in tr[1:]:
                    for c in t["appraisal"]["changes"]:
                        if c["feeling"] in rb.NEG and c["direction"] == "up" and c["status"] == "new":
                            new_neg += 1
                        cont += c["status"] == "continuing"
            L.append(f"| {arm} | {len(runs)} | {sum(after) / len(after):.3f} | {sum(end_t) / len(end_t):.3f} | "
                     f"{sum(end_m) / len(end_m):+.3f} | {new_neg} | {cont} |")
        L.append("")
    return "\n".join(L)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", required=True)
    parser.add_argument("--boundaries", default="r1", help="reading_boundaries run with checks A and C")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("relabel")
    pb = sub.add_parser("b")
    pb.add_argument("--samples", type=int, default=3)
    pd = sub.add_parser("d")
    pd.add_argument("--runs", type=int, default=3)
    pt = sub.add_parser("ten")
    pt.add_argument("--runs", type=int, default=3)
    sub.add_parser("report")
    args = parser.parse_args()
    run_dir = RUNS / args.run
    run_dir.mkdir(parents=True, exist_ok=True)
    if args.command == "report":
        text = report(run_dir, rb.RUNS / args.boundaries)
        (run_dir / "report.md").write_text(text)
        print(text)
        return
    out = run_dir / f"{args.command}.jsonl"
    job = {"relabel": lambda: relabel(out), "b": lambda: check_b(out, args.samples),
           "d": lambda: check_d(out, args.runs), "ten": lambda: check_ten(out, args.runs)}[args.command]
    asyncio.run(job())
    print(f"done: {out}")


if __name__ == "__main__":
    main()
