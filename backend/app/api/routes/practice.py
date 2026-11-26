"""Her practice notes (services/practice.py), the owner's view: read them, write your own, fix or remove hers.

    GET    /api/practice/notes          every note (?surface=)
    POST   /api/practice/notes          a note of yours for a place
    PATCH  /api/practice/notes/{id}     edit one (an edit makes it yours: a review won't change it)
    DELETE /api/practice/notes/{id}
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_owner, validate_origin
from app.db.models import User
from app.db.session import get_db
from app.schemas.practice import PracticeNoteCreate, PracticeNoteOut, PracticeNoteUpdate
from app.services import practice

router = APIRouter(prefix="/practice", tags=["practice"], dependencies=[Depends(require_owner)])
mutation = [Depends(validate_origin)]
_GONE = {"code": "not_found", "message": "no such note"}


@router.get("/notes", response_model=list[PracticeNoteOut])
def list_notes(surface: str | None = Query(None), user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    return practice.list_notes(db, user.id, surface=surface)


@router.post("/notes", response_model=PracticeNoteOut, status_code=status.HTTP_201_CREATED, dependencies=mutation)
def create_note(data: PracticeNoteCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        row = practice.keep_user_note(db, user.id, place=data.place, text=data.text, tool=data.tool,
                                      surface=data.surface)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid", "message": str(exc)}) from exc
    db.commit()
    db.refresh(row)
    return row


@router.patch("/notes/{note_id}", response_model=PracticeNoteOut, dependencies=mutation)
def update_note(note_id: uuid.UUID, data: PracticeNoteUpdate, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    try:
        row = practice.edit_note(db, user.id, note_id, text=data.text, status=data.status)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid", "message": str(exc)}) from exc
    if row is None:
        raise HTTPException(status_code=404, detail=_GONE)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=mutation)
def delete_note(note_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not practice.remove_note(db, user.id, note_id):
        raise HTTPException(status_code=404, detail=_GONE)
    db.commit()
