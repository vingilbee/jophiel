Live copies of her character, read by the running backend (`services/cognitive_live.py`): the
Docker image builds from `backend/` only, so it can't read `docs/`. Sources of truth:

- `columbina.md`: the body of `docs/characters/columbina-v0.6.md` (from "**In one line:**" on)
- `columbina.world.json`: `docs/characters/columbina-teacher-2026-10-19.world.json`
- `columbina.mood.json`: `docs/characters/columbina.mood.json`

`tests/test_cognitive_live.py` fails if they drift apart.
