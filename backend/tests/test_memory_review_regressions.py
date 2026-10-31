"""Review regressions for working memory (2026-10-20): a new world can't resume the old plan, a planner's new
goal gets its own memory link, open sub-goals outlive their parent in the view, and a first start with
``new_goal`` makes one goal. Real PostgreSQL (``TEST_DATABASE_URL``); in-memory Redis."""
from tests.test_memory_manager import (  # noqa: F401  (fixtures)
    database, isolated_redis, enabled, memory_on, alice, bob, db,
    _playing, _goals, _message,
)
from tests.test_desktop_game import _paired_env
from app.services import memory_manager, desktop_game
from app.services import desktop_sessions as svc
from app.services.desktop_sessions import DesktopError
from app.db.models import WorkingItem
import pytest


def test_open_child_remains_visible_after_parent_closed(db, alice):
    goal = memory_manager.set_goal(db, alice.id, 'plan the trip')
    child = memory_manager.set_goal(db, alice.id, 'book flights', parent_id=goal.id)
    db.commit()
    memory_manager.resolve(db, alice.id, goal.id, status='abandoned', reason='changed plan')
    db.commit()
    assert child.text in (memory_manager.view(db, alice.id) or '')


def test_new_world_does_not_allow_old_plan_to_resume(db, alice):
    session = _playing(db, alice, goal='smelt iron in the old cave')
    memory_manager.switch(db, alice.id, label='new map', kind='world', new=True)
    db.commit()
    db.refresh(session)
    with pytest.raises(DesktopError):
        svc.resume_session(db, alice.id, session.id, expected_state_revision=session.state_revision)


def test_first_start_with_new_goal_creates_only_one_goal(db, alice):
    _paired_env(db, alice)
    desktop_game.start(db, alice.id, goal='build a house',
                       steps=[{'do': 'get logs', 'done_when': '16'}], new_goal=True)
    assert len(_goals(db, alice)) == 1


def test_planner_new_goal_updates_memory_link(db, alice):
    session = _playing(db, alice, goal='reach the Nether')
    first_id = session.goal_item_id
    desktop_game.replace_from_planner(db, alice.id, read_revision=session.plan_revision,
        goal='build a house', steps=[{'do': 'get logs', 'done_when': '16'}],
        constraints=[], target=None, source='columbina_planning_turn', new_goal=True)
    db.refresh(session)
    assert session.goal_item_id != first_id
    assert db.get(WorkingItem, session.goal_item_id).text == 'build a house'
