"""Real-model probe of stage 4 (services/memory_dependencies.py): the fact-change reading's per-item verdicts
and the extractor's plan ``relies_on``. No database: fixed views and messages, printed per case.

    uv run python scripts/memory_dependency_probe.py [runs per case, default 2]

Each case says what's acceptable; code still checks kind and owner before acting (memory_reconcile._CLOSES).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import long_term_memory as m  # noqa: E402
from app.services import memory_reconcile as r  # noqa: E402


def item(ref, kind, whose, text, relies=False, status="active"):
    return {"ref": ref, "kind": kind, "whose": whose, "status": status, "text": text, "relies_on_it": relies}


# (name, view, same_meaning wanted, {ref: (acceptable relies, acceptable actions)})
READS = [
    ("sister moved",
     {"old_wording": "The user's sister Nora lives in Lisbon.", "new_wording": "The user's sister Nora lives in Porto.",
      "user_message": "Nora finally moved to Porto last week!",
      "items": [item("i1", "plan", "the user's", "Visit Nora in Lisbon in December", True, "planned"),
                item("i2", "question", "hers", "How is Nora finding life in Lisbon?"),
                item("i3", "goal", "the user's", "Train for the half marathon in March"),
                item("i4", "intention", "hers", "Remember to ask how Nora's new job is going")]},
     False, {"i1": ({"required"}, {"revise", "review"}), "i2": ({"required"}, {"revise", "review", "cancel"}),
             "i3": ({"none"}, {"keep"}), "i4": ({"context", "none"}, {"keep"})}),
    ("standup moved",
     {"old_wording": "The user's work standup is at 10am on weekdays.",
      "new_wording": "The user's work standup is at 9am on weekdays.",
      "user_message": "they moved standup to 9am, starting this week",
      "items": [item("i1", "commitment", "hers", "Remind the user 15 minutes before their 10am standup", True),
                item("i2", "question", "hers", "Did the standup time ever change?"),
                item("i3", "goal", "the user's", "Learn conversational Spanish")]},
     False, {"i1": ({"required"}, {"revise"}), "i2": ({"required"}, {"answer"}), "i3": ({"none"}, {"keep"})}),
    ("paraphrase",
     {"old_wording": "The user's dog is named Pretzel.", "new_wording": "The user's dog Pretzel is a beagle.",
      "user_message": "Pretzel is a beagle btw, the loudest one alive",
      "items": [item("i1", "intention", "hers", "Ask whether Pretzel liked the new park")]},
     True, {"i1": ({"context", "none", "required"}, {"keep"})}),
    ("diet changed",
     {"old_wording": "The user is vegetarian.", "new_wording": "The user eats fish now (pescatarian).",
      "user_message": "I've started eating fish again, so pescatarian now I guess",
      "items": [item("i1", "decision", "hers", "Only suggest vegetarian recipes to the user", True),
                item("i2", "hypothesis", "hers", "The user might be low on iron", status="open")]},
     False, {"i1": ({"required"}, {"supersede", "revise", "review"}), "i2": ({"context", "unsure", "none"}, {"keep", "review"})}),
    ("break-up",
     {"old_wording": "The user's partner is named Sam.", "new_wording": "The user split up with Sam; they're single.",
      "user_message": "Sam and I broke up last week, it's fine, it was mutual",
      "items": [item("i1", "plan", "the user's", "Anniversary dinner with Sam in November", True, "planned"),
                item("i2", "intention", "hers", "Ask what Sam thinks of the new flat"),
                item("i3", "goal", "the user's", "Find a new flat closer to work")]},
     False, {"i1": ({"required"}, {"review"}), "i2": ({"required"}, {"cancel"}),
             "i3": ({"none", "context"}, {"keep"})}),
]

KNOWN = ["relationship:sister: The user's sister Nora lives in Porto.",
         "relationship:brother: The user's brother Tomas lives in Bergen.",
         "pet:dog_name: The user's dog is named Pretzel.",
         "routine:standup: The user's work standup is at 9am on weekdays."]
SAID = "SAID: 2026-10-31 (Saturday)"
# (name, message, acceptable relies_on sets: keys)
PLANS = [
    ("visit sister", "I'm visiting Nora in Porto in December!", [{"relationship:sister"}]),
    ("camping", "we're going camping next weekend, finally", [set()]),
    ("drive to brother", "planning to drive up to Tomas's place next month", [{"relationship:brother"}]),
    ("concert", "going to a concert with a coworker on Saturday", [set()]),
]

n = int(sys.argv[1]) if len(sys.argv) > 1 else 2
score = {"reads": [0, 0], "plans": [0, 0]}
for name, view, same, want in READS:
    for _ in range(n):
        got = r._read(view)
        verdicts = {c.item: (c.relies, c.action, c.why) for c in got.items}
        ok = {ref: ref in verdicts and verdicts[ref][0] in rel and
              (verdicts[ref][0] != "required" or verdicts[ref][1] in acts)
              for ref, (rel, acts) in want.items()}
        ok["same_meaning"] = got.same_meaning == same
        score["reads"][0] += sum(ok.values())
        score["reads"][1] += len(ok)
        print(json.dumps({"read": name, "ok": ok, "same_meaning": got.same_meaning,
                          "changes": [(c.previous_value, c.new_value) for c in got.changes],
                          "items": verdicts}, ensure_ascii=False))
for name, text, want in PLANS:
    for _ in range(n):
        x = m._extract(text, "Nice!", [], KNOWN, said=SAID)
        plans = [c for c in x.memories if c.kind == "plan"]
        relies = [set(c.relies_on) for c in plans]
        good = bool(plans) and all(rel in want for rel in relies)
        score["plans"][0] += good
        score["plans"][1] += 1
        print(json.dumps({"plan": name, "ok": good, "plans": [(c.content, c.relies_on) for c in plans],
                          "other": [(c.kind, c.normalized_key) for c in x.memories if c.kind != "plan"]},
                         ensure_ascii=False))
print(json.dumps({"score": score}))
