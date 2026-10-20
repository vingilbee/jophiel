"""Inner state step 5.1: how would her mood have moved? (docs/COLUMBINA_INNER_STATE_PLAN.md)

Offline replay of mood over conversations, from the teacher's reading of each exchange. Three
tracks from the same readings:
- ``per_turn``: today's teacher rule (simple/mood.update): every reading pulls all five values,
  and each turn drifts a fixed share back to baseline, however long the gap;
- ``time_all``: fades by elapsed time (half-lives), but every reading still pulls all five values;
- ``time_targets``: the 5.2 design: fades by elapsed time, and a reading pulls only the values it
  touches (a neutral message moves nothing).

    # Teacher test conversations already labelled (no model calls):
    uv run python scripts/mood_replay.py --run synth tracks --episodes teacher1 teacher2
    # Real stored conversations: the frozen teacher reads each exchange with her actual reply
    # (read-only on the database; real model calls), then the tracks:
    uv run python scripts/mood_replay.py --run real read
    uv run python scripts/mood_replay.py --run real tracks

Output: var/mood_replay/<run>/ (readings.jsonl, report.md, plots/). Nothing here is read by the app.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
ROOT = BACKEND.parent

from app.ai.Cognitive.simple.contracts import (  # noqa: E402
    APPRAISALS_SIGNED, FEELINGS, MOOD_KEYS, SIGNED_MOOD, scale, signed,
)
from app.ai.Cognitive.simple.live import CHARACTER_DIR  # noqa: E402
from app.ai.Cognitive.simple.mood import START_MOODS, MoodDynamics, band, feeling_pull, update  # noqa: E402
from app.ai.Cognitive.simple.simuser import DELAY_SECONDS  # noqa: E402

RUNS = BACKEND / "var" / "mood_replay"
TEACHER_RUNS = BACKEND / "var" / "cognitive_simple"
DYNAMICS = MoodDynamics.load(CHARACTER_DIR / "columbina.mood.json")
# Minutes for a value to get halfway back to baseline. The doc's starting points; 5.1 shows how
# they behave at half and double speed.
HALF_LIVES = {"proposed": dict(mood=30, energy=60, interest=60, tension=10, closeness=1440)}
HALF_LIVES["fast"] = {k: v / 2 for k, v in HALF_LIVES["proposed"].items()}
HALF_LIVES["slow"] = {k: v * 2 for k, v in HALF_LIVES["proposed"].items()}
MODES = ("per_turn", "time_all", "time_targets")
SOMEWHAT = scale("somewhat")
BOUNDS = {k: ((-1.0, 1.0) if k in SIGNED_MOOD else (0.0, 1.0)) for k in MOOD_KEYS}


# --- the dynamics (pure; what 5.2 would own) --------------------------------------------------

def targets(understand: dict) -> dict[str, float]:
    """The values this reading touches, with where it pulls them. A feeling at "a little" passes
    (the teacher's own scale: "she notices it and it passes"), so it moves nothing."""
    f = {k: scale(understand["feelings"][k]) for k in FEELINGS}
    good = signed(understand["appraisal"][APPRAISALS_SIGNED[0]])
    warm = signed(understand["appraisal"][APPRAISALS_SIGNED[1]])
    pull = feeling_pull(understand)
    touched = {
        "mood": good != 0 or max(f["happy"], f["amused"], f["touched"], f["hurt"], f["annoyed"]) >= SOMEWHAT,
        "energy": f["tired"] >= SOMEWHAT or max(f["happy"], f["amused"]) >= scale("strong"),
        "interest": max(f["curious"], f["touched"]) >= SOMEWHAT,
        "tension": max(f["hurt"], f["annoyed"], f["unsure"]) >= SOMEWHAT,
        "closeness": warm != 0,
    }
    return {k: pull[k] for k in MOOD_KEYS if touched[k]}


def fade(state: dict[str, float], seconds: float, half_lives: dict[str, float]) -> dict[str, float]:
    """Closed form, so checking every second or once an hour gives the same value."""
    base = DYNAMICS.baseline
    return {k: base[k] + (state[k] - base[k]) * 0.5 ** (max(0.0, seconds) / 60 / half_lives[k]) for k in MOOD_KEYS}


def impulse(state: dict[str, float], pulls: dict[str, float]) -> dict[str, float]:
    out = dict(state)
    for k, pull in pulls.items():
        low, high = BOUNDS[k]
        out[k] = max(low, min(high, state[k] + DYNAMICS.react[k] * (pull - state[k])))
    return out


def step(mode: str, state: dict[str, float], understand: dict, seconds: float,
         half_lives: dict[str, float]) -> dict[str, float]:
    if mode == "per_turn":
        return update(state, understand, DYNAMICS)
    state = fade(state, seconds, half_lives)
    return impulse(state, feeling_pull(understand) if mode == "time_all" else targets(understand))


# --- events -----------------------------------------------------------------------------------

@dataclass
class Event:
    at: float                 # seconds since the chain began
    understand: dict
    message: str
    label: str = ""           # episode or conversation


def synthetic_chains(runs: list[str]) -> list[tuple[str, dict, list[Event]]]:
    """Teacher conversations: the continued sample's reading, timed by the simulated user's delays."""
    chains = []
    for run in runs:
        for path in sorted((TEACHER_RUNS / run / "episodes").glob("*.json")):
            record = json.loads(path.read_text())
            events, at = [], 0.0
            for i, turn in enumerate(record.get("turns", [])):
                chosen = next((s[0] for s in turn["samples"] if s and s[0]["index"] == turn["continued"]), None)
                if i:
                    at += DELAY_SECONDS[turn["user"]["delay"]]
                if chosen and chosen.get("understand"):
                    events.append(Event(at, chosen["understand"], turn["user"]["message"], path.stem))
            scenario = record["scenario"]
            label = f"{run}/{path.stem} ({scenario.get('user_type')}, {scenario.get('situation')}, start {scenario['mood']})"
            chains.append((label, dict(START_MOODS[scenario["mood"]]), events))
    return chains


def real_chain(run_dir: Path) -> list[tuple[str, dict, list[Event]]]:
    """All read conversations of one user in time order, as one chain: her mood carries across them."""
    rows = _read(run_dir / "readings.jsonl")
    if not rows:
        return []
    start = min(r["at"] for r in rows)
    events = [Event(r["at"] - start, r["understand"], r["message"], r["conversation"][:8])
              for r in sorted(rows, key=lambda r: r["at"]) if r.get("understand")]
    return [("real conversations (one user, in time order)", dict(DYNAMICS.baseline), events)]


def simulate(start: dict, events: list[Event], mode: str, half_lives: dict[str, float]) -> list[dict[str, float]]:
    state, last, out = dict(start), 0.0, []
    for e in events:
        state = step(mode, state, e.understand, e.at - last, half_lives)
        last = e.at
        out.append(state)
    return out


# --- reading real conversations (the frozen teacher, with her actual reply) -------------------

async def read_real(run_dir: Path, conversations: list[str], model: str, limit: int, mood_input: str) -> None:
    from dotenv import dotenv_values
    from sqlalchemy import select

    from app.ai.Cognitive.simple.contracts import WINDOW, Bundle, Message
    from app.ai.Cognitive.simple.intention import extract_schema
    from app.ai.Cognitive.simple.llm import LLM
    from app.ai.Cognitive.simple.notes import NoteBook, events_from_reply
    from app.ai.Cognitive.simple.notice import notice
    from app.ai.Cognitive.simple.real import _time_of_day, load_conversation
    from app.ai.Cognitive.simple.retrieval import MemoryPool
    from app.ai.Cognitive.simple.rhythm import own_rhythm
    from app.ai.Cognitive.simple.teacher import View, prompt
    from app.ai.Cognitive.simple.world import sample_typed_world
    from app.db.models import ChatMessage, Conversation
    from app.db.session import SessionLocal

    if not conversations:
        with SessionLocal() as db:   # read-only: every conversation with at least one user message
            rows = db.execute(select(Conversation.id, Conversation.created_at)
                              .where(Conversation.id.in_(select(ChatMessage.conversation_id)
                                                         .where(ChatMessage.role == "user")))
                              .order_by(Conversation.created_at)).all()
            conversations = [str(r[0]) for r in rows]
    key = dotenv_values(BACKEND / ".env").get("OPENAI_API_KEY")
    llm = LLM(key, model=model, concurrency=8)
    page = (CHARACTER_DIR / "columbina.md").read_text()
    world = sample_typed_world(CHARACTER_DIR / "columbina.world.json", seed=0)
    her_texts = [w["text"] for w in world]
    out = run_dir / "readings.jsonl"
    done = {(r["conversation"], r["turn"]) for r in _read(out)}
    loaded = []
    for cid in conversations:
        import uuid
        _, turns, items = load_conversation(uuid.UUID(cid))
        if turns:
            loaded.append((cid, turns, items))
    loaded.sort(key=lambda c: c[1][0]["at"])
    state, last_at = dict(DYNAMICS.baseline), None
    for cid, turns, items in loaded:
        pool = MemoryPool(items, llm, "text-embedding-3-small")
        await pool.prepare()
        history: list[Message] = []
        notes = NoteBook()
        count, labelled, last_reply_at, previous = 0, 0, None, None
        for i, item in enumerate(turns):
            if item["role"] != "user" or labelled >= limit:
                continue
            reply_item = turns[i + 1] if i + 1 < len(turns) and turns[i + 1]["role"] == "assistant" else None
            reply = reply_item["text"] if reply_item else ""
            count += 1
            at = item["at"].timestamp()
            # Her mood as 5.2 would hold it: the proposed time track, faded to this message.
            state = fade(state, at - last_at, HALF_LIVES["proposed"]) if last_at is not None else state
            last_at = at
            seconds = (item["at"] - last_reply_at).total_seconds() if last_reply_at else None
            about = await pool.retrieve(item["text"])
            typed = [dict(kind="about_them", text=t) for t in about] + world
            bundle = Bundle(turn=count, message=item["text"], window=history[-WINDOW:], notes=list(notes.notes),
                            memories=about, notice=notice(item["text"], seconds_since_reply=seconds,
                                                          first_user_turn=labelled == 0),
                            # The reading that moves her mood sees her baseline, not her mood: the teacher
                            # echoes a mood it's given back as a feeling (teacher runs: "tired" on 43/45
                            # messages read with low energy, 0/366 otherwise), which would feed itself.
                            mood={k: round(v, 4) for k, v in (state if mood_input == "track"
                                                              else DYNAMICS.baseline).items()}, affinity=0.0,
                            time_of_day=_time_of_day(item["at"].hour), previous=previous,
                            rhythm=own_rhythm(history, her_texts), typed_memories=typed)
            view = View(page, bundle, history, full=True)
            labelled += 1
            row: dict[str, Any] = dict(conversation=cid, turn=count, at=at, message=item["text"], reply=reply,
                                       mood_before=bundle.mood)
            if (cid, count) in done:
                cached = next(r for r in _read(out) if r["conversation"] == cid and r["turn"] == count)
                understand, did = cached.get("understand"), cached.get("did")
            else:
                base = view.inputs()
                note_ids = [n.id for n in bundle.notes]
                try:
                    thought = await llm.text("intend", prompt("intend_v4"), base, reasoning="low", max_tokens=2500)
                    labels = await llm.json(
                        "extract", prompt("extract_v4"),
                        f'{base}\n\nOPEN NOTE IDS: {", ".join(note_ids) or "none (give an empty note_updates list)"}'
                        f"\n\nHER THOUGHT\n{thought}\n\nHER REPLY\n{reply or '(she did not reply)'}",
                        extract_schema(note_ids))
                    understand, did = labels["understand"], labels["did"]
                    seen = {u["note"]: u["update"] for u in understand["note_updates"] if u["note"] in note_ids}
                    understand["note_updates"] = [{"note": n, "update": seen.get(n, "none")} for n in note_ids]
                    row.update(thought=thought, understand=understand, did=did)
                except Exception as exc:  # noqa: BLE001 - a failed reading is a gap, not a stop
                    understand, did = None, None
                    row["error"] = f"{type(exc).__name__}: {exc}"[:300]
                with out.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                print(f"  {cid[:8]} turn {count}: {item['text'][:60]!r}", flush=True)
            if understand:
                state = impulse(state, targets(understand))
                notes.apply_message(turn=count, message=item["text"], understand=understand,
                                    previous_reply=history[-1].text if history and history[-1].speaker == "columbina"
                                    else None)
                previous = dict(understand=understand, decide={k: did[k] for k in ("main", "second", "dials")})
            history.append(Message("user", item["text"], count))
            if reply_item:
                count += 1
                if understand:
                    notes.apply_reply(turn=count, message=item["text"], understand=understand, reply=reply,
                                      events=events_from_reply(reply, did, source="description"))
                history.append(Message("columbina", reply, count))
                last_reply_at = reply_item["at"]
    (run_dir / "usage.json").write_text(json.dumps(llm.usage.to_dict(), indent=1))


# --- tracks, metrics, plots -------------------------------------------------------------------

def metrics(chains: list[tuple[str, dict, list[Event]]], mode: str, half_lives: dict[str, float]) -> dict[str, Any]:
    base = DYNAMICS.baseline
    pinned, distance, moved, neutral_moves = (defaultdict(list) for _ in range(4))
    settle_before, settle_after = defaultdict(list), defaultdict(list)
    for _, start, events in chains:
        states = simulate(start, events, mode, half_lives)
        before = dict(start)
        for i, (e, s) in enumerate(zip(events, states, strict=True)):
            gap = e.at - (events[i - 1].at if i else 0.0)
            for k in MOOD_KEYS:
                low, high = BOUNDS[k]
                pinned[k].append(min(s[k] - low, high - s[k]) < 0.05)
                distance[k].append(abs(s[k] - base[k]))
                moved[k].append(abs(s[k] - before[k]) > 0.02)
                if not targets(e.understand):
                    neutral_moves[k].append(abs(s[k] - before[k]))
                if gap >= 3600 and i:   # a long gap: how far from baseline before and right after it
                    settle_before[k].append(abs(before[k] - base[k]))
                    settle_after[k].append(abs(fade(before, gap, half_lives)[k] - base[k]) if mode != "per_turn"
                                           else abs(update(before, _NEUTRAL, DYNAMICS)[k] - base[k]))
            before = s

    def mean(xs):
        return sum(xs) / len(xs) if xs else None
    return {k: dict(pinned=mean(pinned[k]), distance=mean(distance[k]), moved=mean(moved[k]),
                    neutral_move=mean(neutral_moves[k]), settle_before=mean(settle_before[k]),
                    settle_after=mean(settle_after[k])) for k in MOOD_KEYS}


# A reading with nothing in it: what per_turn does across a gap (it has no notion of time).
_NEUTRAL = {"feelings": {f: "none" for f in FEELINGS},
            "appraisal": {APPRAISALS_SIGNED[0]: "neutral", APPRAISALS_SIGNED[1]: "neutral"}}


def plot(label: str, start: dict, events: list[Event], path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(len(MOOD_KEYS), 1, figsize=(11, 10), sharex=True)
    xs = list(range(1, len(events) + 1))
    styles = {"per_turn": ("#9a9a9a", "--"), "time_all": ("#d08a3a", ":"), "time_targets": ("#2a7f8f", "-")}
    tracks = {m: simulate(start, events, m, HALF_LIVES["proposed"]) for m in MODES}
    for ax, k in zip(axes, MOOD_KEYS, strict=True):
        for m in MODES:
            color, ls = styles[m]
            ax.plot(xs, [s[k] for s in tracks[m]], ls, color=color, label=m, linewidth=1.6)
        ax.axhline(DYNAMICS.baseline[k], color="#cccccc", linewidth=0.8)
        ax.set_ylim(*BOUNDS[k])
        ax.set_ylabel(k)
        for i, e in enumerate(events):
            if i and e.at - events[i - 1].at >= 3600:
                ax.axvline(i + 0.5, color="#e0b0b0", linewidth=0.8)
    axes[0].legend(loc="upper right", fontsize=8)
    axes[0].set_title(label[:110], fontsize=9)
    axes[-1].set_xlabel("message (red lines: a gap of an hour or more)")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=90)
    plt.close(fig)


def _tracks(args) -> None:
    run_dir = RUNS / args.run
    run_dir.mkdir(parents=True, exist_ok=True)
    chains = synthetic_chains(args.episodes) if args.episodes else real_chain(run_dir)
    if not chains:
        raise SystemExit("no readings: run `read` first, or pass --episodes")
    n_events = sum(len(e) for _, _, e in chains)
    untouched = sum(1 for _, _, ev in chains for e in ev if not targets(e.understand))
    lines = [f"# Mood replay {args.run} (inner state step 5.1)", "",
             f"{len(chains)} conversation chains, {n_events} readings; {untouched} ({untouched / n_events:.0%}) "
             "touch no value under `time_targets` (nothing felt at \"somewhat\" or more).", "",
             "Columns: **pinned** = share of messages where a value sits within 0.05 of its limit; "
             "**off baseline** = mean distance from her baseline; **moved** = share of messages that moved it "
             "(> 0.02); **neutral move** = mean change from a reading that touches nothing; "
             "**gap** = distance from baseline before a gap of an hour or more -> after it.", ""]

    def fmt(x, pct=False):
        return "-" if x is None else (f"{x:.0%}" if pct else f"{x:.2f}")

    for set_name in ("proposed",) if not args.all_half_lives else tuple(HALF_LIVES):
        lines += [f"## Half-lives: {set_name} ({', '.join(f'{k} {v:g} min' for k, v in HALF_LIVES[set_name].items())})",
                  ""]
        for mode in MODES:
            if mode == "per_turn" and set_name != "proposed":
                continue
            m = metrics(chains, mode, HALF_LIVES[set_name])
            lines += [f"**{mode}**", "", "| value | pinned | off baseline | moved | neutral move | gap |",
                      "|---|---|---|---|---|---|"]
            for k in MOOD_KEYS:
                r = m[k]
                gap = (f"{fmt(r['settle_before'])} -> {fmt(r['settle_after'])}" if r["settle_before"] is not None
                       else "-")
                lines.append(f"| {k} | {fmt(r['pinned'], True)} | {fmt(r['distance'])} | {fmt(r['moved'], True)} | "
                             f"{fmt(r['neutral_move'])} | {gap} |")
            lines.append("")

    # Which chains to draw: the ones with the most felt events.
    def felt(events):
        return sum(len(targets(e.understand)) for e in events)
    chosen = sorted(chains, key=lambda c: -felt(c[2]))[:args.plots]
    for i, (label, start, events) in enumerate(chosen):
        name = f"plots/{i:02d}.png"
        plot(label, start, events, run_dir / name)
        lines += [f"![{label}]({name})", ""]

    if not args.episodes:   # real: what moved her, message by message
        label, start, events = chains[0]
        states = simulate(start, events, "time_targets", HALF_LIVES["proposed"])
        lines += ["## What moved her (real, `time_targets`, proposed half-lives)", "",
                  "| # | conv | gap | message | felt | moved | after |", "|---|---|---|---|---|---|---|"]
        for i, (e, s) in enumerate(zip(events, states, strict=True)):
            gap = e.at - (events[i - 1].at if i else 0.0)
            felt_words = ", ".join(f"{f} {e.understand['feelings'][f]}" for f in FEELINGS
                                   if scale(e.understand["feelings"][f]) >= SOMEWHAT) or "-"
            after = ", ".join(band(k, s[k]) for k in MOOD_KEYS)
            msg = e.message.replace("|", "/").replace("\n", " ")[:70]
            lines.append(f"| {i + 1} | {e.label} | {_gap(gap)} | {msg} | {felt_words} | "
                         f"{', '.join(targets(e.understand)) or '-'} | {after} |")
    report = run_dir / "report.md"
    report.write_text("\n".join(lines))
    print("\n".join(line for line in lines if not line.startswith("![")))
    print(f"\nreport: {report}")


def _gap(seconds: float) -> str:
    if seconds < 120:
        return f"{seconds:.0f} s"
    if seconds < 7200:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    read = sub.add_parser("read", help="real conversations: the frozen teacher reads each exchange")
    read.add_argument("--conversation", nargs="*", default=[], help="ids (default: every conversation, in time order)")
    read.add_argument("--model", default="gpt-6-luna")
    read.add_argument("--limit", type=int, default=200, help="user turns per conversation")
    read.add_argument("--mood-input", choices=("baseline", "track"), default="baseline",
                      help="the mood the teacher sees while reading (default: her baseline, no echo)")
    tracks = sub.add_parser("tracks")
    tracks.add_argument("--episodes", nargs="*", default=[], help="teacher runs to use instead of real readings")
    tracks.add_argument("--plots", type=int, default=6)
    tracks.add_argument("--all-half-lives", action="store_true", help="also half and double speed")
    args = parser.parse_args()
    if args.command == "read":
        run_dir = RUNS / args.run
        run_dir.mkdir(parents=True, exist_ok=True)
        asyncio.run(read_real(run_dir, args.conversation, args.model, args.limit, args.mood_input))
    else:
        _tracks(args)


if __name__ == "__main__":
    main()
