"""The layered inner state (app/ai/mind/mood_layers.py): brief reactions fade fast, only a share of
a shift lasts, mood saturates softly but still responds, and the inner_state guarantees still hold."""
import pytest

from app.ai.mind.mood_layers import Config, Shift, apply, fade, start, values

BASE = dict(mood=0.3, energy=0.55, interest=0.6, tension=0.15, closeness=0.1)
CFG = Config(baseline=BASE, half_life_minutes=dict(mood=30, energy=60, interest=60, tension=10, closeness=1440))


def shift(event="m1", feeling="happy", status="opened", size="clear", incident="i1", at=0.0):
    return Shift("chat", "c1", event, incident, feeling, status, size, at)


def run(*shifts, state=None, cfg=CFG):
    s = state or start(0.0)
    for x in shifts:
        s = apply(s, x, cfg).state
    return s


def mood(s, at=None, cfg=CFG):
    return values(fade(s, cfg, at) if at is not None else s, cfg)["mood"]


def test_a_duplicate_applies_once():
    once = run(shift())
    again = apply(once, shift(), CFG)
    assert not again.applied and again.reason == "duplicate"


def test_a_reaction_fades_fast_and_only_part_lasts():
    s = run(shift(size="small"))
    right_after = mood(s) - BASE["mood"]
    later = mood(s, 15 * 60) - BASE["mood"]     # 5 reaction half-lives, half a mood half-life
    assert right_after > 0 and 0 < later < right_after * 0.4


def test_a_big_shift_lasts_more_than_a_small_one():
    small, big = run(shift(size="small")), run(shift(size="big"))
    assert mood(big, 15 * 60) - BASE["mood"] > 3 * (mood(small, 15 * 60) - BASE["mood"])


def test_mood_never_reaches_the_limit_and_a_second_big_event_still_shows():
    s = run(*(shift(event=f"m{i}", incident=f"i{i}", size="big", at=float(i)) for i in range(12)))
    top = mood(s)
    assert top < 1.0
    after = mood(run(shift(event="x", incident="news", size="big", at=12.0), state=s))
    assert after > top


def test_ordinary_small_moments_stay_modest():
    s = run(*(shift(event=f"m{i}", incident=f"i{i}", size="small", at=60.0 * i) for i in range(10)))
    assert mood(s) < 0.75


def test_unclear_size_follows_the_assumption():
    skip = Config(**{**CFG.__dict__, "unclear_amount": None})
    assert values(run(shift(size="unclear"), cfg=skip), skip)["mood"] == pytest.approx(BASE["mood"])
    assert mood(run(shift(size="unclear"))) > BASE["mood"]


def test_lingering_adds_nothing_and_sustained_holds_without_raising():
    s = run(shift(size="clear"))
    linger = run(shift(event="m2", status="lingering", size="-", at=600.0), state=s)
    assert mood(linger) == pytest.approx(mood(s, 600.0))
    held = run(shift(event="m2", status="sustained", size="-", at=600.0), state=s)
    assert mood(s, 600.0) < mood(held) <= mood(s) + 1e-9


def test_easing_takes_back_its_own_incident_only():
    hurt = run(shift(feeling="hurt", size="clear"), shift(event="m2", feeling="happy", incident="i2", size="clear"))
    eased = run(shift(event="m3", feeling="hurt", status="eased", size="big", at=60.0), state=hurt)
    v = values(eased, CFG)
    assert v["tension"] == pytest.approx(BASE["tension"], abs=1e-6)
    assert v["mood"] > BASE["mood"]          # the happy incident is untouched


def test_an_incident_cap_makes_repeats_in_one_concern_count_less():
    capped = Config(**{**CFG.__dict__, "incident_cap": 0.15})
    one_incident = run(*(shift(event=f"m{i}", status="opened" if i == 0 else "escalated", size="clear", at=float(i))
                         for i in range(6)), cfg=capped)
    separate = run(*(shift(event=f"m{i}", incident=f"i{i}", size="clear", at=float(i)) for i in range(6)), cfg=capped)
    assert values(fade(one_incident, capped, 900.0), capped)["mood"] < values(fade(separate, capped, 900.0), capped)["mood"]


def test_fading_does_not_depend_on_polling():
    s = run(shift(size="big"))
    jump = values(fade(s, CFG, 3600.0), CFG)
    stepped = s
    for t in range(60, 3601, 60):
        stepped = fade(stepped, CFG, float(t))
    for k, v in values(stepped, CFG).items():
        assert v == pytest.approx(jump[k])


def test_a_late_event_brings_a_faded_reaction_but_its_lasting_share():
    on_time = run(shift(size="big", at=600.0), state=start(600.0))
    late_state = start(0.0)
    late_state.at = 600.0                      # the state has moved on; the event happened at 0
    late = run(shift(size="big", at=0.0), state=late_state)
    from app.ai.mind.mood_layers import reaction
    # Ten minutes is several reaction half-lives: little of the brief reaction is left...
    assert reaction(late, "mood") < 0.15 * reaction(on_time, "mood")
    # ...but what lasts is there, aged on the mood's own clock (10 of its 30 minutes).
    late_share = late.incidents["chat:c1:i1"]["mood"]
    assert late_share == pytest.approx(on_time.incidents["chat:c1:i1"]["mood"] * 0.5 ** (10 / 30), rel=1e-6)


def test_repair_only_calms_its_own_incidents_reaction():
    old_hurt = run(shift(feeling="hurt", size="clear", incident="old"))
    later = fade(old_hurt, CFG, 3600.0)     # the old hurt's reaction is long gone
    new_hurt = run(shift(event="m5", feeling="hurt", size="clear", incident="new", at=3600.0), state=later)
    before = values(new_hurt, CFG)["tension"]
    apology_for_old = run(shift(event="m6", feeling="hurt", status="eased", size="big", incident="old", at=3600.0),
                          state=new_hurt)
    after = values(apology_for_old, CFG)["tension"]
    lasting_old = values(later, CFG)["tension"] - BASE["tension"]
    # Only the old incident's remaining share goes; the new incident's fresh reaction stays.
    assert before - after == pytest.approx(lasting_old, abs=0.01)


def test_state_round_trips_through_a_dict():
    from app.ai.mind.mood_layers import State
    s = run(shift(), shift(event="m2", feeling="hurt", incident="i2", at=30.0))
    assert values(State.from_dict(s.to_dict()), CFG) == values(s, CFG)


def test_negativity_bias_makes_hurt_last_longer_than_an_equal_joy():
    biased = Config(**{**CFG.__dict__, "negative_keep_scale": 2.0})
    joy = run(shift(feeling="happy", size="clear"), cfg=biased)
    hurt = run(shift(feeling="hurt", size="clear"), cfg=biased)
    joy_left = values(fade(joy, biased, 900.0), biased)["mood"] - BASE["mood"]
    hurt_left = BASE["mood"] - values(fade(hurt, biased, 900.0), biased)["mood"]
    # hurt moves mood 0.4 per unit, happy 0.5: compare per unit of effect
    assert hurt_left / 0.4 > 1.5 * joy_left / 0.5
