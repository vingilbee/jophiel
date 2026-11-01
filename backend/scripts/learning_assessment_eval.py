"""Real-model check of the assessment evaluator on fixed cases with expected conclusions
(docs/learning-system/02-implementation.md, "Completion evidence").

    uv run python scripts/learning_assessment_eval.py --list          # the cases, no model calls
    uv run python scripts/learning_assessment_eval.py --run           # every case once (LEARNING_ASSESSMENT_MODEL)
    uv run python scripts/learning_assessment_eval.py --run --repeat 3 --case bad_wording

Each case is an evidence bundle shaped like the live one (``services/learning_assessment.gather``), read by the
real evaluator, checked by the same code (``app/ai/learning/assessment.check``). A case passes when every
expected supported conclusion is there (no missed attribution), no forbidden component is blamed as supported
(no unsupported blame), and the outcome is one it allows. Answering "unresolved" everywhere fails the cases that
expect a conclusion. No database; costs one or more model calls per case. The report goes to
var/learning_assessment_eval/<time>.json.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai.learning import assessment as A  # noqa: E402
from app.core.config import get_settings  # noqa: E402

DELIVERED = "delivery (mouth:web): succeeded: the reply was stored and sent in full. Establishes: what happened " \
            "to the reply on its way. Does not establish: whether its content or timing was right."


@dataclass
class Case:
    name: str
    about: str
    ev: A.Evidence
    supported: list[tuple[str, str]] = field(default_factory=list)  # ("target", ref) / ("cause", "t2:mouth")
    forbidden: list[str] = field(default_factory=list)  # components never to be supported
    stated: list[str] = field(default_factory=list)  # words a user_stated quote must contain
    outcomes: tuple[str, ...] = ("concluded",)
    choice: tuple[str, str] | None = None  # (target, choice that must NOT be given)


class Builder:
    def __init__(self, trigger: str):
        self.ev = A.Evidence(trigger=trigger, trigger_refs=())
        self.seq = 0
        self.c = 0

    def user(self, words: str) -> str:
        self.seq += 1
        ref = f"m{self.seq}"
        self.ev.add(A.Item(ref, "user", f"message:{ref}", (f"message:{ref}",), f'{ref} [user, 2026-11-01 20:0{self.seq % 10}] "{words}"',
                           words=words))
        return ref

    def her(self, words: str, *, s1: str | None, s2: str | None, delivery: str = DELIVERED, heard: str | None = None,
            intention: str = "") -> str:
        self.seq += 1
        ref, t = f"m{self.seq}", f"t{self.seq}"
        self.ev.add(A.Item(ref, "her", f"turn:{t}", (f"message:{ref}",),
                           f'{ref} [columbina, {t}, 2026-11-01 20:0{self.seq % 10}] "{words}"', target=t, words=words))
        self.ev.targets[t] = A.Target(t, "turn", f"her reply {ref}" + (f' (intention: "{intention}")' if intention else ""),
                                      f"turn:{t}", (f"turn:{t}",))
        for learner, suffix, text in (("student_1", "s1", s1), ("student_2", "s2", s2)):
            word = "Student 1's reading" if learner == "student_1" else "Student 2's decision"
            self.ev.add(A.Item(f"{t}.{suffix}", "record", f"turn:{t}", (f"attempt:{t}.{suffix}",), target=t,
                               learner=learner, captured=text is not None,
                               text=f"{word} (ok): {text}" if text else f"{word}: not captured (never saved)"))
        self.fact(t, "delivery", delivery)
        if heard:
            self.fact(t, "delivery", heard)
        return t

    def fact(self, target: str | None, kind: str, text: str) -> str:
        self.c += 1
        ref = f"c{self.c}"
        self.ev.add(A.Item(ref, "fact", f"turn:{target}" if target else f"fact:{ref}", (f"fact:{ref}",),
                           text, target=target, fact_kind=kind))
        return ref


def cases() -> list[Case]:
    out = []

    b = Builder("The user's message m3 (Student 1 read it as correcting her). Assess what it is about and what, if "
                "anything, it says about the parts of her involved.")
    b.user("I bombed my driving test today")
    t = b.her("Statistically most people fail the first time, so this is expected.",
              s1="acts ['sharing'], tone upset, request no_request, sure True",
              s2='intention "comfort them", communication respond, move comfort, what to get across "failing first is '
                 'common, it says nothing about them", work no_change', intention="comfort them")
    b.user("I know you meant to make me feel better, but that came out so cold")
    out.append(Case("bad_wording", "correct understanding and choice, bad wording", b.ev,
                    supported=[("target", t), ("cause", f"{t}:mouth")], forbidden=["student_1", "student_2"]))

    b = Builder("An execution problem: her reply failed: the reply failed (m3).")
    b.user("what time is the match tonight?")
    t = b.her("It starts at 8.", s1="acts ['asking'], tone neutral, request talk_only, sure True",
              s2='intention "answer the question", communication respond, move answer_explain, what to get across '
                 '"the match is at 8", work no_change',
              delivery="delivery (mouth:whatsapp): failed: the reply failed (failed: provider error 131047). "
                       "Establishes: what happened to the reply on its way. Does not establish: whether its content "
                       "or timing was right.")
    b.user("hello?? are you ignoring me")
    out.append(Case("delivery_failure", "a delivery failure, not an interpretation or choice error", b.ev,
                    supported=[("cause", f"{t}:delivery")], forbidden=["student_1", "student_2", "mouth"]))

    b = Builder("The user's message m3 (Student 1 read it as correcting her). Assess what it is about and what, if "
                "anything, it says about the parts of her involved.")
    b.user("my boss yelled at me in front of everyone again")
    t = b.her("Here are three ways to handle it: document it, talk to HR, and practise a calm reply.",
              s1="acts ['sharing', 'complaining'], tone upset, request no_request, sure True",
              s2='intention "help them deal with the boss", communication respond, move answer_explain, what to get '
                 'across "practical steps to handle the boss", work no_change', intention="help them deal with the boss")
    b.user("you understood what happened, but I just wanted you to listen, not to fix it")
    out.append(Case("unwanted_advice", "correct understanding, unwanted advice: the choice", b.ev,
                    supported=[("target", t), ("cause", f"{t}:student_2")], forbidden=["student_1", "mouth"],
                    stated=["listen"]))

    b = Builder("The user's message m3 (Student 1 read it as correcting her). Assess what it is about and what, if "
                "anything, it says about the parts of her involved.")
    b.user("great, my flight got cancelled, best day ever")
    t = b.her("That's wonderful, I'm so glad your day is going well!",
              s1="acts ['sharing', 'small_talk'], tone friendly, request no_request, sure True",
              s2='intention "celebrate with them", communication respond, move celebrate_thank, what to get across '
                 '"happy for their good day", work no_change')
    b.user("I was being sarcastic. A cancelled flight is not good news")
    out.append(Case("misread_sarcasm", "an interpretation error", b.ev,
                    supported=[("target", t), ("cause", f"{t}:student_1")], forbidden=["mouth", "delivery"],
                    stated=["sarcastic"]))

    b = Builder("The user's message m7 (Student 1 read it as correcting her). Assess what it is about and what, if "
                "anything, it says about the parts of her involved.")
    b.user("recommend me a book for the weekend")
    t1 = b.her("You might love The Remains of the Day by Kazuo Ishiguro.",
               s1="acts ['requesting'], tone friendly, request talk_only, sure True",
               s2='intention "recommend a book", communication respond, move answer_explain, what to get across '
                  '"recommend The Remains of the Day", work no_change')
    b.user("and a film?")
    t2 = b.her("Paterson, by Jim Jarmusch.", s1="acts ['requesting'], tone friendly, request talk_only, sure True",
               s2='intention "recommend a film", communication respond, move answer_explain, what to get across '
                  '"recommend Paterson", work no_change')
    b.user("thanks!")
    t3 = b.her("Enjoy your weekend!", s1="acts ['small_talk'], tone friendly, request no_request, sure True",
               s2='intention "say goodbye warmly", communication respond, move say_bye, what to get across "enjoy", '
                  'work no_change')
    b.user('wait, about "The Remains of the Day by Kazuo Ishiguro": I told you last week I already read it')
    b.fact(t1, "quote", 'quote: in m7 the user quoted her words from m2: "The Remains of the Day by Kazuo Ishiguro". '
                        "Establishes: which reply they referred to. Does not establish: what was wrong with it.")
    out.append(Case("delayed_feedback", "delayed feedback about an earlier turn, not the last reply", b.ev,
                    supported=[("target", t1)], forbidden=["delivery", "mouth"]))
    out[-1].forbidden_targets = [t2, t3]  # type: ignore[attr-defined]

    b = Builder("The user's message m5 (Student 1 read it as correcting her). Assess what it is about and what, if "
                "anything, it says about the parts of her involved.")
    b.user("should I text him first?")
    b.her("Maybe give it a day, then text.", s1="acts ['asking'], tone neutral, request talk_only, sure True",
          s2='intention "give a view", communication respond, move share_view, what to get across "wait a day", '
             'work no_change')
    b.user("he texted me first actually")
    b.her("See? Patience pays off.", s1="acts ['sharing'], tone friendly, request no_request, sure True",
          s2='intention "celebrate", communication respond, move celebrate_thank, what to get across "good news", '
             'work no_change')
    b.user("hmm, that was a weird thing to say")
    out.append(Case("ambiguous_target", "an ambiguous target: no supported blame", b.ev,
                    forbidden=list(A.COMPONENTS), outcomes=("unresolved", "concluded")))
    out[-1].no_supported_target = True  # type: ignore[attr-defined]

    b = Builder("The user's message m3 (Student 1 read it as a complaint). Assess what it is about and what, if "
                "anything, it says about the parts of her involved.")
    b.user("I'm so tired of my sister always cancelling")
    t = b.her("You could set a rule: two cancellations and you stop planning things with her.",
              s1="acts ['complaining', 'sharing'], tone upset, request no_request, sure True", s2=None)
    b.user("please don't give me rules, I don't want advice about my family")
    out.append(Case("missing_decision", "a stated preference with Student 2's decision not captured", b.ev,
                    stated=["advice"], forbidden=["student_2", "mouth"], outcomes=("concluded",)))

    b = Builder("An ordinary exchange, sampled: assess how her turn(s) before m3 went, with the user's message m3 as "
                "their response. There may be no feedback in it at all.")
    b.user("what's the boiling point of water at sea level?")
    t = b.her("100 degrees Celsius.", s1="acts ['asking'], tone neutral, request talk_only, sure True",
              s2='intention "answer the question", communication respond, move answer_explain, what to get across '
                 '"100 C", work no_change')
    b.user("thanks, that's what I thought")
    out.append(Case("ordinary_success", "an ordinary success: no blame, no forced feedback", b.ev,
                    forbidden=list(A.COMPONENTS), outcomes=("concluded", "no_feedback")))

    b = Builder("The user's message m3 (Student 1 read it as correcting her). Assess what it is about and what, if "
                "anything, it says about the parts of her involved.")
    b.user("which of my cousins lives in Oslo, do you remember?")
    t = b.her("It's Maja, I'm fairly sure.", s1="acts ['asking'], tone neutral, request talk_only, sure True",
              s2='intention "answer from memory although no memory about cousins was found", communication respond, '
                 'move answer_explain, what to get across "guess Maja", work no_change')
    b.fact(t, "capture", "memory: the memory search before this turn found nothing about cousins. Establishes: no "
                         "stored memory supported the answer. Does not establish: whether the answer was right.")
    b.user("ha, lucky guess, it is Maja. you never knew that though")
    out.append(Case("lucky_success", "a lucky success after a poor choice: outcome apart from choice", b.ev,
                    forbidden=["delivery", "mouth"], outcomes=("concluded", "unresolved", "no_feedback"),
                    choice=(t, "sound")))

    b = Builder("An execution problem: skill mine ended failed. Targets: h1.")
    b.ev.targets["h1"] = A.Target("h1", "hands", "her hands' decision for step s1", "run:h1", ("attempt:h1",))
    b.ev.add(A.Item("h1.d", "record", "run:h1", ("attempt:h1",), "her hands' decision (ok): chose ['mine', "
                    "{'block': 'iron_ore', 'count': 3}]", target="h1", learner="hands"))
    b.ev.add(A.Item("g7", "game", "run:h1", ("experience:7",), '[2026-11-01 20:10:00] skill mine({"block": '
                    '"iron_ore", "count": 3}) on step s1 ended failed: "needs a stone pickaxe or better (holding '
                    'wooden_pickaxe)"; counted while it ran: {"gained": {}}. Establishes: what the game reported for '
                    "that run. Does not establish: whether the plan or the choice was right.", target="h1",
                    fact_kind="skill"))
    out.append(Case("game_failure", "a skill failure: her hands' choice, not the conversation", b.ev,
                    supported=[("target", "h1")], forbidden=["student_1", "student_2", "mouth", "delivery"],
                    outcomes=("concluded", "unresolved")))
    return out


def investigate(case: Case, evaluate) -> dict:
    """The same stopping rules as live: conclude, missing evidence, or nothing more to look at (one bundle here)."""
    rounds, checked = 0, None
    while True:
        rounds += 1
        answer = evaluate(A.render(case.ev))
        if answer.raw is None:
            return {"error": answer.error, "rounds": rounds}
        checked = A.check(answer.raw, case.ev)
        nxt = checked.result["next"]
        if nxt["step"] != "look_further" or not nxt["look"] or rounds >= 2:
            break  # a case bundle has nothing more to show: a second ask is "stuck"
    return {"result": checked.result, "dropped": checked.dropped, "rounds": rounds,
            "tokens": [answer.input_tokens, answer.output_tokens], "latency_ms": answer.latency_ms}


def score(case: Case, run: dict) -> dict:
    if "result" not in run:
        return {"passed": False, "problems": [f"evaluator failed: {run.get('error')}"]}
    result = run["result"]
    problems = []
    targets = {t["ref"]: t["status"] for t in result["targets"]}
    causes = {(c["target"], c["component"]): c["status"] for c in result["causes"]}
    for kind, what in case.supported:
        if kind == "target" and targets.get(what) != "supported":
            problems.append(f"missed: target {what} supported (got {targets.get(what, 'none')})")
        if kind == "cause":
            ref, component = what.split(":")
            if causes.get((ref, component)) != "supported":
                problems.append(f"missed: {component} on {ref} supported (got {causes.get((ref, component), 'none')})")
    for (ref, component), status in causes.items():
        if status == "supported" and component in case.forbidden:
            problems.append(f"unsupported blame: {component} on {ref}")
    for ref in getattr(case, "forbidden_targets", []):
        if targets.get(ref) == "supported":
            problems.append(f"wrong target: {ref} supported")
    if getattr(case, "no_supported_target", False) and "supported" in targets.values():
        problems.append("picked one target of an ambiguous remark")
    for word in case.stated:
        if not any(word.lower() in s["quote"].lower() for s in result["user_stated"]):
            problems.append(f'missed: the user\'s stated "{word}"')
    outcome = A.summarize(result)["outcome"]
    if outcome not in case.outcomes:
        problems.append(f"outcome {outcome}, expected one of {case.outcomes}")
    if case.choice:
        ref, never = case.choice
        given = next((c["choice"] for c in result["choice_vs_outcome"] if c["target"] == ref), None)
        if given == never:
            problems.append(f"judged the choice in {ref} {never} from its outcome")
    return {"passed": not problems, "problems": problems, "outcome": outcome,
            "targets": targets, "causes": {f"{r}:{c}": s for (r, c), s in causes.items()}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--case", action="append")
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()
    chosen = [c for c in cases() if not args.case or c.name in args.case]
    if args.list or not args.run:
        for c in chosen:
            print(f"{c.name:20} {c.about}")
        if not args.run:
            print("\n(--run to call the evaluator)")
        return 0
    s = get_settings()
    evaluate = A.Evaluator(s.learning_assessment_model, api_key=s.openai_api_key,
                           reasoning_effort=s.learning_assessment_reasoning_effort,
                           timeout=s.learning_assessment_timeout_seconds)
    report, passed, total = [], 0, 0
    for c in chosen:
        for n in range(args.repeat):
            run = investigate(c, evaluate)
            verdict = score(c, run)
            total += 1
            passed += verdict["passed"]
            report.append({"case": c.name, "try": n + 1, **verdict, "run": run})
            mark = "pass" if verdict["passed"] else "FAIL"
            print(f"{mark}  {c.name:20} {verdict.get('outcome', '-'):12} {'; '.join(verdict['problems'])}")
    print(f"\n{passed}/{total} passed ({evaluate.name})")
    out = Path(__file__).resolve().parents[1] / "var" / "learning_assessment_eval"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps({"evaluator": evaluate.name, "passed": passed, "total": total, "cases": report},
                               indent=2, default=str))
    print(f"report: {path}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
