# desktop_controller

The game-agnostic action contract for Columbina's desktop agent, and its enforcement.
Spec: `docs/DESKTOP_AGENT_IMPLEMENTATION_PLAN.md` §7.

This package is standalone (only `pydantic` + `pyyaml`; a uv workspace member of
`backend/`, which it lives inside so the backend Docker image can build it) because the
same code runs in two places: inside the game VM as the controller, and in the backend,
which validates decisions before sending them. The controller re-validates everything
itself, so a backend bug can never bypass the safety rules.

| Module | What it does |
|---|---|
| `protocol.py` | Wire models: `Decision` (§7.2), `StepOutput` (§7.2.1), `Rejection` codes (§7.9) |
| `profile.py` | Game profile model, loader, and every profile check (§7.3-7.4) |
| `safety.py` | Global rules no profile can override: key vocabulary, blocked OS keys and combinations, ceilings (§7.5) |
| `validate.py` | A decision against the profile, the current context and the frame |
| `geometry.py` | Frame/crop/viewport mapping, `look_at` angles, axis units |
| `until.py` | Local evaluation of `until` conditions from region-change ratios |
| `scheduler.py` | The controller core: timed input, supersede-by-diff, expiry, lease, generation fence, dedupe |
| `fake.py` | Manual clock + recording backend: the real scheduler with no OS (stage A "fake controller") |
| `cli.py` | `desktop-profile-check` |

Profiles live in `../desktop_profiles/`.

From `backend/`:

```bash
uv sync
uv run pytest desktop_controller/tests                 # these tests only, no VM or game needed
uv run desktop-profile-check desktop_profiles/*.yaml   # check profiles
```

In the guest VM, install it on its own: `pip install ./desktop_controller`.

`generic_keyboard_mouse.yaml` is a template and fails the check until its
`target.process` is set to a real game executable.

## Not here yet

- OS input drivers (`SendInput` on Windows) and screen capture: stage C. The scheduler
  takes any `InputBackend`, so a driver plugs in without touching the core.
- Turning pixels into the region-change ratios `until.py` consumes: stage C capture.
- The relay connection, pairing, and TLS: stage B.
- Macros and typing: refused by the profile checker until designed.
