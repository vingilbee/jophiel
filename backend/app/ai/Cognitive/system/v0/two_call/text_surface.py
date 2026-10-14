"""Versioned display normalization for model-authored content, never source quotes."""
import unicodedata
from ..contracts.realization import valid_text


def model_surface(text: str) -> str:
    # Invisible Unicode formatting has no authorized assertion here. Keep the
    # raw proposal on the trace; downstream value and anchor share this surface.
    text = ''.join(c for c in text if unicodedata.category(c) != 'Cf')
    text = ' '.join(text.split())
    if not text or not valid_text(text):
        raise ValueError('model content cannot form a valid display surface')
    return text
