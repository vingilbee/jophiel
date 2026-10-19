"""The two students: small networks over encoded context slots and numbers.

Each slot (message, 6 recent messages, 6 notes, 5 memories) gets a shared small projection per slot
type, and the projections are concatenated in order (an MLP has no sense of order on its own, so
the order is the position). No attention yet: the plan adds it only if measurement says so.
"""
from __future__ import annotations

import torch
from torch import nn

from ..contracts import ACTS, DIALS, GROUPS, MAX_MEMORIES, MAX_NOTES, NOTE_UPDATES, REQUEST_KINDS, TONES, WINDOW
from .data import NUMERIC_WIDTH, S2_EXTRA_WIDTH, SLOT_EXTRA
from .encode import DIM

SLOT_TYPES = (1, WINDOW, MAX_NOTES, MAX_MEMORIES)
SCALARS = 14   # about_her, sure, 4 appraisal, 8 feelings
SIGNED_SCALARS = (2, 3)   # good_for_her, warm_toward_her are -1..1


class Context(nn.Module):
    def __init__(self, width: int = 48, dropout: float = 0.2):
        super().__init__()
        self.proj = nn.ModuleList(nn.Sequential(nn.Linear(DIM + SLOT_EXTRA, width), nn.GELU()) for _ in SLOT_TYPES)
        self.drop = nn.Dropout(dropout)
        self.width = width
        self.out = width * sum(SLOT_TYPES)

    def forward(self, slots: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        parts, start = [], 0
        for count, proj in zip(SLOT_TYPES, self.proj, strict=True):
            chunk = slots[:, start:start + count]
            present = chunk[..., DIM:DIM + 1]
            parts.append(proj(chunk) * present)       # empty slots stay zero
            start += count
        per_slot = torch.cat(parts, dim=1)            # (N, SLOTS, width)
        return self.drop(per_slot.flatten(1)), per_slot


def _trunk(inp: int, hidden: int, dropout: float) -> nn.Sequential:
    return nn.Sequential(nn.Linear(inp, hidden), nn.GELU(), nn.Dropout(dropout), nn.Linear(hidden, hidden), nn.GELU(),
                         nn.Dropout(dropout))


class StudentOne(nn.Module):
    """Understand + Feel (+ per-note updates)."""

    def __init__(self, width: int = 48, hidden: int = 256, dropout: float = 0.2):
        super().__init__()
        self.context = Context(width, dropout)
        self.trunk = _trunk(self.context.out + NUMERIC_WIDTH, hidden, dropout)
        self.acts = nn.Linear(hidden, len(ACTS))
        self.tone = nn.Linear(hidden, len(TONES))
        self.request = nn.Linear(hidden, len(REQUEST_KINDS))
        self.scalars = nn.Linear(hidden, SCALARS)
        self.notes = nn.Linear(hidden + width, len(NOTE_UPDATES))

    def forward(self, slots, numeric):
        flat, per_slot = self.context(slots)
        h = self.trunk(torch.cat([flat, numeric], dim=1))
        raw = self.scalars(h)
        scalars = torch.sigmoid(raw).clone()
        scalars[:, SIGNED_SCALARS] = torch.tanh(raw[:, SIGNED_SCALARS])
        note_slots = per_slot[:, 1 + WINDOW:1 + WINDOW + MAX_NOTES]
        notes = self.notes(torch.cat([h.unsqueeze(1).expand(-1, MAX_NOTES, -1), note_slots], dim=-1))
        return dict(acts=self.acts(h), tone=self.tone(h), request=self.request(h), scalars=scalars, notes=notes)


class StudentTwo(nn.Module):
    """Decide: main move, optional second move, dials; impossible moves are masked out."""

    def __init__(self, width: int = 48, hidden: int = 256, dropout: float = 0.2):
        super().__init__()
        self.context = Context(width, dropout)
        self.trunk = _trunk(self.context.out + NUMERIC_WIDTH + S2_EXTRA_WIDTH, hidden, dropout)
        self.main = nn.Linear(hidden, len(GROUPS))
        self.second = nn.Linear(hidden, len(GROUPS) + 1)
        self.dials = nn.Linear(hidden, len(DIALS))

    def forward(self, slots, numeric, extra, allowed):
        flat, _ = self.context(slots)
        h = self.trunk(torch.cat([flat, numeric, extra], dim=1))
        block = (allowed < 0.5)
        main = self.main(h).masked_fill(block, -1e9)
        second_block = torch.cat([block, torch.zeros_like(block[:, :1])], dim=1)
        second = self.second(h).masked_fill(second_block, -1e9)
        return dict(main=main, second=second, dials=torch.sigmoid(self.dials(h)))
