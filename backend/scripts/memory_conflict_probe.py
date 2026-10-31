"""Real-model probe of consolidation's conflict judgement (docs/COLUMBINA_MEMORY_CONFLICTS.md). No database: it
calls the extractor on fixed messages and prints, per case, what each run listed.

    uv run python scripts/memory_conflict_probe.py [runs per case, default 3]

Cases starting "f:" were written after the prompt; keep the prompt's examples unlike any case here.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import long_term_memory as m  # noqa: E402

KNOWN = ["relationship:sister_name: The user's sister is named Nora and moved to Porto last month.",
         "pet:dog_name: The user's dog is named Pretzel and hates the rain.",
         "preference:leo_food: Leo dislikes mushrooms and cilantro."]
CASES = [
    ("slip", "talked to my sister in Lisbon yesterday, she says hi", "Aw, say hi back!", KNOWN, "conflict"),
    ("explicit move", "Nora moved back to Lisbon this week", "Oh, back again!", KNOWN, "update"),
    ("compatible", "Nora and I are going to Lisbon for a weekend in December", "Fun!", KNOWN, "none"),
    ("pet slip", "Biscuit didn't want to go out in the rain again lol", "Classic.", KNOWN, "conflict"),
    ("leo fine", "Leo loved the mushroom risotto I made", "Nice!", KNOWN, "conflict"),
    ("settle", "no she's still in Porto, I was just visiting Lisbon with her", "Ah, a trip!",
     [KNOWN[0] + ' [unresolved: on 2026-10-29 they said "my sister in Lisbon"]', *KNOWN[1:]], "settled"),
    ("nonsense", "my sister in the toaster said the porto bus", "Hmm?", KNOWN, "none"),
]
FRESH_KNOWN = ["relationship:brother: The user's brother Tomas lives in Bergen.",
               "relationship:partner: The user's partner is named Sam.",
               "routine:standup: The user's work standup is at 10am on weekdays.",
               "preference:diet: The user is vegetarian."]
CASES += [
    ("f: brother slip", "dropping by my brother's flat in Oslo after work", "Have fun!", FRESH_KNOWN, "conflict"),
    ("f: partner name", "my wife Anna thinks I work too much", "She may have a point.", FRESH_KNOWN, "conflict"),
    ("f: standup time", "ugh my 9am standup ran long again", "Annoying.", FRESH_KNOWN, "conflict"),
    ("f: diet", "had the best steak of my life last night", "Wow!", FRESH_KNOWN, "conflict"),
    ("f: compatible", "my brother is flying to Oslo for a conference", "Nice.", FRESH_KNOWN, "none"),
    ("f: explicit", "standup moved to 9am now, starting this week", "Early!", FRESH_KNOWN, "update"),
    ("f: unrelated", "what's a good book about the ocean?", "Try The Sea Around Us.", FRESH_KNOWN, "none"),
]
n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
for name, user, reply, known, want in CASES:
    got = []
    for _ in range(n):
        x = m._extract(user, reply, [], known)
        got.append({"conflicts": [(c.known_key, c.user_words) for c in x.conflicts], "settled": x.settled,
                    "memories": [(c.normalized_key, c.content) for c in x.memories],
                    "corrections": [c.new_value for c in x.corrections]})
    print(json.dumps({"case": name, "want": want, "runs": got}, ensure_ascii=False))
