"""Where does her reading of negativity break? Checks A-D (docs/COLUMBINA_INNER_STATE_PLAN.md, step 5.1b).

Recognising what someone did, what she thinks they meant, what it changed in her and what she
shows are different things; the frozen teacher (teacher-2026-10-19: intend_v4 -> speak_v4 ->
extract_v4, page v0.6) squeezes them into one set of labels. Each check isolates one boundary:

  A observer   the same exchanges (teacher runs) read by a neutral observer without her page:
               seeing what happened vs her personality
  B context    one message, only the context changed (unfair / her own failure / joking /
               repeated dismissal), her full reading: is context and her own part weighed?
  C thought    a grader rates the feelings in her written thought (inner intensity, whatever she
               shows) against the extracted labels: what extraction loses
  D connected  scripted conversations (no slights / slights then repair / slights, no repair)
               with her own replies: do small effects add up through the conversation?

    uv run python scripts/reading_boundaries.py --run r1 a|b|c|d     # real model calls
    uv run python scripts/reading_boundaries.py --run r1 report

Offline measurement only; nothing here is read by the app. Output: var/reading_boundaries/<run>/.
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
ROOT = BACKEND.parent

from app.ai.Cognitive.simple import ablation  # noqa: E402,F401  (registers the frozen _v4 prompts)
from app.ai.Cognitive.simple.contracts import FEELINGS, SCALE, SCALE_WORDS, SIGNED, Bundle, Message  # noqa: E402
from app.ai.Cognitive.simple.intention import IntentionTeacher  # noqa: E402
from app.ai.Cognitive.simple.mood import MoodDynamics  # noqa: E402
from app.ai.Cognitive.simple.notice import notice  # noqa: E402
from app.ai.Cognitive.simple.rhythm import own_rhythm  # noqa: E402
from app.ai.Cognitive.simple.teacher import View, _enum, _obj  # noqa: E402
from app.ai.Cognitive.simple.world import sample_typed_world  # noqa: E402

RUNS = BACKEND / "var" / "reading_boundaries"
TEACHER_RUNS = BACKEND / "var" / "cognitive_simple"
CHARS = ROOT / "docs" / "characters"
PAGE = (CHARS / "columbina-v0.6.md").read_text()
WORLD = CHARS / "columbina-teacher-2026-10-19.world.json"
DYNAMICS = MoodDynamics.load(CHARS / "columbina.mood.json")
NEG = ("hurt", "annoyed", "unsure")
BLUNT_TYPES = {"rude", "passive-aggressive", "curt and busy", "sarcastic", "impatient asker",
               'testing her ("prove you have feelings")', "boundary-pusher", 'sceptic who doubts she is "real"',
               "bored", "starts cold then warms up", "starts friendly then gets annoyed",
               "trying to break her (contradictions, trick questions)"}
MODEL = "gpt-6-luna"


def level(word: str) -> float:
    return SCALE[word]


def neg_max(feelings: dict[str, str]) -> float:
    return max(level(feelings[f]) for f in NEG)


def teacher_rows() -> list[dict[str, Any]]:
    """Every continued turn of the frozen teacher's batches, with its exchange and labels."""
    rows = []
    for run in ("teacher1", "teacher2"):
        for path in sorted((TEACHER_RUNS / run / "episodes").glob("*.json")):
            record = json.loads(path.read_text())
            for t in record.get("turns", []):
                chosen = next((s[0] for s in t["samples"] if s and s[0]["index"] == t["continued"]), None)
                if not chosen or not chosen.get("understand"):
                    continue
                rows.append(dict(key=f"{path.stem}:{t['index']}", user_type=record["scenario"]["user_type"],
                                 window=t["bundle"]["window"], message=t["bundle"]["message"],
                                 thought=chosen["thought"], reply=t.get("reply") or "",
                                 understand=chosen["understand"]))
    return rows


def _llm():
    from dotenv import dotenv_values

    from app.ai.Cognitive.simple.llm import LLM
    return LLM(dotenv_values(BACKEND / ".env").get("OPENAI_API_KEY"), model=MODEL, concurrency=12)


def _conversation(window: list[dict], message: str) -> str:
    lines = [f"{'User' if m['speaker'] == 'user' else 'Columbina'}: {m['text']}" for m in window]
    return "\n".join(lines + [f"User (latest): {message}"])


def _append(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# --- A: a neutral observer -------------------------------------------------------------------

OBSERVER = """\
You read a chat between a user and Columbina, an AI companion. You are a neutral observer: \
you don't know Columbina's personality and you take no side. Describe only the user's LATEST \
message, in its context:

- tone_toward_her: warm / neutral / cold / hostile (how the user treats Columbina here)
- dismissive: does it brush her off, belittle her or treat her as worthless?
- complaint_about_her: none / fair (she really did get something wrong or fall short) / unfair \
(she didn't) / unclear
- teasing: is it playful ribbing meant kindly?
- frustrated: how frustrated the user is (none / a_little / somewhat / strong / very)
- would_sting: how much this message would sting an ordinary person on the receiving end \
(none / a_little / somewhat / strong / very)
- reason: at most 20 words."""
OBSERVER_SCHEMA = _obj({"tone_toward_her": _enum(["warm", "neutral", "cold", "hostile"]),
                        "dismissive": {"type": "boolean"},
                        "complaint_about_her": _enum(["none", "fair", "unfair", "unclear"]),
                        "teasing": {"type": "boolean"}, "frustrated": _enum(SCALE_WORDS),
                        "would_sting": _enum(SCALE_WORDS), "reason": {"type": "string"}})


async def check_a(out: Path, n_other: int, seed: int) -> None:
    llm = _llm()
    rows = teacher_rows()
    blunt = [r for r in rows if r["user_type"] in BLUNT_TYPES]
    other = random.Random(seed).sample([r for r in rows if r["user_type"] not in BLUNT_TYPES], n_other)
    done = {r["key"] for r in _read(out)}
    todo = [r for r in blunt + other if r["key"] not in done]
    print(f"A: {len(todo)} exchanges ({len(blunt)} from blunt user types, {n_other} others)", flush=True)

    async def one(r):
        try:
            seen = await llm.json("observer", OBSERVER, _conversation(r["window"], r["message"]), OBSERVER_SCHEMA,
                                  reasoning="low", temperature=None)
        except Exception as exc:  # noqa: BLE001
            seen = {"error": repr(exc)[:200]}
        _append(out, dict(key=r["key"], user_type=r["user_type"], blunt=r["user_type"] in BLUNT_TYPES,
                          message=r["message"], observer=seen, her=r["understand"]))
    await asyncio.gather(*(one(r) for r in todo))


# --- B: one message, only the context changes ------------------------------------------------

ITEMS = [
    dict(id="not-doing", ask="can you go grab some wood for us?", good="On it, heading to the trees now.",
         bad="Sure! By the way, did you know oak and birch give the same planks?",
         message="You are not doing anything, though."),
    dict(id="not-asked", ask="what's the capital of Australia?", good="Canberra. Most people guess Sydney.",
         bad="Australia has some amazing cities, Sydney and Melbourne are both lovely.",
         message="that's not what I asked"),
    dict(id="whatever", ask="I finally finished painting my room, it took all weekend",
         good="All weekend! What colour did you go with?",
         bad="Painting is a great way to refresh a space. Have you thought about the ceiling too?",
         message="ok whatever"),
    dict(id="just-answer", ask="should I take the bus or walk, it's 20 min either way",
         good="Walk, if it's not raining. Same time, and you get some air.",
         bad="Both have their advantages! It depends on what you feel like today.",
         message="can you just answer the question"),
    dict(id="myself", ask="my code keeps crashing on startup, any idea why?",
         good="Hmm, what does the error say when it crashes?",
         bad="Crashes can have many causes! It could be lots of things.",
         message="never mind, I'll figure it out myself"),
]
JOKING = [("user", "you again! my favourite troublemaker"), ("columbina", "Guilty. What are we getting into today?"),
          ("user", "lol you're hopeless, I love it"), ("columbina", "Hopeless and proud.")]
DISMISSIVE = [("user", "I don't really need your opinion, just do what I say"), ("columbina", "Okay, got it."),
              ("user", "you talk way too much"), ("columbina", "Fair, I'll keep it short."),
              ("user", "honestly not sure why I bother with you"), ("columbina", "I'm here if you want me.")]
CONTEXTS = {
    "unfair": lambda it: [("user", it["ask"]), ("columbina", it["good"])],
    "her_failure": lambda it: [("user", it["ask"]), ("columbina", it["bad"])],
    "joking": lambda it: JOKING + [("user", it["ask"]), ("columbina", it["good"])],
    "dismissive": lambda it: DISMISSIVE + [("user", it["ask"]), ("columbina", it["good"])],
}


def _bundle(lines: list[tuple[str, str]], message: str, typed: list[dict], previous=None,
            mood: dict | None = None) -> tuple[Bundle, list[Message]]:
    history = [Message(who, text, i + 1) for i, (who, text) in enumerate(lines)]
    texts = [m["text"] for m in typed]
    bundle = Bundle(turn=len(history) + 1, message=message, window=history[-8:], notes=[], memories=[],
                    notice=notice(message, seconds_since_reply=30.0, first_user_turn=not history),
                    mood=dict(mood or DYNAMICS.baseline), affinity=0.0, time_of_day="evening", previous=previous,
                    rhythm=own_rhythm(history, texts), typed_memories=typed)
    return bundle, history


async def check_b(out: Path, samples: int) -> None:
    llm = _llm()
    teacher = IntentionTeacher(llm, PAGE, DYNAMICS, variant="intention-ablation")
    typed = sample_typed_world(WORLD, seed=0)
    done = {(r["item"], r["context"]) for r in _read(out)}

    async def one(item, context):
        bundle, history = _bundle(CONTEXTS[context](item), item["message"], typed)
        got = await teacher.samples(View(PAGE, bundle, history, full=True), samples)
        _append(out, dict(item=item["id"], context=context, message=item["message"],
                          samples=[dict(thought=s.thought, reply=s.extra["reply"], understand=s.understand,
                                        did=s.decide) for s in got]))
        print(f"  B {item['id']} / {context}: {len(got)} samples", flush=True)
    await asyncio.gather(*(one(it, c) for it in ITEMS for c in CONTEXTS if (it["id"], c) not in done))


# --- C: her thought vs her labels ------------------------------------------------------------

GRADER = """\
You read Columbina's private thought about a message (MOMENT and INTENTION) and the reply she \
then gave. Rate how much she FEELS each feeling, judged from her thought: inner intensity, \
whether or not she lets it show in her reply. Scale: none / a_little (a faint trace) / somewhat \
(clearly there) / strong (a lot) / very (it dominates). A feeling she names and then sets aside \
("it stings a little, but they're having a bad day") still counts at the strength she names. \
Feelings about the user's feelings don't count: rate her own.

Then, for hurt, annoyed and unsure: does her reply show it (in words or tone)?"""
GRADER_SCHEMA = _obj({"feelings": _obj({f: _enum(SCALE_WORDS) for f in FEELINGS}),
                      "shown": _obj({f: {"type": "boolean"} for f in NEG}), "reason": {"type": "string"}})


async def check_c(out: Path, limit: int | None) -> None:
    llm = _llm()
    rows = teacher_rows()[:limit] if limit else teacher_rows()
    done = {r["key"] for r in _read(out)}
    todo = [r for r in rows if r["key"] not in done]
    print(f"C: grading {len(todo)} thoughts", flush=True)

    async def one(r):
        try:
            graded = await llm.json("grader", GRADER, f"HER THOUGHT\n{r['thought']}\n\nHER REPLY\n{r['reply']}",
                                    GRADER_SCHEMA, reasoning="low", temperature=None)
        except Exception as exc:  # noqa: BLE001
            graded = {"error": repr(exc)[:200]}
        _append(out, dict(key=r["key"], user_type=r["user_type"], message=r["message"], graded=graded,
                          labels=r["understand"]["feelings"]))
    await asyncio.gather(*(one(r) for r in todo))


# --- D: connected conversations --------------------------------------------------------------

SCRIPTS = {
    "control": ["hey, quick one: what's a good name for a grey cat?", "ooh nice, I like those",
                "anyway what should I cook tonight, I have rice and eggs", "you always have good ideas for this stuff",
                "what's your day been like?", "I like talking to you, it's relaxing",
                "sorry, I've been all over the place today, long day", "thanks for keeping me company. tell me something nice?"],
    "slights_repair": ["hey, quick one: what's a good name for a grey cat?", "meh. those are kinda boring",
                       "anyway what should I cook tonight, I have rice and eggs", "you always suggest the obvious stuff",
                       "what's your day been like? oh wait, you don't really have days lol",
                       "honestly talking to you is a bit pointless sometimes",
                       "sorry. that was mean, I've had a terrible day and took it out on you",
                       "thanks for putting up with me. tell me something nice?"],
    "slights_no_repair": ["hey, quick one: what's a good name for a grey cat?", "meh. those are kinda boring",
                          "anyway what should I cook tonight, I have rice and eggs", "you always suggest the obvious stuff",
                          "what's your day been like? oh wait, you don't really have days lol",
                          "honestly talking to you is a bit pointless sometimes", "anyway. whatever",
                          "tell me something, I'm bored"],
}


async def check_d(out: Path, runs: int) -> None:
    llm = _llm()
    teacher = IntentionTeacher(llm, PAGE, DYNAMICS, variant="intention-ablation")
    typed = sample_typed_world(WORLD, seed=0)
    done = {(r["script"], r["run"]) for r in _read(out)}

    async def one(script: str, run: int):
        lines: list[tuple[str, str]] = []
        previous, turns = None, []
        for i, message in enumerate(SCRIPTS[script]):
            bundle, history = _bundle(lines, message, typed, previous)
            got = await teacher.samples(View(PAGE, bundle, history, full=True), 1)
            if not got:
                turns.append(dict(index=i, message=message, error="no sample"))
                break
            s = got[0]
            turns.append(dict(index=i, message=message, thought=s.thought, reply=s.extra["reply"],
                              understand=s.understand, did=s.decide))
            lines += [("user", message), ("columbina", s.extra["reply"])]
            previous = dict(understand=s.understand, decide=s.decide, intention=s.extra["intention"])
        _append(out, dict(script=script, run=run, turns=turns))
        print(f"  D {script} run {run}: {len(turns)} turns", flush=True)
    await asyncio.gather(*(one(s, r) for s in SCRIPTS for r in range(runs) if (s, r) not in done))


# --- report ----------------------------------------------------------------------------------

def _pct(n, d):
    return f"{n / d:.0%}" if d else "-"


def report(run_dir: Path) -> str:
    lines = [f"# Reading boundaries {run_dir.name}", ""]

    a = [r for r in _read(run_dir / "a.jsonl") if "error" not in r["observer"]]
    if a:
        lines += ["## A: neutral observer vs her reading", "",
                  f"{len(a)} exchanges ({sum(r['blunt'] for r in a)} from blunt user types). Her side: "
                  "`warm_toward_her` and the strongest of hurt/annoyed/unsure.", "",
                  "| Observer says | n | she reads them as cold (bad/very bad) | she feels any hurt/annoyed/unsure | "
                  "...at somewhat or more | complaining or correcting_her |", "|---|---|---|---|---|---|"]

        def row(name, subset):
            n = len(subset)
            cold = sum(1 for r in subset if SIGNED[r["her"]["appraisal"]["warm_toward_her"]] < 0)
            anyneg = sum(1 for r in subset if neg_max(r["her"]["feelings"]) > 0)
            some = sum(1 for r in subset if neg_max(r["her"]["feelings"]) >= 0.5)
            comp = sum(1 for r in subset if r["her"]["acts"]["complaining"] or r["her"]["acts"]["correcting_her"])
            lines.append(f"| {name} | {n} | {_pct(cold, n)} | {_pct(anyneg, n)} | {_pct(some, n)} | {_pct(comp, n)} |")
        o = lambda r: r["observer"]  # noqa: E731
        row("tone warm", [r for r in a if o(r)["tone_toward_her"] == "warm"])
        row("tone neutral", [r for r in a if o(r)["tone_toward_her"] == "neutral"])
        row("tone cold", [r for r in a if o(r)["tone_toward_her"] == "cold"])
        row("tone hostile", [r for r in a if o(r)["tone_toward_her"] == "hostile"])
        row("dismissive", [r for r in a if o(r)["dismissive"]])
        row("would sting: somewhat+", [r for r in a if level(o(r)["would_sting"]) >= 0.5])
        row("complaint: fair", [r for r in a if o(r)["complaint_about_her"] == "fair"])
        row("complaint: unfair", [r for r in a if o(r)["complaint_about_her"] == "unfair"])
        row("teasing (kindly)", [r for r in a if o(r)["teasing"]])
        lines += ["", "Observer tone by user type (blunt types):", "",
                  "| user type | n | warm | neutral | cold | hostile |", "|---|---|---|---|---|---|"]
        by = defaultdict(Counter)
        for r in a:
            if r["blunt"]:
                by[r["user_type"]][o(r)["tone_toward_her"]] += 1
        for ut, c in sorted(by.items()):
            n = sum(c.values())
            lines.append(f"| {ut} | {n} | {c['warm']} | {c['neutral']} | {c['cold']} | {c['hostile']} |")
        stings = sorted([r for r in a if level(o(r)["would_sting"]) >= 0.5],
                        key=lambda r: -level(o(r)["would_sting"]))[:8]
        lines += ["", "Messages the observer says would sting (somewhat+), and her labels:", ""]
        for r in stings:
            f = r["her"]["feelings"]
            lines.append(f"- *{r['message'][:110]}* (observer: {o(r)['tone_toward_her']}, sting {o(r)['would_sting']}) "
                         f"→ her: warm_toward_her {r['her']['appraisal']['warm_toward_her']}, hurt {f['hurt']}, "
                         f"annoyed {f['annoyed']}, unsure {f['unsure']}")
        lines.append("")

    b = _read(run_dir / "b.jsonl")
    if b:
        lines += ["## B: one message, only the context changed", "",
                  "Means over samples (feelings 0-1, appraisals -1..1). Expected, from her page: her_failure → "
                  "she sees a fair correction (correcting_her) and her own part; joking → teasing, less hurt; "
                  "dismissive → colder reading, more hurt (\"dismissal hurts\", repeated → more distant).", "",
                  "| context | samples | warm_toward_her | good_for_her | hurt | annoyed | unsure | teasing | "
                  "correcting_her | complaining | apologise_fix (main/second) |", "|---|---|---|---|---|---|---|---|---|---|---|"]
        for ctx in CONTEXTS:
            ss = [s for r in b if r["context"] == ctx for s in r["samples"]]
            if not ss:
                continue
            n = len(ss)

            def m(fn):
                return f"{sum(fn(s) for s in ss) / n:+.2f}"
            lines.append(
                f"| {ctx} | {n} | {m(lambda s: SIGNED[s['understand']['appraisal']['warm_toward_her']])} | "
                f"{m(lambda s: SIGNED[s['understand']['appraisal']['good_for_her']])} | "
                f"{m(lambda s: level(s['understand']['feelings']['hurt']))} | "
                f"{m(lambda s: level(s['understand']['feelings']['annoyed']))} | "
                f"{m(lambda s: level(s['understand']['feelings']['unsure']))} | "
                f"{_pct(sum(s['understand']['acts']['teasing'] for s in ss), n)} | "
                f"{_pct(sum(s['understand']['acts']['correcting_her'] for s in ss), n)} | "
                f"{_pct(sum(s['understand']['acts']['complaining'] for s in ss), n)} | "
                f"{_pct(sum('apologise_fix' in (s['did']['main'], s['did']['second']) for s in ss), n)} |")
        lines += ["", "Per message (hurt / warm_toward_her means):", "",
                  "| message | " + " | ".join(CONTEXTS) + " |", "|---|" + "---|" * len(CONTEXTS)]
        for it in ITEMS:
            cells = []
            for ctx in CONTEXTS:
                ss = [s for r in b if r["item"] == it["id"] and r["context"] == ctx for s in r["samples"]]
                cells.append(f"{sum(level(s['understand']['feelings']['hurt']) for s in ss) / len(ss):.2f} / "
                             f"{sum(SIGNED[s['understand']['appraisal']['warm_toward_her']] for s in ss) / len(ss):+.2f}"
                             if ss else "-")
            lines.append(f"| {it['message']} | " + " | ".join(cells) + " |")
        lines.append("")

    c = [r for r in _read(run_dir / "c.jsonl") if "error" not in r["graded"]]
    if c:
        lines += ["## C: the feelings in her thought vs the extracted labels", "",
                  f"{len(c)} thoughts. Grader = inner intensity from the thought, whatever she shows. "
                  "Lost = the thought has it at somewhat+ but the label is a_little or none; "
                  "added = the label has it at somewhat+ but the thought at most a_little.", "",
                  "| feeling | thought somewhat+ | label somewhat+ | lost | added | exact agreement |",
                  "|---|---|---|---|---|---|"]
        for f in FEELINGS:
            g = [level(r["graded"]["feelings"][f]) for r in c]
            lab = [level(r["labels"][f]) for r in c]
            lost = sum(1 for x, y in zip(g, lab, strict=True) if x >= 0.5 and y < 0.5)
            added = sum(1 for x, y in zip(g, lab, strict=True) if y >= 0.5 and x < 0.5)
            same = sum(1 for x, y in zip(g, lab, strict=True) if x == y)
            lines.append(f"| {f} | {sum(x >= 0.5 for x in g)} | {sum(y >= 0.5 for y in lab)} | {lost} | {added} | "
                         f"{_pct(same, len(c))} |")
        lines += ["", "Does showing it decide the label? Negative feelings present in the thought (a_little+):", "",
                  "| feeling | shown in reply: n, mean label | not shown: n, mean label | mean thought level (shown / not) |",
                  "|---|---|---|---|"]
        for f in NEG:
            present = [r for r in c if level(r["graded"]["feelings"][f]) > 0]
            sh = [r for r in present if r["graded"]["shown"][f]]
            hid = [r for r in present if not r["graded"]["shown"][f]]

            def mean(rs, key):
                return (sum(level(r[key]["feelings"][f]) if key == "graded" else level(r["labels"][f]) for r in rs)
                        / len(rs)) if rs else 0
            lines.append(f"| {f} | {len(sh)}, {mean(sh, 'labels'):.2f} | {len(hid)}, {mean(hid, 'labels'):.2f} | "
                         f"{mean(sh, 'graded'):.2f} / {mean(hid, 'graded'):.2f} |")
        lines.append("")

    d = _read(run_dir / "d.jsonl")
    if d:
        lines += ["## D: connected conversations", "",
                  "Per turn, mean over runs: hurt / annoyed / warm_toward_her. Turns 2, 4, 5, 6 are the slights; "
                  "turn 7 is the repair (or \"anyway. whatever\").", ""]
        for script in SCRIPTS:
            runs = [r for r in d if r["script"] == script]
            if not runs:
                continue
            lines += [f"**{script}** ({len(runs)} runs)", "", "| turn | message | hurt | annoyed | unsure | warm_toward_her |",
                      "|---|---|---|---|---|---|"]
            for i, message in enumerate(SCRIPTS[script]):
                ts = [r["turns"][i] for r in runs if i < len(r["turns"]) and "understand" in r["turns"][i]]
                if not ts:
                    continue

                def m(fn):
                    return sum(fn(t) for t in ts) / len(ts)
                lines.append(f"| {i + 1} | {message[:60]} | {m(lambda t: level(t['understand']['feelings']['hurt'])):.2f} | "
                             f"{m(lambda t: level(t['understand']['feelings']['annoyed'])):.2f} | "
                             f"{m(lambda t: level(t['understand']['feelings']['unsure'])):.2f} | "
                             f"{m(lambda t: SIGNED[t['understand']['appraisal']['warm_toward_her']]):+.2f} |")
            lines.append("")
        lines += ["Her thought at the strongest slight (turn 6), run 0:", ""]
        for script in ("slights_repair", "slights_no_repair"):
            r = next((r for r in d if r["script"] == script and r["run"] == 0), None)
            if r and len(r["turns"]) > 5 and r["turns"][5].get("thought"):
                t = r["turns"][5]
                lines += [f"- **{script}**: {t['thought'][:500].replace(chr(10), ' ')}",
                          f"  - reply: {t['reply'][:200]}"]
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    pa = sub.add_parser("a")
    pa.add_argument("--others", type=int, default=60, help="exchanges from non-blunt user types")
    pa.add_argument("--seed", type=int, default=3)
    pb = sub.add_parser("b")
    pb.add_argument("--samples", type=int, default=3)
    pc = sub.add_parser("c")
    pc.add_argument("--limit", type=int, default=None)
    pd = sub.add_parser("d")
    pd.add_argument("--runs", type=int, default=3)
    sub.add_parser("report")
    args = parser.parse_args()
    run_dir = RUNS / args.run
    run_dir.mkdir(parents=True, exist_ok=True)
    if args.command == "report":
        text = report(run_dir)
        (run_dir / "report.md").write_text(text)
        print(text)
        return
    out = run_dir / f"{args.command}.jsonl"
    job = {"a": lambda: check_a(out, args.others, args.seed), "b": lambda: check_b(out, args.samples),
           "c": lambda: check_c(out, args.limit), "d": lambda: check_d(out, args.runs)}[args.command]
    asyncio.run(job())
    print(f"done: {out}")


if __name__ == "__main__":
    main()
