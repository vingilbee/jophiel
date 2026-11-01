"""Embedding memory events for recall, then what was said for transcript search, for the worker started with
python -m app.workers."""
from app.services import recall_embeddings, transcript_search


def run_once() -> bool:
    return recall_embeddings.run_once() or transcript_search.embed_run_once()


__all__ = ["run_once"]
