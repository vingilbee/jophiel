"""Student 1 at runtime: load once, read one message, put the reading into words.

Stateless (the per-conversation notes and logs live in ``services/cognitive_live.py``). Only the
trusted reading cues reach the reply model: what they're doing, what they want from her, their tone.
Feelings and appraisal are predicted too but stay experimental: logged, never shown.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from .contracts import ACTS, REQUEST_KINDS, TONES, Bundle

CHARACTER_DIR = Path(__file__).parent / 'character'

_ACT_WORDS = {
    'asking': 'asking you something', 'sharing': 'sharing something about themselves',
    'requesting': 'asking you to do something', 'teasing': 'teasing', 'complaining': 'complaining',
    'correcting_her': 'correcting you', 'small_talk': 'making small talk', 'ending': 'wrapping up',
}
_REQUEST_WORDS = {
    'no_request': 'nothing in particular beyond chatting',
    'talk_only': 'something you can give by talking (an answer, your view, company)',
    'outside_ability': 'something you can\'t do from a chat',
}


@lru_cache(maxsize=2)
def load(model_dir: str):
    """Student 1 and its frozen encoder, loaded once per process."""
    import torch

    from .student.encode import Encoder
    from .student.model import StudentOne
    model = StudentOne()
    model.load_state_dict(torch.load(Path(model_dir) / 'student_one.pt', map_location='cpu'))
    model.eval()
    return model, Encoder(cache_path=None, device='cpu')


def read(bundle: Bundle, model_dir: str) -> dict[str, Any]:
    """One message -> Student 1's soft outputs (probabilities) and its reading in words."""
    from .student.data import prediction_to_understand
    from .student.train import _context, predict_s1
    model, encoder = load(model_dir)
    pred = predict_s1(model, _context([bundle], encoder))
    understand = prediction_to_understand(pred, 0, bundle)
    return dict(
        understand=understand,
        acts={a: round(float(pred['acts'][0][i]), 3) for i, a in enumerate(ACTS)},
        tone={t: round(float(pred['tone'][0][i]), 3) for i, t in enumerate(TONES)},
        request={r: round(float(pred['request'][0][i]), 3) for i, r in enumerate(REQUEST_KINDS)},
        experimental=dict(about_her=understand['about_her'], sure=understand['sure'],
                          appraisal=understand['appraisal'], feelings=understand['feelings']),
    )


def reading_text(reading: dict[str, Any]) -> str:
    u = reading['understand']
    acts = [_ACT_WORDS[a] for a in ACTS if u['acts'][a]] or ['nothing clear']
    return (f'- What they seem to be doing: {", ".join(acts)}.\n'
            f'- What they seem to want from you: {_REQUEST_WORDS[u["request"]]}.\n'
            f'- Their tone seems: {u["tone"]}.')
