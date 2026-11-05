"""Memory ownership of optional game episodes from shared assessment revisions."""
import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from app.db.models import EpisodicMemory


def from_assessment(db, assessment, revision, episode):
    if assessment.game_session_id is None:
        return
    episode_id = uuid.uuid5(assessment.id, "episode")
    row = db.get(EpisodicMemory, episode_id)
    if not episode:
        if row is not None:
            row.status = "superseded"
        return
    from app.services.long_term_memory import _embedding
    if row is None:
        row = EpisodicMemory(id=episode_id, user_id=assessment.user_id,
                             conversation_id=assessment.conversation_id, event_at=assessment.anchor_at,
                             importance=0.5, confidence=0.5, summary=episode["summary"])
        db.add(row)
    row.summary, row.status = episode["summary"], "active"
    row.embedding = _embedding(row.summary)
    row.source_message_ids = [r.split(":", 1)[1] for r in episode["roots"] if r.startswith("message:")]
    row.context = {"source": "game", "game": "minecraft", "session_id": str(assessment.game_session_id),
                   "assessment_id": str(assessment.id), "assessment_revision": revision,
                   "source_refs": episode["roots"], "interpretation": True}


def latest_game(db, user_id, game):
    return db.scalar(select(EpisodicMemory).where(
        EpisodicMemory.user_id == user_id, EpisodicMemory.status == "active",
        EpisodicMemory.context["source"].astext == "game", EpisodicMemory.context["game"].astext == game,
        EpisodicMemory.event_at >= datetime.now(timezone.utc) - timedelta(days=7))
        .order_by(EpisodicMemory.event_at.desc()).limit(1))
