"""Train the students, then evaluate them against the teacher's own agreement.

- Split by scenario family (user group x situation), so whole conversations, their mood replays
  and their kind of situation are held out together.
- Student 1 trains on accepted samples. Student 2 trains on the same samples with its Understand
  inputs half from the teacher and half from Student 1's predictions (mood, flags and allowed
  moves recomputed from those), so it learns to work on imperfect inputs.
- Evaluation: teacher leave-one-out agreement is the ceiling; students are reported as a share of
  it, isolated and connected (students driving mood and notes across the conversation).
"""
from __future__ import annotations

import hashlib
import json
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch
from torch import nn

from ..contracts import (
    ACTS, DIALS, FEELINGS, GROUPS, NO_SECOND, TONES, Bundle, decide_targets, understand_targets,
)
from ..mood import MoodDynamics, update as mood_update
from ..notes import NoteBook
from ..turn import allowed_groups, flags as turn_flags
from .data import (
    RARE_MOVES, Example, context_batch, examples_from, load_episodes, prediction_s1_vector,
    prediction_to_decide, prediction_to_understand, s1_targets, s1_vector, s2_extra, s2_targets,
)
from .encode import Encoder
from .model import StudentOne, StudentTwo

SCALAR_NAMES = ('about_her', 'sure', 'good_for_her', 'warm_toward_her', 'surprising', 'important', *FEELINGS)
PASS_SHARE = 0.8


def _family_bucket(family: str, seed: int) -> float:
    return int(hashlib.sha1(f'{seed}:{family}'.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def split(examples: list[Example], *, test_share: float = 0.25, seed: int = 0) -> tuple[list[Example], list[Example]]:
    families = sorted({e.family for e in examples}, key=lambda f: _family_bucket(f, seed))
    episodes_by_family = defaultdict(set)
    for e in examples:
        episodes_by_family[e.family].add(e.episode)
    total = len({e.episode for e in examples})
    test, count = set(), 0
    for f in families:
        if count >= test_share * total:
            break
        test.add(f)
        count += len(episodes_by_family[f])
    return [e for e in examples if e.family not in test], [e for e in examples if e.family in test]


def _tensor(a, dtype=torch.float32):
    return torch.as_tensor(a, dtype=dtype)


@dataclass
class Inputs:
    slots: torch.Tensor
    numeric: torch.Tensor


def _context(bundles: list[Bundle], encoder: Encoder) -> Inputs:
    b = context_batch(bundles, encoder)
    return Inputs(_tensor(b.slots), _tensor(b.numeric))


def _fit(model: nn.Module, loss_fn, n: int, val_loss_fn, *, epochs: int = 400, lr: float = 1e-3, patience: int = 40,
         batch: int = 64, seed: int = 0) -> dict[str, Any]:
    torch.manual_seed(seed)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-2)
    best, best_state, stale, history = float('inf'), None, 0, []
    rng = np.random.default_rng(seed)
    for epoch in range(epochs):
        model.train()
        order = rng.permutation(n)
        for i in range(0, n, batch):
            idx = torch.as_tensor(order[i:i + batch])
            opt.zero_grad()
            loss = loss_fn(idx)
            loss.backward()
            opt.step()
        model.eval()
        with torch.no_grad():
            val = float(val_loss_fn())
        history.append(val)
        if val < best - 1e-4:
            best, stale = val, 0
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
        else:
            stale += 1
            if stale >= patience:
                break
    if best_state:
        model.load_state_dict(best_state)
    return dict(epochs=len(history), best_val=round(best, 4))


def _weights(examples: list[Example]) -> torch.Tensor:
    return _tensor([e.weight for e in examples])


def s1_loss(model: StudentOne, x: Inputs, y: dict[str, torch.Tensor], w: torch.Tensor, idx=None) -> torch.Tensor:
    if idx is None:
        idx = torch.arange(len(w))
    out = model(x.slots[idx], x.numeric[idx])
    ww = w[idx]
    acts = nn.functional.binary_cross_entropy_with_logits(out['acts'], y['acts'][idx], reduction='none').mean(1)
    tone = nn.functional.cross_entropy(out['tone'], y['tone'][idx], reduction='none')
    request = nn.functional.cross_entropy(out['request'], y['request'][idx], reduction='none')
    scalars = ((out['scalars'] - y['scalars'][idx]) ** 2).mean(1) * 4.0
    notes_t = y['notes'][idx]
    if (notes_t >= 0).any():
        notes = nn.functional.cross_entropy(out['notes'].flatten(0, 1), notes_t.flatten(), ignore_index=-100,
                                            reduction='none').view(notes_t.shape).sum(1) / 3.0
    else:
        notes = torch.zeros_like(acts)
    return ((acts + 0.5 * tone + 0.5 * request + scalars + notes) * ww).sum() / ww.sum()


def s2_loss(model: StudentTwo, x: Inputs, extra: torch.Tensor, allowed: torch.Tensor, y: dict[str, torch.Tensor],
            w: torch.Tensor, idx=None) -> torch.Tensor:
    if idx is None:
        idx = torch.arange(len(w))
    out = model(x.slots[idx], x.numeric[idx], extra[idx], allowed[idx])
    ww = w[idx]
    main = nn.functional.cross_entropy(out['main'], y['main'][idx], reduction='none')
    second = nn.functional.cross_entropy(out['second'], y['second'][idx], reduction='none')
    dials = ((out['dials'] - y['dials'][idx]) ** 2).mean(1) * 4.0
    return ((main + 0.5 * second + dials) * ww).sum() / ww.sum()


def predict_s1(model: StudentOne, x: Inputs) -> dict[str, np.ndarray]:
    model.eval()
    with torch.no_grad():
        out = model(x.slots, x.numeric)
    return dict(acts=torch.sigmoid(out['acts']).numpy(), tone=torch.softmax(out['tone'], 1).numpy(),
                request=torch.softmax(out['request'], 1).numpy(), scalars=out['scalars'].numpy(),
                notes=torch.softmax(out['notes'], -1).numpy())


def predict_s2(model: StudentTwo, x: Inputs, extra: torch.Tensor, allowed: torch.Tensor) -> dict[str, np.ndarray]:
    model.eval()
    with torch.no_grad():
        out = model(x.slots, x.numeric, extra, allowed)
    return dict(main=torch.softmax(out['main'], 1).numpy(), second=torch.softmax(out['second'], 1).numpy(),
                dials=out['dials'].numpy())


def code_stages(bundle: Bundle, understand: dict[str, Any], dynamics: MoodDynamics):
    mood_after = mood_update(bundle.mood, understand, dynamics)
    f = turn_flags(bundle.notice, understand, bundle.notes)
    return mood_after, f, allowed_groups(bundle.notice, understand, f)


def s2_inputs_teacher(examples: list[Example]) -> tuple[torch.Tensor, torch.Tensor]:
    extra = [s2_extra(s1_vector(e.understand), e.mood_after, e.flags, e.allowed) for e in examples]
    allowed = [[float(g in e.allowed) for g in GROUPS] for e in examples]
    return _tensor(extra), _tensor(allowed)


def s2_inputs_predicted(examples: list[Example], pred: dict[str, np.ndarray], dynamics: MoodDynamics,
                        keep_targets: bool) -> tuple[torch.Tensor, torch.Tensor, int]:
    """Student 2 inputs rebuilt from Student 1's predictions; code stages rerun on them.

    With ``keep_targets`` (training) the teacher's chosen moves stay allowed so the loss is finite;
    the count of targets the predicted mask would have blocked is returned.
    """
    extra, allowed, blocked = [], [], 0
    for i, e in enumerate(examples):
        understand = prediction_to_understand(pred, i, e.bundle)
        mood_after, f, ok = code_stages(e.bundle, understand, dynamics)
        moves = {e.decide['main'], e.decide['second']} - {NO_SECOND}
        if not moves <= set(ok):
            blocked += 1
            if keep_targets:
                ok = sorted(set(ok) | moves, key=GROUPS.index)
        extra.append(s2_extra(prediction_s1_vector(pred, i), mood_after, f, ok))
        allowed.append([float(g in ok) for g in GROUPS])
    return _tensor(extra), _tensor(allowed), blocked


def train(run_dir: Path, dynamics: MoodDynamics, *, seed: int = 0, fraction: float = 1.0,
          models_name: str = 'models') -> dict[str, Any]:
    from .review import load_votes
    episodes = load_episodes(run_dir)
    votes = load_votes(run_dir)
    examples = examples_from(episodes, votes)
    train_ex, test_ex = split(examples, seed=seed)
    # Validation for early stopping: a few training episodes.
    eps = sorted({e.episode for e in train_ex})
    random.Random(seed).shuffle(eps)
    val_eps = set(eps[:max(1, len(eps) // 6)])
    fit_eps = [e for e in eps if e not in val_eps]
    keep = set(fit_eps[:max(1, round(len(fit_eps) * fraction))])   # learning curve: fewer training conversations
    fit_ex = [e for e in train_ex if e.episode in keep]
    val_ex = [e for e in train_ex if e.episode in val_eps]
    encoder = Encoder(run_dir / 'embeddings.npz')

    x_fit, x_val = _context([e.bundle for e in fit_ex], encoder), _context([e.bundle for e in val_ex], encoder)
    y_fit = {k: _tensor(v, torch.int64 if v.dtype == np.int64 else torch.float32) for k, v in s1_targets(fit_ex).items()}
    y_val = {k: _tensor(v, torch.int64 if v.dtype == np.int64 else torch.float32) for k, v in s1_targets(val_ex).items()}
    w_fit, w_val = _weights(fit_ex), _weights(val_ex)
    s1 = StudentOne()
    s1_info = _fit(s1, lambda idx: s1_loss(s1, x_fit, y_fit, w_fit, idx), len(fit_ex),
                   lambda: s1_loss(s1, x_val, y_val, w_val), seed=seed)

    # Student 2: half teacher inputs, half Student 1 predictions.
    pred_fit, pred_val = predict_s1(s1, x_fit), predict_s1(s1, x_val)
    t_extra, t_allowed = s2_inputs_teacher(fit_ex)
    p_extra, p_allowed, blocked_fit = s2_inputs_predicted(fit_ex, pred_fit, dynamics, keep_targets=True)
    mix = torch.as_tensor(np.random.default_rng(seed).random(len(fit_ex)) < 0.5).unsqueeze(1)
    extra_fit = torch.where(mix, p_extra, t_extra)
    allowed_fit = torch.where(mix, p_allowed, t_allowed)
    v_extra, v_allowed, _ = s2_inputs_predicted(val_ex, pred_val, dynamics, keep_targets=True)
    z_fit = {k: _tensor(v, torch.int64 if v.dtype == np.int64 else torch.float32) for k, v in s2_targets(fit_ex).items()}
    z_val = {k: _tensor(v, torch.int64 if v.dtype == np.int64 else torch.float32) for k, v in s2_targets(val_ex).items()}
    s2 = StudentTwo()
    s2_info = _fit(s2, lambda idx: s2_loss(s2, x_fit, extra_fit, allowed_fit, z_fit, w_fit, idx), len(fit_ex),
                   lambda: s2_loss(s2, x_val, v_extra, v_allowed, z_val, w_val), seed=seed)

    models = run_dir / models_name
    models.mkdir(exist_ok=True)
    torch.save(s1.state_dict(), models / 'student_one.pt')
    torch.save(s2.state_dict(), models / 'student_two.pt')
    info = dict(examples=len(examples), fraction=fraction, fit_conversations=len(keep), votes_used=len(votes), fit=len(fit_ex), val=len(val_ex), test=len(test_ex),
                test_families=sorted({e.family for e in test_ex}), student_one=s1_info, student_two=s2_info,
                s2_training_targets_blocked_by_predicted_mask=blocked_fit,
                params=dict(student_one=sum(p.numel() for p in s1.parameters()),
                            student_two=sum(p.numel() for p in s2.parameters())))
    (models / 'train.json').write_text(json.dumps(info, indent=1))
    return info


# --- evaluation -------------------------------------------------------------------------------

def _mode_set(values: list) -> set:
    if not values:
        return set()
    counts = Counter(values)
    top = max(counts.values())
    return {v for v, c in counts.items() if c == top}


def _top2(values: list) -> set:
    counts = Counter(values).most_common()
    return {v for v, _ in counts[:2]}


class Tally:
    def __init__(self):
        self.sums: dict[str, float] = defaultdict(float)
        self.counts: dict[str, int] = defaultdict(int)

    def add(self, key: str, value: float) -> None:
        self.sums[key] += value
        self.counts[key] += 1

    def result(self) -> dict[str, dict[str, float]]:
        return {k: dict(value=round(self.sums[k] / self.counts[k], 4), n=self.counts[k]) for k in sorted(self.sums)}


def _turn_groups(examples: list[Example]) -> dict[tuple, list[Example]]:
    groups = defaultdict(list)
    for e in examples:
        groups[(e.episode, e.turn, e.kind)].append(e)
    return groups


def evaluate(run_dir: Path, dynamics: MoodDynamics, *, seed: int = 0, models_name: str = 'models') -> dict[str, Any]:
    episodes = load_episodes(run_dir)
    examples = examples_from(episodes)
    _, test_ex = split(examples, seed=seed)
    encoder = Encoder(run_dir / 'embeddings.npz')
    s1, s2 = StudentOne(), StudentTwo()
    s1.load_state_dict(torch.load(run_dir / models_name / 'student_one.pt'))
    s2.load_state_dict(torch.load(run_dir / models_name / 'student_two.pt'))

    groups = _turn_groups(test_ex)
    keys = list(groups)
    reps = [groups[k][0] for k in keys]         # one bundle per turn (samples share the input)
    x = _context([r.bundle for r in reps], encoder)
    p1 = predict_s1(s1, x)
    p_extra, p_allowed, _ = s2_inputs_predicted(reps, p1, dynamics, keep_targets=False)
    p2 = predict_s2(s2, x, p_extra, p_allowed)

    teacher, student = Tally(), Tally()
    subsets: dict[str, tuple[Tally, Tally]] = defaultdict(lambda: (Tally(), Tally()))
    for gi, key in enumerate(keys):
        samples = groups[key]
        if len(samples) < 2:
            continue
        tgt = [understand_targets(s.understand) for s in samples]
        dec = [decide_targets(s.decide) for s in samples]
        tags = set().union(*(s.tags for s in samples))
        main_pred = p2['main'][gi]
        pred_top = int(main_pred.argmax())
        pred_top2 = set(np.argsort(main_pred)[-2:].tolist())
        tallies = [(teacher, student)] + [subsets[tag] for tag in ('memory_turn', 'has_memory', 'older') if tag in tags]
        for i, s in enumerate(samples):
            others = [j for j in range(len(samples)) if j != i]
            for T, S in tallies:
                # Student 2
                o_main = [dec[j]['main'] for j in others]
                T.add('main_top1', float(dec[i]['main'] in _mode_set(o_main)) / max(1, len(_mode_set(o_main))))
                S.add('main_top1', float(pred_top == dec[i]['main']))
                T.add('main_top2', float(dec[i]['main'] in _top2(o_main)))
                S.add('main_top2', float(dec[i]['main'] in pred_top2))
                o_second = [dec[j]['second'] for j in others]
                T.add('second', float(dec[i]['second'] in _mode_set(o_second)) / max(1, len(_mode_set(o_second))))
                S.add('second', float(int(p2['second'][gi].argmax()) == dec[i]['second']))
                o_dials = np.mean([dec[j]['dials'] for j in others], 0)
                T.add('dials_error', float(np.abs(o_dials - dec[i]['dials']).mean()))
                S.add('dials_error', float(np.abs(p2['dials'][gi] - dec[i]['dials']).mean()))
                if GROUPS[dec[i]['main']] in RARE_MOVES:
                    T.add('rare_top2', float(dec[i]['main'] in _top2(o_main)))
                    S.add('rare_top2', float(dec[i]['main'] in pred_top2))
                # Student 1
                o_acts = np.mean([tgt[j]['acts'] for j in others], 0) >= 0.5
                T.add('acts', float((o_acts == np.array(tgt[i]['acts'], bool)).mean()))
                S.add('acts', float(((p1['acts'][gi] >= 0.5) == np.array(tgt[i]['acts'], bool)).mean()))
                o_tone = [tgt[j]['tone'] for j in others]
                T.add('tone', float(tgt[i]['tone'] in _mode_set(o_tone)) / max(1, len(_mode_set(o_tone))))
                S.add('tone', float(int(p1['tone'][gi].argmax()) == tgt[i]['tone']))
                o_req = [tgt[j]['request'] for j in others]
                T.add('request', float(tgt[i]['request'] in _mode_set(o_req)) / max(1, len(_mode_set(o_req))))
                S.add('request', float(int(p1['request'][gi].argmax()) == tgt[i]['request']))
                mine = np.array([tgt[i]['about_her'], tgt[i]['sure'], *tgt[i]['appraisal'], *tgt[i]['feelings']])
                o_sc = np.mean([[tgt[j]['about_her'], tgt[j]['sure'], *tgt[j]['appraisal'], *tgt[j]['feelings']]
                                for j in others], 0)
                T.add('feelings_error', float(np.abs(o_sc[6:] - mine[6:]).mean()))
                S.add('feelings_error', float(np.abs(p1['scalars'][gi][6:] - mine[6:]).mean()))
                T.add('appraisal_error', float(np.abs(o_sc[:6] - mine[:6]).mean()))
                S.add('appraisal_error', float(np.abs(p1['scalars'][gi][:6] - mine[:6]).mean()))

    def share(t: dict, s: dict) -> dict[str, Any]:
        out = {}
        for k in t:
            if k not in s:
                continue
            tv, sv = t[k]['value'], s[k]['value']
            if k.endswith('_error'):
                # Errors: the ceiling is the teacher's own spread; share = teacher error / student error.
                ratio = tv / sv if sv else 1.0
            else:
                ratio = sv / tv if tv else 0.0
            out[k] = dict(teacher=tv, student=sv, share=round(ratio, 3), n=t[k]['n'], passes=ratio >= PASS_SHARE)
        return out

    result = dict(test_turns=len(keys), test_examples=len(test_ex),
                  isolated_chain=share(teacher.result(), student.result()),
                  subsets={tag: share(T.result(), S.result()) for tag, (T, S) in subsets.items()})
    result['student_two_on_teacher_inputs'] = _s2_teacher_inputs(s2, test_ex, encoder)
    result['state_drift'] = _state_drift(s1, s2, episodes, {e.episode for e in test_ex}, encoder, dynamics)
    result['replays'] = _replays(s1, s2, examples, {e.episode for e in test_ex}, encoder, dynamics)
    (run_dir / models_name / 'evaluate.json').write_text(json.dumps(result, indent=1))
    return result


def _s2_teacher_inputs(s2: StudentTwo, test_ex: list[Example], encoder: Encoder) -> dict[str, float]:
    """Stage attribution: Student 2 given each sample's own teacher Understand/Feel and code stages."""
    x = _context([e.bundle for e in test_ex], encoder)
    extra, allowed = s2_inputs_teacher(test_ex)
    p = predict_s2(s2, x, extra, allowed)
    hits = [float(int(p['main'][i].argmax()) == GROUPS.index(e.decide['main'])) for i, e in enumerate(test_ex)]
    return dict(main_top1=round(float(np.mean(hits)), 4) if hits else None, n=len(hits))


def _state_drift(s1, s2, episodes, test_episodes, encoder, dynamics) -> dict[str, Any]:
    """State-drift diagnostic over each held-out recorded conversation (not a student-driven one).

    Student 1's readings drive her mood and the notes about the user (corrections, shared things,
    note updates). Notes about what *she* did follow the reply actually given (its recorded events,
    or the teacher's description of it): the replies are the teacher's, so a student's guessed move
    must not become the record of them. Student 2's choice is scored against the teacher's samples
    but changes no state. A full student-driven conversation needs the live speaking model.
    """
    from ..notes import ReplyEvents, events_from_reply
    tally = Tally()
    for ep in episodes:
        if ep['scenario']['id'] not in test_episodes:
            continue
        book, teacher_book_kinds = NoteBook(), None
        mood = None
        previous = None
        previous_reply = None
        for t in ep['turns']:
            if t.get('continued') is None or t.get('reply') is None:
                break
            teacher_bundle = Bundle.from_dict(t['bundle'])
            mood = mood or dict(teacher_bundle.mood)
            bundle = Bundle.from_dict(teacher_bundle.to_dict() | {
                'notes': [n.to_dict() for n in book.notes], 'mood': mood, 'previous': previous})
            x = _context([bundle], encoder)
            p1 = predict_s1(s1, x)
            understand = prediction_to_understand(p1, 0, bundle)
            mood_after, f, ok = code_stages(bundle, understand, dynamics)
            extra = _tensor([s2_extra(prediction_s1_vector(p1, 0), mood_after, f, ok)])
            allowed = _tensor([[float(g in ok) for g in GROUPS]])
            decide = prediction_to_decide(predict_s2(s2, x, extra, allowed), 0)
            chosen = next(a[-1] for a in t['samples'] if a[-1]['index'] == t['continued'])
            mains = [a[-1]['decide']['main'] for a in t['samples'] if a[-1].get('accepted')]
            if mains:
                tally.add('main_matches_a_teacher_sample', float(decide['main'] in mains))
                tally.add('main_matches_teacher_mode', float(decide['main'] in _mode_set(mains)))
                tally.add('teacher_main_blocked_by_student_mask', sum(m not in ok for m in mains) / len(mains))
            teacher_kinds = sorted(n.kind for n in teacher_bundle.notes)
            tally.add('notes_open', float(len(book.notes)))
            tally.add('notes_open_teacher', float(len(teacher_bundle.notes)))
            tally.add('note_kinds_match_teacher', float(sorted(n.kind for n in book.notes) == teacher_kinds))
            book.apply_message(turn=bundle.turn, message=bundle.message, understand=understand,
                               previous_reply=previous_reply)
            recorded = t.get('events')
            events = (ReplyEvents(**recorded) if recorded else
                      events_from_reply(t['reply'], chosen['decide'], source='description'))
            book.apply_reply(turn=bundle.turn + 1, message=bundle.message, understand=understand, reply=t['reply'],
                             events=events)
            previous_reply = t['reply']
            mood = mood_after
            # Next turn's "previous": her reading as the student saw it, and what she actually did.
            previous = dict(understand=understand, decide={k: chosen['decide'][k] for k in ('main', 'second', 'dials')})
    return tally.result()


def _replays(s1, s2, examples, test_episodes, encoder, dynamics) -> dict[str, Any]:
    """Mood replays: does the student move the same direction as the teacher when only mood changes?"""
    groups = _turn_groups([e for e in examples if e.episode in test_episodes])
    agree, total = Counter(), Counter()
    for (episode, turn, kind), replay in groups.items():
        if kind != 'replay' or (episode, turn, 'main') not in groups:
            continue
        main = groups[(episode, turn, 'main')]

        def teacher_vec(samples):
            u = [understand_targets(s.understand) for s in samples]
            d = [decide_targets(s.decide) for s in samples]
            return np.mean([[*x['feelings'], *x['appraisal']] for x in u], 0), np.mean([y['dials'] for y in d], 0)

        def student_vec(sample: Example):
            x = _context([sample.bundle], encoder)
            p1 = predict_s1(s1, x)
            understand = prediction_to_understand(p1, 0, sample.bundle)
            mood_after, f, ok = code_stages(sample.bundle, understand, dynamics)
            p2 = predict_s2(s2, x, _tensor([s2_extra(prediction_s1_vector(p1, 0), mood_after, f, ok)]),
                            _tensor([[float(g in ok) for g in GROUPS]]))
            return p1['scalars'][0][6:].tolist() + p1['scalars'][0][2:6].tolist(), p2['dials'][0]

        tf_m, td_m = teacher_vec(main)
        tf_r, td_r = teacher_vec(replay)
        sf_m, sd_m = student_vec(main[0])
        sf_r, sd_r = student_vec(replay[0])
        names = [*FEELINGS, 'good_for_her', 'warm_toward_her', 'surprising', 'important', *(f'dial_{d}' for d in DIALS)]
        t_delta = np.concatenate([tf_r - tf_m, td_r - td_m])
        s_delta = np.concatenate([np.array(sf_r) - np.array(sf_m), sd_r - sd_m])
        for name, td, sd in zip(names, t_delta, s_delta, strict=True):
            if abs(td) >= 0.15:
                total[name] += 1
                agree[name] += int(np.sign(td) == np.sign(sd) and abs(sd) >= 0.02)
    return {k: dict(same_direction=agree[k], of=total[k]) for k in sorted(total)} | dict(
        overall=dict(same_direction=sum(agree.values()), of=sum(total.values())))
