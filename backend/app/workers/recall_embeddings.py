"""Embedding memory events for recall, for the worker started with python -m app.workers."""
from app.services.recall_embeddings import run_once

__all__ = ["run_once"]
