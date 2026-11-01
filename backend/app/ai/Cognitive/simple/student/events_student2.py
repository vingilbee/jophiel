"""Student 2, structured: one complete decision, chosen only among what the decision contract accepts.

Design: docs/STUDENT2_STUDENT_OUTPUT_DESIGN.md. Replaces the separate heads of events_student.py, which could
combine individually plausible fields into decisions the contract rejects and had no output for basis,
resolution, other goal, thinking or deferral.

- **Work part as one choice.** `events.work_options` lists every (operation, target, resolution, basis, other goal,
  handling) the validator accepts in the snapshot; the student scores each as a whole and picks among them.
- **References are read, not named.** A reference is scored from the encoder's state at its marker in the text
  (`[G1] ...`), so choosing a target can use what the goal says.
- **The rest follows the work part:** communication given the work part; move, second move, dials and `about` only
  when she speaks; the deferral only for defer; the thinking form and `think_with` only for thinking.
- **Text is handed on, never written:** `what_to_communicate` (the mouth writes from the selection), `think_about`
  (the thinking component formulates it), `not_before` (no training data has a clock yet). Placeholders say so.
- **Training keeps whole teacher decisions:** each teacher sample's complete path is scored given its own earlier
  choices and the loss averages over samples; no field is voted on its own.

Data: v4 snapshots (older runs upgraded by events_relabel). Samples are left out only when the validator rejects
them or an override in review_overrides.json says wrong or unsure; the model review alone excludes nothing.

Offline only.
"""
from __future__ import annotations

import json
import random
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import torch
from torch import nn

from ..contracts import DIALS, GROUPS, NO_SECOND, SCALE_WORDS
from ..events import (COMMUNICATION, EXPIRY, OTHER_HANDLING, RECONSIDER, RESOLUTIONS, REVIEW_OVERRIDES_FILE, SPEAKING,
                      THINK_FORMS, THINKING, WORK, WORK_FIELDS, _QUIET, candidates, check, load, with_exchange_line, with_memory_section,
                      work_options)
from .events_relabel import RELABEL_FILE, upgrade
from .events_student import (CONTRAST_TESTS, FROZEN_LAYERS, MAX_LEN, MODEL_NAME, ORIGINAL_TESTS, _batches, _bucket,
                             _device, split)

MOVES = GROUPS
SECOND = (*GROUPS, NO_SECOND)
RECONSIDER_ON = RECONSIDER[1:]
EXPIRIES = EXPIRY[1:]
FORMS = THINK_FORMS[1:]
HANDOFF = dict(what_to_communicate='<written by the mouth from the selection>',
               think_about='<formulated by the thinking component from the selection>')
_MARK = re.compile(r'\[(M|U\d+|R\d+|T\d+|A1|G\d+|W\d+|P\d+|E\d+)\]')


def markers(text: str) -> dict[str, int]:
    """Character offset of each reference's own line (where it is defined): the first occurrence at a line start
    or, for user messages, in the conversation line."""
    out: dict[str, int] = {}
    for m in _MARK.finditer(text):
        ref = m.group(1)
        if ref in out:
            continue
        line_start = text.rfind('\n', 0, m.start()) + 1
        prefix = text[line_start:m.start()]
        if prefix == '' or (ref.startswith('U') and prefix.startswith('- ') and text[m.end():m.end() + 6] == ' User:'):
            out[ref] = m.start()
    return out


@dataclass
class Example:
    sid: str
    family: str
    variant: str
    text: str
    refs: list[str]                  # candidate references, in snapshot order
    options: list[tuple[str, ...]]   # valid work parts
    samples: list[dict[str, Any]]    # complete teacher decisions (v4), validator-passed and not excluded
    snapshot: dict[str, Any] = field(repr=False)
    group: str = 'train'

    def ws(self, d: dict[str, Any]) -> tuple[str, ...]:
        return tuple(d[k] for k in WORK_FIELDS)


def load_examples(runs: list[Path]) -> tuple[list[Example], Counter]:
    stats: Counter = Counter()
    out = []
    for run in runs:
        labels = json.loads((run / RELABEL_FILE).read_text()) if (run / RELABEL_FILE).exists() else {}
        overrides = json.loads((run / REVIEW_OVERRIDES_FILE).read_text()) if (run / REVIEW_OVERRIDES_FILE).exists() else {}
        for r in load(run):
            for v in r['variants']:
                if not v['accepted'] or not v['samples']:
                    continue
                key = f'{r["plan"]["id"]}|{v["name"]}'
                snap, text, samples = upgrade(v, labels, key) if r.get('decision_version') != 'event-decision-v4' else (
                    v['snapshot'], v['rendered'], v['samples'])
                text = with_exchange_line(text, snap)   # who spoke last, as a plain line (2026-10-28)
                text = with_memory_section(text, snap)   # her memory check for [M] (v5, 2026-10-31)
                keep = []
                for i, d in enumerate(samples, 1):
                    verdict = (overrides.get(key, {}).get(str(i)) or {}).get('verdict')
                    if d['problems']:
                        stats['validator'] += 1
                    elif verdict in ('wrong', 'unsure'):
                        stats[f'override_{verdict}'] += 1
                    else:
                        keep.append(d)
                        stats['kept'] += 1
                if not keep:
                    continue
                options = work_options(snap)
                missing = [d for d in keep if tuple(d[k] for k in WORK_FIELDS) not in options]
                stats['not_in_options'] += len(missing)
                keep = [d for d in keep if d not in missing]
                if keep:
                    out.append(Example(r['plan']['id'], r['plan']['family'], v['name'], text,
                                       [ref for ref, _ in candidates(snap)], options, keep, snap))
    return out, stats


def _mlp(width_in: int, hidden: int = 256) -> nn.Module:
    return nn.Sequential(nn.Linear(width_in, hidden), nn.Tanh(), nn.Linear(hidden, 1))


class StructuredStudent(nn.Module):
    def __init__(self, dropout: float = 0.1, emb: int = 64):
        super().__init__()
        from transformers import AutoModel
        self.encoder = AutoModel.from_pretrained(MODEL_NAME)
        for p in [*self.encoder.embeddings.parameters(),
                  *(q for layer in self.encoder.layers[:FROZEN_LAYERS] for q in layer.parameters())]:
            p.requires_grad = False
        d = self.encoder.config.hidden_size
        self.drop = nn.Dropout(dropout)
        self.none_ref = nn.Parameter(torch.zeros(d))
        self.op_emb = nn.Embedding(len(WORK), emb)
        self.comm_emb = nn.Embedding(len(COMMUNICATION), emb)
        self.op = nn.Linear(d, len(WORK))
        self.target = _mlp(2 * d + emb)
        self.resolution = nn.Linear(2 * d, len(RESOLUTIONS))
        self.basis = _mlp(3 * d + emb)
        self.other = _mlp(3 * d)
        self.handling = nn.Linear(2 * d, len(OTHER_HANDLING))
        self.comm = nn.Linear(2 * d + emb, len(COMMUNICATION))
        self.move = nn.Linear(d + 2 * emb, len(MOVES))
        self.second = nn.Linear(d + 2 * emb, len(SECOND))
        self.dials = nn.ModuleDict({k: nn.Linear(d + 2 * emb, len(SCALE_WORDS)) for k in DIALS})
        self.about = _mlp(2 * d + emb)
        self.defer_item = _mlp(2 * d)
        self.reconsider = nn.Linear(d, len(RECONSIDER_ON))
        self.reconsider_ref = _mlp(2 * d)
        self.expiry = nn.Linear(d, len(EXPIRIES))
        self.form = nn.Linear(2 * d, len(FORMS))
        self.think_with = _mlp(3 * d)

    def encode(self, input_ids, attention_mask):
        hidden = self.encoder(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state
        mask = attention_mask.unsqueeze(-1).to(hidden.dtype)
        pooled = (hidden * mask).sum(1) / mask.sum(1)
        return self.drop(hidden), self.drop(pooled)

    # --- per example scoring (h: [L, D], p: [D], refs: {ref: vector}) ------------------------------

    def ref_vecs(self, h, p, positions: dict[str, int | None], refs: list[str]) -> dict[str, torch.Tensor]:
        out = {r: (h[positions[r]] if positions.get(r) is not None else p) for r in refs}
        out['none'] = self.none_ref
        return out

    def work_scores(self, p, R, options: list[tuple[str, ...]]) -> torch.Tensor:
        op_logits = self.op(p)
        cache: dict[tuple, torch.Tensor] = {}

        def memo(key, fn):
            if key not in cache:
                cache[key] = fn()
            return cache[key]

        scores = []
        for work, target, resolution, basis, other, handling in options:
            w = WORK.index(work)
            s = op_logits[w]
            if work != 'no_change':
                oe = self.op_emb.weight[w]
                s = s + memo(('t', w, target), lambda: self.target(torch.cat([p, oe, R[target]])).squeeze(-1))
                if work == 'resolve_goal':
                    s = s + memo(('r', target), lambda: self.resolution(torch.cat([p, R[target]])))[
                        RESOLUTIONS.index(resolution)]
                if basis != 'none' or work in ('resolve_goal', 'revise_goal', 'request_replanning', 'pursue', 'resume'):
                    s = s + memo(('b', w, target, basis), lambda: self.basis(
                        torch.cat([p, oe, R[target], R[basis]])).squeeze(-1))
                if work in ('pursue', 'resume'):
                    s = s + memo(('o', target, other), lambda: self.other(
                        torch.cat([p, R[target], R[other]])).squeeze(-1))
                    if other != 'none':
                        s = s + memo(('h', other), lambda: self.handling(torch.cat([p, R[other]])))[
                            OTHER_HANDLING.index(handling)]
            scores.append(s)
        return torch.stack(scores)

    def comm_logits(self, p, R, ws: tuple[str, ...]) -> torch.Tensor:
        return self.comm(torch.cat([p, self.op_emb.weight[WORK.index(ws[0])], R[ws[1]]]))

    def speech_logits(self, p, ws, comm: str):
        x = torch.cat([p, self.op_emb.weight[WORK.index(ws[0])], self.comm_emb.weight[COMMUNICATION.index(comm)]])
        return self.move(x), self.second(x), {k: head(x) for k, head in self.dials.items()}

    def about_logits(self, p, R, refs: list[str], comm: str) -> torch.Tensor:
        ce = self.comm_emb.weight[COMMUNICATION.index(comm)]
        return torch.stack([self.about(torch.cat([p, R[r], ce])).squeeze(-1) for r in refs])

    def defer_logits(self, p, R, refs: list[str]):
        item = torch.stack([self.defer_item(torch.cat([p, R[r]])).squeeze(-1) for r in refs])
        ref = torch.stack([self.reconsider_ref(torch.cat([p, R[r]])).squeeze(-1) for r in ['none', *refs]])
        return item, self.reconsider(p), ref, self.expiry(p)

    def think_logits(self, p, R, refs: list[str], target: str):
        others = [r for r in refs if r != target]
        form = self.form(torch.cat([p, R[target]]))
        if not others:
            return form, others, None
        return form, others, torch.stack([self.think_with(torch.cat([p, R[target], R[r]])).squeeze(-1) for r in others])


def _positions(tok, examples: list[Example]):
    enc = tok([e.text for e in examples], padding=True, truncation=True, max_length=MAX_LEN, pad_to_multiple_of=128,
              return_tensors='pt', return_offsets_mapping=True)
    positions = []
    for i, e in enumerate(examples):
        offsets = enc['offset_mapping'][i].tolist()
        where = {}
        for ref, char in markers(e.text).items():
            where[ref] = next((t for t, (a, b) in enumerate(offsets) if a <= char < b and b > a), None)
        positions.append(where)
    return enc['input_ids'], enc['attention_mask'], positions


def sample_loss(model: StructuredStudent, e: Example, h, p, pos) -> torch.Tensor:
    R = model.ref_vecs(h, p, pos, e.refs)
    ws_scores = torch.log_softmax(model.work_scores(p, R, e.options), -1)
    index = {o: i for i, o in enumerate(e.options)}
    total = 0.0
    for d in e.samples:
        ws = e.ws(d)
        ll = ws_scores[index[ws]]
        ll = ll + torch.log_softmax(model.comm_logits(p, R, ws), -1)[COMMUNICATION.index(d['communication'])]
        if d['communication'] in SPEAKING:
            move, second, dials = model.speech_logits(p, ws, d['communication'])
            ll = ll + torch.log_softmax(move, -1)[MOVES.index(d['move'])]
            ll = ll + 0.5 * torch.log_softmax(second, -1)[SECOND.index(d['second_move'])]
            for k, logits in dials.items():
                if d['dials'] and d['dials'].get(k) in SCALE_WORDS:
                    ll = ll + 0.25 * torch.log_softmax(logits, -1)[SCALE_WORDS.index(d['dials'][k])]
            if e.refs:
                target = torch.tensor([float(r in d['about']) for r in e.refs], device=p.device)
                ll = ll - 0.5 * nn.functional.binary_cross_entropy_with_logits(
                    model.about_logits(p, R, e.refs, d['communication']), target, reduction='sum')
        if d['communication'] == 'defer':
            item, on, ref, expiry = model.defer_logits(p, R, e.refs)
            ll = ll + torch.log_softmax(item, -1)[e.refs.index(d['defer_item'])]
            if d['reconsider_on'] in RECONSIDER_ON:
                ll = ll + torch.log_softmax(on, -1)[RECONSIDER_ON.index(d['reconsider_on'])]
            refs_n = ['none', *e.refs]
            if d['reconsider_ref'] in refs_n:
                ll = ll + torch.log_softmax(ref, -1)[refs_n.index(d['reconsider_ref'])]
            ll = ll + torch.log_softmax(expiry, -1)[EXPIRIES.index(d['defer_expiry'])]
        if d['work'] in THINKING and d['think_form'] in FORMS:
            form, others, with_logits = model.think_logits(p, R, e.refs, d['target'])
            ll = ll + torch.log_softmax(form, -1)[FORMS.index(d['think_form'])]
            if with_logits is not None:
                target = torch.tensor([float(r in d['think_with']) for r in others], device=p.device)
                ll = ll - 0.5 * nn.functional.binary_cross_entropy_with_logits(with_logits, target, reduction='sum')
        total = total - ll
    return total / len(e.samples)


def train(examples: list[Example], out: Path, *, epochs: int = 4, lr: float = 4e-5, head_lr: float = 1e-3,
          batch: int = 4, seed: int = 0, log=print) -> StructuredStudent:
    from transformers import AutoTokenizer
    torch.manual_seed(seed)
    rng = random.Random(seed)
    device = _device()
    tok = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = StructuredStudent().to(device)
    heads = [p for n, p in model.named_parameters() if not n.startswith('encoder.')]
    opt = torch.optim.AdamW([{'params': [p for p in model.encoder.parameters() if p.requires_grad], 'lr': lr},
                             {'params': heads, 'lr': head_lr}], weight_decay=0.01)
    steps = epochs * ((len(examples) + batch - 1) // batch)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=[lr, head_lr], total_steps=steps, pct_start=0.1)
    started = time.monotonic()
    for epoch in range(epochs):
        model.train()
        total = 0.0
        for b in _batches(examples, batch, True, rng):
            ids, att, pos = _positions(tok, b)
            h, p = model.encode(ids.to(device), att.to(device))
            loss = sum(sample_loss(model, e, h[i], p[i], pos[i]) for i, e in enumerate(b)) / len(b)
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            total += float(loss) * len(b)
        log(f'epoch {epoch + 1}/{epochs}: loss {total / len(examples):.3f} ({round(time.monotonic() - started)} s)')
    out.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), out / 'student.pt')
    return model


# --- Decoding --------------------------------------------------------------------------------------

def decode(e: Example, ws_scores: torch.Tensor, comm_fn, speech_fn, about_fn, defer_fn, think_fn,
           beam: int = 5) -> dict[str, Any]:
    """The most likely complete decision: the best (work part, communication) pair over the top work parts, then
    each dependent field among its valid values. Text fields are handed on (HANDOFF)."""
    logp = torch.log_softmax(ws_scores, -1)
    best, best_score = None, -1e9
    for i in torch.topk(logp, min(beam, len(e.options))).indices.tolist():
        comm_lp = torch.log_softmax(comm_fn(e.options[i]), -1)
        if not e.refs:   # nothing to keep: defer is not available
            comm_lp = comm_lp.clone()
            comm_lp[COMMUNICATION.index('defer')] = -1e9
        c = int(comm_lp.argmax())
        if float(logp[i] + comm_lp[c]) > best_score:
            best, best_score = (e.options[i], COMMUNICATION[c]), float(logp[i] + comm_lp[c])
    ws, comm = best
    d = dict(_QUIET) | dict(zip(WORK_FIELDS, ws)) | dict(communication=comm)
    if comm in SPEAKING:
        move, second, dials = speech_fn(ws, comm)
        d.update(move=MOVES[int(move.argmax())], second_move=SECOND[int(second.argmax())],
                 dials={k: SCALE_WORDS[int(v.argmax())] for k, v in dials.items()},
                 what_to_communicate=HANDOFF['what_to_communicate'])
        if e.refs:
            probs = torch.sigmoid(about_fn(comm))
            d['about'] = [e.refs[j] for j in torch.argsort(probs, descending=True).tolist() if probs[j] > 0.5][:4]
    if comm == 'defer':
        item, on, ref, expiry = defer_fn()
        refs_n = ['none', *e.refs]
        d.update(defer_item=e.refs[int(item.argmax())], defer_expiry=EXPIRIES[int(expiry.argmax())])
        ranked = [RECONSIDER_ON[j] for j in torch.argsort(on, descending=True).tolist()]
        # time_reached needs a time; without a clock in the data none can be chosen, so the next condition is used.
        d['reconsider_on'] = next(x for x in ranked if x != 'time_reached')
        if d['reconsider_on'] == 'work_finished':
            works = [r for r in e.refs if r.startswith('W')]
            if works:
                d['reconsider_ref'] = max(works, key=lambda r: float(ref[refs_n.index(r)]))
            else:
                d['reconsider_on'] = next(x for x in ranked if x not in ('time_reached', 'work_finished'))
        else:
            d['reconsider_ref'] = refs_n[int(ref.argmax())]
    if ws[0] in THINKING:
        form, others, with_logits = think_fn(ws[1])
        d.update(think_form=FORMS[int(form.argmax())], think_about=HANDOFF['think_about'])
        if with_logits is not None:
            probs = torch.sigmoid(with_logits)
            d['think_with'] = [others[j] for j in torch.argsort(probs, descending=True).tolist() if probs[j] > 0.5][:2]
    return d


@torch.no_grad()
def predict(model: StructuredStudent, examples: list[Example], batch: int = 8) -> list[dict[str, Any]]:
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(MODEL_NAME)
    device = next(model.parameters()).device
    model.eval()
    preds: dict[int, dict[str, Any]] = {}
    for b in _batches(examples, batch, False, random.Random(0)):
        ids, att, pos = _positions(tok, b)
        h, p = model.encode(ids.to(device), att.to(device))
        for i, e in enumerate(b):
            R = model.ref_vecs(h[i], p[i], pos[i], e.refs)
            pi = p[i]
            preds[id(e)] = decode(
                e, model.work_scores(pi, R, e.options),
                comm_fn=lambda ws, pi=pi, R=R: model.comm_logits(pi, R, ws),
                speech_fn=lambda ws, c, pi=pi: model.speech_logits(pi, ws, c),
                about_fn=lambda c, pi=pi, R=R, e=e: model.about_logits(pi, R, e.refs, c),
                defer_fn=lambda pi=pi, R=R, e=e: model.defer_logits(pi, R, e.refs),
                think_fn=lambda t, pi=pi, R=R, e=e: model.think_logits(pi, R, e.refs, t))
    return [preds[id(e)] for e in examples]


# --- Evaluation --------------------------------------------------------------------------------------

CORE = ('communication', *WORK_FIELDS)


def core(d: dict[str, Any]) -> tuple[str, ...]:
    return tuple(d[k] for k in CORE)


def teacher_majority(e: Example) -> dict[str, Any]:
    """The teacher sample whose complete core decision is most common (a real sample, never a field-wise vote)."""
    top = Counter(core(d) for d in e.samples).most_common(1)[0][0]
    return next(d for d in e.samples if core(d) == top)


def evaluate(examples: list[Example], preds: list[dict[str, Any]], idx: list[int]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    n = len(idx)
    T = {i: teacher_majority(examples[i]) for i in idx}
    out['n'] = n
    out['invalid'] = sum(bool(check(preds[i], examples[i].snapshot)) for i in idx)
    out['joint_comm_work_target'] = sum((preds[i]['communication'], preds[i]['work'], preds[i]['target'])
                                        == (T[i]['communication'], T[i]['work'], T[i]['target']) for i in idx) / max(1, n)
    out['complete_core_vs_majority'] = sum(core(preds[i]) == core(T[i]) for i in idx) / max(1, n)
    out['complete_core_vs_any_sample'] = sum(core(preds[i]) in {core(d) for d in examples[i].samples}
                                             for i in idx) / max(1, n)
    acting = [i for i in idx if T[i]['work'] != 'no_change']
    out['acting'] = len(acting)
    out['acting_op_right'] = sum(preds[i]['work'] == T[i]['work'] for i in acting)
    out['acting_work_part_right'] = sum(examples[i].ws(preds[i]) == examples[i].ws(T[i]) for i in acting)
    out['predicted_no_change'] = sum(preds[i]['work'] == 'no_change' for i in idx)
    out['teacher_no_change'] = n - len(acting)
    # step accuracy given the earlier steps were right
    steps: Counter = Counter()
    for i in idx:
        p, t = preds[i], T[i]
        if examples[i].ws(p) == examples[i].ws(t):
            steps['work_part_right'] += 1
            steps['comm_given_work'] += p['communication'] == t['communication']
            if p['communication'] == t['communication'] and t['communication'] in SPEAKING:
                steps['speaking'] += 1
                steps['move_given_comm'] += p['move'] == t['move']
    out['steps'] = dict(steps)
    per_op: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for i in idx:
        per_op[T[i]['work']][1] += 1
        per_op[T[i]['work']][0] += preds[i]['work'] == T[i]['work']
    out['per_operation'] = {k: f'{a}/{b}' for k, (a, b) in sorted(per_op.items(), key=lambda kv: -kv[1][1])}
    return out


def baselines(examples: list[Example], train_idx: list[int], idx: list[int]) -> dict[str, Any]:
    """Always the most common training decision, and the family's most common training decision (which knows the
    family name the student never sees)."""
    overall = Counter(core(teacher_majority(examples[i])) for i in train_idx).most_common(1)[0][0]
    fam: dict[str, Counter] = defaultdict(Counter)
    for i in train_idx:
        fam[examples[i].family][core(teacher_majority(examples[i]))] += 1
    out = {}
    for name, pick in (('always_most_common', lambda e: overall),
                       ('family_most_common', lambda e: fam[e.family].most_common(1)[0][0] if fam[e.family] else overall)):
        T = {i: core(teacher_majority(examples[i])) for i in idx}
        acting = [i for i in idx if T[i][1] != 'no_change']
        out[name] = dict(joint_comm_work_target=sum(pick(examples[i])[:3] == T[i][:3] for i in idx) / max(1, len(idx)),
                         acting_op_right=sum(pick(examples[i])[1] == T[i][1] for i in acting), acting=len(acting))
    return out


__all__ = ['Example', 'StructuredStudent', 'load_examples', 'split', 'train', 'predict', 'decode', 'evaluate',
           'baselines', 'markers', '_bucket', 'np']


def experiment(runs: list[Path], out: Path, *, epochs: int = 4, seed: int = 0, limit: int = 0, log=print) -> dict:
    """Train on the training situations, predict every example, score train and held-out sets against the teacher
    and the two baselines; writes preds.json and results.json. `limit` trains on a few examples (a smoke run)."""
    out.mkdir(parents=True, exist_ok=True)
    examples, stats = load_examples(runs)
    split(examples, seed)
    log(f'examples {len(examples)}; samples {dict(stats)}; groups {dict(Counter(e.group for e in examples))}')
    train_idx = [i for i, e in enumerate(examples) if e.group == 'train']
    pool = [examples[i] for i in train_idx]
    if limit:
        pool = random.Random(seed).sample(pool, limit)
    cached = out / 'preds.json'
    if cached.exists() and not limit:
        preds = json.loads(cached.read_text())
    else:
        model = train(pool, out, epochs=epochs, seed=seed, log=log)
        targets = examples if not limit else pool
        preds = predict(model, targets)
        if limit:
            return dict(smoke=True, invalid=sum(bool(check(p, e.snapshot)) for p, e in zip(preds, pool)), n=len(pool))
        cached.write_text(json.dumps(preds))
    groups = {g: [i for i, e in enumerate(examples) if e.group == g] for g in ('train', *ORIGINAL_TESTS, *CONTRAST_TESTS)}
    groups = {g: v for g, v in groups.items() if v or g not in CONTRAST_TESTS}
    held = [i for i, e in enumerate(examples) if e.group in ORIGINAL_TESTS]
    result = dict(stats=dict(stats), groups={g: len(v) for g, v in groups.items()},
                  train=evaluate(examples, preds, groups['train']), held_out=evaluate(examples, preds, held),
                  **{g: evaluate(examples, preds, v) for g, v in groups.items() if g != 'train'},
                  baselines=baselines(examples, train_idx, held))
    (out / 'results.json').write_text(json.dumps(result, indent=1))
    return result
