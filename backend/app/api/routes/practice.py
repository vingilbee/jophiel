"""Her practice notes (services/practice.py), the owner's view: read them, write your own, fix or remove hers.

    GET    /api/practice/notes          every note (?surface=)
    POST   /api/practice/notes          a note of yours for a place
    PATCH  /api/practice/notes/{id}     edit one (an edit makes it yours: a review won't change it), or pin it
    DELETE /api/practice/notes/{id}
    GET    /api/practice/places         what stays the same in each place she uses (?surface=)
    PATCH  /api/practice/places/{id}    fix her own lines about it
    DELETE /api/practice/places/{id}    forget the place (and everything she knows of it)
    PATCH  /api/practice/places/entries/{id}   fix one thing she knows of it
    DELETE /api/practice/places/entries/{id}
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_owner, validate_origin
from app.db.models import User
from app.db.session import get_db
from app.schemas.practice import (PlaceEntryOut, PlaceEntryUpdate, PlaceLayoutOut, PlaceLayoutUpdate,
                                  PracticeNoteCreate, PracticeNoteOut, PracticeNoteUpdate)
from app.services import place_memory, practice

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
        row = practice.edit_note(db, user.id, note_id, text=data.text, status=data.status,
                                 pinned=data.pinned)
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


_NO_PLACE = {"code": "not_found", "message": "no such place"}


@router.get("/places", response_model=list[PlaceLayoutOut])
def list_places(surface: str | None = Query(None), user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    return place_memory.list_places(db, user.id, surface=surface)


@router.patch("/places/entries/{entry_id}", response_model=PlaceEntryOut, dependencies=mutation)
def update_place_entry(entry_id: uuid.UUID, data: PlaceEntryUpdate, user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    entry = place_memory.edit_entry(db, user.id, entry_id, text=data.text)
    if entry is None:
        raise HTTPException(status_code=404, detail=_NO_PLACE)
    db.commit()
    db.refresh(entry)
    return entry


@router.delete("/places/entries/{entry_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=mutation)
def delete_place_entry(entry_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not place_memory.remove_entry(db, user.id, entry_id):
        raise HTTPException(status_code=404, detail=_NO_PLACE)
    db.commit()


@router.patch("/places/{layout_id}", response_model=PlaceLayoutOut, dependencies=mutation)
def update_place(layout_id: uuid.UUID, data: PlaceLayoutUpdate, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    row = place_memory.edit_place(db, user.id, layout_id, her_layout=data.her_layout)
    if row is None:
        raise HTTPException(status_code=404, detail=_NO_PLACE)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/places/{layout_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=mutation)
def delete_place(layout_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not place_memory.remove_place(db, user.id, layout_id):
        raise HTTPException(status_code=404, detail=_NO_PLACE)
    db.commit()
