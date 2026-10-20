"""The inner-state updater (app/ai/mind/inner_state.py): each event once, lingering adds nothing,
sustained holds without raising, easing works on its own incident, fading doesn't depend on how
often anyone looks."""
import pytest

from app.ai.mind.inner_state import AMOUNTS, KEYS, Change, Dynamics, apply, fade, start

DYN = Dynamics(baseline=dict(mood=0.3, energy=0.55, interest=0.6, tension=0.15, closeness=0.1),
               half_life_minutes=dict(mood=30, energy=60, interest=60, tension=10, closeness=1440))


def change(event="m6", feeling="hurt", direction=1, amount="somewhat", status="opened", incident="i1", at=0.0):
    return Change("chat", "c1", event, incident, feeling, direction, AMOUNTS[amount], status, at)


def run(*changes, state=None):
    s = state or start(DYN, 0.0)
    for c in changes:
        s = apply(s, c, DYN).state
    return s


def test_a_duplicate_applies_once():
    once = run(change())
    twice = apply(once, change(), DYN)
    assert not twice.applied and twice.reason == "duplicate"
    assert twice.state.values == once.values


def test_untouched_values_only_fade():
    s = start(DYN, 0.0)
    s.values["interest"] = 0.9
    out = apply(s, change(at=600.0), DYN).state   # hurt touches tension, mood, closeness
    assert out.values["interest"] == pytest.approx(fade(s, DYN, 600.0).values["interest"])
    assert out.values["energy"] == pytest.approx(DYN.baseline["energy"])


def test_fading_does_not_depend_on_polling():
    s = run(change())
    one_jump = fade(s, DYN, 3600.0)
    stepped = s
    for t in range(60, 3601, 60):
        stepped = fade(stepped, DYN, float(t))
    for k in KEYS:
        assert stepped.values[k] == pytest.approx(one_jump.values[k])


def test_lingering_adds_nothing():
    s = run(change())
    out = apply(s, change(event="m7", status="lingering", at=30.0), DYN)
    assert not out.applied and out.reason == "lingering"
    assert out.state.values == fade(s, DYN, 30.0).values


def test_one_vs_ten_lingering_messages_over_the_same_time():
    hurt = run(change())
    one = run(change(event="m7", status="lingering", at=600.0), state=hurt)
    ten = run(*(change(event=f"n{i}", status="lingering", at=60.0 * (i + 1)) for i in range(10)), state=hurt)
    for k in KEYS:
        assert ten.values[k] == pytest.approx(one.values[k])


def test_sustained_holds_without_raising():
    warm = run(change(feeling="happy", amount="somewhat", incident="chat"))
    peak = warm.values["mood"]
    held = warm
    for i in range(10):
        held = run(change(event=f"n{i}", feeling="happy", status="sustained", incident="chat", at=60.0 * (i + 1)),
                   state=held)
    assert held.values["mood"] == pytest.approx(peak)               # held where it was...
    faded = fade(warm, DYN, 600.0).values["mood"]
    assert faded < held.values["mood"]                              # ...where fading alone would drop it


def test_sustained_on_an_unknown_incident_does_nothing():
    out = apply(start(DYN, 0.0), change(feeling="happy", status="sustained", incident="nope"), DYN)
    assert not out.applied and out.reason == "nothing_to_hold"


def test_escalation_accumulates():
    one = run(change(amount="a_little"))
    three = run(change(amount="a_little"), change(event="m8", status="escalated", amount="a_little", at=10.0),
                change(event="m9", status="escalated", amount="a_little", at=20.0))
    assert three.values["tension"] > one.values["tension"] + 0.15


def test_independent_incidents_do_not_discount_each_other():
    a = run(change(feeling="happy", incident="exam"))
    both = run(change(feeling="happy", incident="exam"),
               change(event="m9", feeling="happy", incident="baby", at=1.0))
    gain_a = a.values["mood"] - DYN.baseline["mood"]
    assert both.values["mood"] - a.values["mood"] == pytest.approx(gain_a, rel=0.05)


def test_easing_takes_back_only_what_its_incident_left():
    s = run(change(amount="strong"))
    eased = apply(s, change(event="m7", direction=-1, amount="very", status="eased", at=60.0), DYN).state
    assert eased.values["tension"] == pytest.approx(DYN.baseline["tension"], abs=1e-6)


def test_an_apology_eases_the_hurt_and_can_open_a_separate_warm_feeling():
    s = run(change(amount="strong"))
    after = run(change(event="m7", direction=-1, amount="very", status="eased", at=60.0),
                change(event="m7", feeling="touched", amount="a_little", incident="apology", at=60.0), state=s)
    assert after.values["tension"] == pytest.approx(DYN.baseline["tension"], abs=1e-6)
    assert after.values["closeness"] > fade(s, DYN, 60.0).values["closeness"]


def test_easing_an_unknown_incident_does_nothing():
    s = run(change())
    out = apply(s, change(event="m9", direction=-1, status="eased", incident="i2", at=60.0), DYN)
    assert not out.applied and out.reason == "nothing_to_take_back"


def test_appraisal_v1_statuses_still_work():
    s = run(change(status="new", incident="m6"))
    assert apply(s, change(event="m7", status="continuing", incident="m6", at=1.0), DYN).reason == "lingering"
    eased = apply(s, change(event="m8", direction=-1, amount="very", status="reinterpreted", incident="m6", at=2.0), DYN)
    assert eased.applied and eased.state.values["tension"] == pytest.approx(DYN.baseline["tension"], abs=1e-6)


def test_a_late_event_is_weakened_by_its_age():
    s = start(DYN, 0.0)
    s.at = 600.0
    on_time = run(change(at=600.0), state=start(DYN, 600.0))
    late = run(change(at=0.0), state=s)
    assert 0 < late.values["tension"] - DYN.baseline["tension"] < on_time.values["tension"] - DYN.baseline["tension"]


def test_a_very_late_event_is_dropped_for_fast_values():
    s = start(DYN, 0.0)
    s.at = 3 * 3600.0
    assert run(change(at=0.0), state=s).values["tension"] == pytest.approx(DYN.baseline["tension"])


def test_values_stay_in_range_and_return_to_baseline():
    s = run(*(change(event=f"m{i}", amount="very", incident=f"i{i}", at=float(i)) for i in range(20)))
    assert all(-1.0 <= s.values[k] <= 1.0 for k in KEYS) and 0.0 <= s.values["tension"] <= 1.0
    later = fade(s, DYN, 3 * 24 * 3600.0)
    for k in ("mood", "tension", "energy", "interest"):
        assert later.values[k] == pytest.approx(DYN.baseline[k], abs=1e-3)
