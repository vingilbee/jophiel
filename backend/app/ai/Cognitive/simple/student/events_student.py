"""Student 2 for event decisions: ModernBERT over the rendered snapshot -> communication, work operation,
target, move and other-goal handling (the decision contract in events.py, minus free text).

- Labels are the teacher's samples as soft targets (its disagreement is kept, not voted away).
- Split by situation: every variant of a held-out situation (base, twin, thought substitutions) is held
  out together. Three held-out sets: general, thought substitution (situations with thought variants) and
  motives (graded motive families, half the situations of each level).
- `drop_subs` trains without thought substitutions: the control for whether those examples teach her to
  judge a thought rather than follow it.

Offline only (training data experiments); nothing here is on a live chat path.
"""
from __future__ import annotations

import hashlib
import json
import random
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import torch
from torch import nn

from ..contracts import GROUPS
from ..events import (CONTRAST_SETS, COMMUNICATION, EVAL_ONLY, FAMILY_BY_ID, OTHER_HANDLING, WORK, candidates, load_review,
                      review_verdict, with_exchange_line, with_memory_section)

MODEL_NAME = 'answerdotai/ModernBERT-base'
# The original held-out sets, scored as "held out" exactly as before; contrast sets are reported on their own.
ORIGINAL_TESTS = ('test_general', 'test_thought', 'test_motive')
CONTRAST_TESTS = ('test_contrast_seen', 'test_contrast_unseen', 'test_recall_seen', 'test_recall_unseen',
                  'test_scope_seen', 'test_scope_unseen', 'test_think_seen', 'test_think_unseen',
                  'test_speak_seen', 'test_speak_unseen')
MAX_LEN = 1280
FROZEN_LAYERS = 11   # of 22: the lower half stays as pretrained (small data, and half the backward pass)
REFS = ('none', 'M', *(f'U{i}' for i in range(1, 17)), *(f'R{i}' for i in range(1, 9)), *(f'T{i}' for i in range(1, 9)),
        'A1', *(f'G{i}' for i in range(1, 9)), *(f'W{i}' for i in range(1, 11)), *(f'P{i}' for i in range(1, 6)),
        *(f'E{i}' for i in range(1, 6)))
HEADS: dict[str, tuple[str, ...]] = dict(communication=COMMUNICATION, work=WORK, target=REFS, move=(*GROUPS, 'none'),
                                         handling=OTHER_HANDLING)
HEAD_WEIGHT = dict(communication=1.0, work=1.0, target=1.0, move=0.5, handling=0.5)
FIELD = dict(communication='communication', work='work', target='target', move='move', handling='other_goal_handling')


@dataclass
class Example:
    sid: str                    # situation (plan id)
    family: str
    variant: str
    text: str
    allowed: np.ndarray         # target refs present in the snapshot (+ none)
    soft: dict[str, np.ndarray]
    samples: list[dict[str, Any]]
    candidates: dict[str, str]
    record: dict[str, Any] = field(repr=False, default_factory=dict)
    group: str = 'train'

    def majority(self, head: str) -> str:
        return Counter(s[FIELD[head]] for s in self.samples).most_common(1)[0][0]


def _bucket(key: str, seed: int) -> float:
    return int(hashlib.sha1(f'{seed}:{key}'.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def focus_text(rendered: str) -> str:
    """The same snapshot with her thought and the new message repeated first: a one-sentence difference
    otherwise drowns in ~700 tokens of context (student-side formatting; the snapshot is unchanged)."""
    lines = rendered.split('\n')
    picked = [ln for ln in lines if ln.startswith(('[T1]', 'HER PRIVATE THOUGHT', '[M]', 'NEW MESSAGE', 'TRIGGER'))]
    return 'IN FOCUS\n' + '\n'.join(picked) + '\n\nFULL SITUATION\n' + rendered


def load_examples(runs: list[Path], *, focus: bool = False) -> list[Example]:
    """Accepted snapshots; samples the validator rejected or the review marked wrong are left out
    (an override in review_overrides.json replaces the model's verdict)."""
    out = []
    for run in runs:
        review = load_review(run)
        for path in sorted((run / 'situations').glob('*.json')):
            r = json.loads(path.read_text())
            for v in r['variants']:
                samples = [s for i, s in enumerate(v.get('samples') or [], 1) if not s['problems']
                           and (review_verdict(review, r['plan']['id'], v['name'], i) or {}).get('verdict') != 'wrong']
                if not v['accepted'] or not samples:
                    continue
                present = {ref for ref, _ in candidates(v['snapshot'])} | {'none'}
                allowed = np.array([ref in present for ref in REFS], dtype=bool)
                soft = {}
                for head, vocab in HEADS.items():
                    vec = np.zeros(len(vocab), np.float32)
                    for s in samples:
                        value = s[FIELD[head]]
                        if value in vocab:
                            vec[vocab.index(value)] += 1
                    soft[head] = vec / max(1.0, vec.sum())
                rendered = with_memory_section(with_exchange_line(v['rendered'], v['snapshot']), v['snapshot'])
                text = focus_text(rendered) if focus else rendered
                out.append(Example(r['plan']['id'], r['plan']['family'], v['name'], text, allowed, soft,
                                   samples, dict(candidates(v['snapshot'])), record=r))
    return out


def split(examples: list[Example], seed: int = 0, *, general: float = 0.15, thought: float = 0.35,
          motive_per_family: int = 2) -> None:
    """Assign groups in place, by situation."""
    by_sid: dict[str, list[Example]] = defaultdict(list)
    for e in examples:
        by_sid[e.sid].append(e)
    motive_sids: dict[str, list[str]] = defaultdict(list)
    for sid, es in by_sid.items():
        if es[0].family.startswith('mot_'):
            motive_sids[es[0].family].append(sid)
    held: dict[str, str] = {}
    # Contrast families (2026-10-26): evaluation-only siblings are never trained on; every other contrast family
    # holds out exactly one situation, picked at random by the same hash, so each is measured. Assigned here and
    # skipped below, so every other situation keeps the group it had.
    contrast_sids: dict[str, list[str]] = defaultdict(list)
    for sid, es in by_sid.items():
        family = FAMILY_BY_ID.get(es[0].family)
        if family is not None and family.set in CONTRAST_SETS:
            contrast_sids[family.id].append(sid)
    for fam, sids in contrast_sids.items():
        prefix = f'test_{FAMILY_BY_ID[fam].set}'   # recall families (2026-10-27) get their own groups
        if fam in EVAL_ONLY:
            held.update({sid: f'{prefix}_unseen' for sid in sids})
        else:
            held[min(sids, key=lambda s: _bucket(s, seed))] = f'{prefix}_seen'
    contrast = {sid for sids in contrast_sids.values() for sid in sids}
    for fam, sids in motive_sids.items():
        for sid in sorted(sids, key=lambda s: _bucket(s, seed))[:motive_per_family]:
            held[sid] = 'test_motive'
    for sid, es in by_sid.items():
        if sid in held or sid in contrast or es[0].family.startswith('mot_'):
            continue
        has_subs = any(e.variant.startswith('thought:') for e in es)
        b = _bucket(sid, seed)
        if has_subs and b < thought:
            held[sid] = 'test_thought'
        elif not has_subs and b < general:
            held[sid] = 'test_general'
    for e in examples:
        e.group = held.get(e.sid, 'train')


class EventStudent(nn.Module):
    def __init__(self, dropout: float = 0.1, cls: bool = False):
        super().__init__()
        self.cls = cls
        from transformers import AutoModel
        self.encoder = AutoModel.from_pretrained(MODEL_NAME)
        for p in [*self.encoder.embeddings.parameters(),
                  *(q for layer in self.encoder.layers[:FROZEN_LAYERS] for q in layer.parameters())]:
            p.requires_grad = False
        width = self.encoder.config.hidden_size
        self.drop = nn.Dropout(dropout)
        self.heads = nn.ModuleDict({h: nn.Linear(width * (2 if cls else 1), len(v)) for h, v in HEADS.items()})

    def forward(self, input_ids, attention_mask, allowed):
        hidden = self.encoder(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state
        mask = attention_mask.unsqueeze(-1).to(hidden.dtype)
        pooled = (hidden * mask).sum(1) / mask.sum(1)
        if self.cls:   # the first token's own view next to the average
            pooled = torch.cat([hidden[:, 0], pooled], dim=-1)
        pooled = self.drop(pooled)
        out = {h: head(pooled) for h, head in self.heads.items()}
        out['target'] = out['target'].masked_fill(~allowed, -1e4)
        return out


def _device() -> str:
    # Not MPS: on an M1 it rebuilds kernels for every new sequence length (minutes each) and was slower than
    # the CPU at these lengths once warm.
    return 'cuda' if torch.cuda.is_available() else 'cpu'


def _batches(examples: list[Example], size: int, shuffle: bool, rng: random.Random) -> list[list[Example]]:
    # Similar lengths together (less padding), batches shuffled.
    order = sorted(examples, key=lambda e: len(e.text))
    batches = [order[i:i + size] for i in range(0, len(order), size)]
    if shuffle:
        rng.shuffle(batches)
    return batches


def _encode(tok, batch: list[Example], device: str):
    t = tok([e.text for e in batch], padding=True, truncation=True, max_length=MAX_LEN, pad_to_multiple_of=128,
            return_tensors='pt')
    allowed = torch.as_tensor(np.stack([e.allowed for e in batch]))
    return t['input_ids'].to(device), t['attention_mask'].to(device), allowed.to(device)


def train(examples: list[Example], out: Path, *, epochs: int = 4, lr: float = 4e-5, head_lr: float = 1e-3,
          batch: int = 4, seed: int = 0, cls: bool = False, log=print) -> EventStudent:
    from transformers import AutoTokenizer
    torch.manual_seed(seed)
    rng = random.Random(seed)
    device = _device()
    tok = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = EventStudent(cls=cls).to(device)
    opt = torch.optim.AdamW([{'params': [p for p in model.encoder.parameters() if p.requires_grad], 'lr': lr},
                             {'params': model.heads.parameters(), 'lr': head_lr}], weight_decay=0.01)
    steps = epochs * ((len(examples) + batch - 1) // batch)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=[lr, head_lr], total_steps=steps, pct_start=0.1)
    started, step = time.monotonic(), 0
    for epoch in range(epochs):
        model.train()
        total = 0.0
        for b in _batches(examples, batch, True, rng):
            ids, att, allowed = _encode(tok, b, device)
            logits = model(ids, att, allowed)
            loss = 0.0
            for head, w in HEAD_WEIGHT.items():
                soft = torch.as_tensor(np.stack([e.soft[head] for e in b])).to(device)
                logp = torch.log_softmax(logits[head], dim=-1)
                loss = loss + w * -(soft * logp).sum(-1).mean()
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            total += float(loss) * len(b)
            step += 1
        log(f'epoch {epoch + 1}/{epochs}: loss {total / len(examples):.3f} ({round(time.monotonic() - started)} s)')
    out.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), out / 'student.pt')
    return model


@torch.no_grad()
def predict(model: EventStudent, examples: list[Example], batch: int = 8) -> list[dict[str, str]]:
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(MODEL_NAME)
    device = next(model.parameters()).device
    model.eval()
    preds: dict[int, dict[str, str]] = {}
    for b in _batches(examples, batch, False, random.Random(0)):
        ids, att, allowed = _encode(tok, b, str(device))
        logits = model(ids, att, allowed)
        for i, e in enumerate(b):
            preds[id(e)] = {h: HEADS[h][int(logits[h][i].argmax())] for h in HEADS}
    return [preds[id(e)] for e in examples]


# --- Evaluation --------------------------------------------------------------------------------

def _decision(e: Example, d: dict[str, str]) -> tuple[str, str, str]:
    return d['communication'], d['work'], d['target'] if d['work'] != 'no_change' else 'none'


def teacher_decision(e: Example) -> tuple[str, str, str]:
    picks = Counter((s['communication'], s['work'], s['target'] if s['work'] != 'no_change' else 'none')
                    for s in e.samples)
    return picks.most_common(1)[0][0]


def teacher_ceiling(examples: list[Example]) -> float:
    """Leave-one-out: how often one teacher sample matches the majority of the others (joint decision)."""
    hits, n = 0, 0
    for e in examples:
        keys = [(s['communication'], s['work'], s['target'] if s['work'] != 'no_change' else 'none') for s in e.samples]
        for i, k in enumerate(keys):
            rest = keys[:i] + keys[i + 1:]
            if rest:
                hits += k == Counter(rest).most_common(1)[0][0]
                n += 1
    return hits / max(1, n)


def head_accuracy(examples: list[Example], preds: list[dict[str, str]]) -> dict[str, float]:
    out = {h: float(np.mean([p[h] == e.majority(h) for e, p in zip(examples, preds, strict=True)])) for h in HEADS}
    out['joint'] = float(np.mean([_decision(e, p) == teacher_decision(e) for e, p in zip(examples, preds, strict=True)]))
    return out


def thought_sensitivity(examples: list[Example], preds: list[dict[str, str]]) -> dict[str, Any]:
    """Against the same moment with an accurate thought: when the teacher's decision changes, does the
    student's change to the teacher's; when it stays, does the student's stay?"""
    by = {(e.sid, e.variant): (e, p) for e, p in zip(examples, preds, strict=True)}
    rows: dict[str, Counter] = defaultdict(Counter)
    for (sid, name), (e, p) in by.items():
        if not name.startswith('thought:') or name == 'thought:accurate' or (sid, 'thought:accurate') not in by:
            continue
        a, pa = by[(sid, 'thought:accurate')]
        kind = name.removeprefix('thought:')
        t_changed = teacher_decision(e) != teacher_decision(a)
        s_changed = _decision(e, p) != _decision(a, pa)
        key = 'teacher_changed' if t_changed else 'teacher_stayed'
        rows[kind][f'{key}'] += 1
        if t_changed:
            rows[kind]['student_followed_change'] += _decision(e, p) == teacher_decision(e)
            rows[kind]['student_changed_at_all'] += s_changed
        else:
            rows[kind]['student_stayed'] += not s_changed
    return {k: dict(v) for k, v in rows.items()}
