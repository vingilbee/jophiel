"""``desktop-profile-check``: validate profile files (a plan §16 developer tool)."""

from __future__ import annotations

import sys
from pathlib import Path

from .profile import ProfileError, load_profile


def profile_check(argv: list[str] | None = None) -> int:
    paths = argv if argv is not None else sys.argv[1:]
    if not paths:
        print("usage: desktop-profile-check PROFILE.yaml [...]", file=sys.stderr)
        return 2
    failed = 0
    for path in paths:
        try:
            profile = load_profile(Path(path))
        except ProfileError as exc:
            failed += 1
            print(f"FAIL {path}")
            for issue in exc.issues:
                print(f"  - {issue}")
        except (OSError, ValueError) as exc:
            failed += 1
            print(f"FAIL {path}\n  - {exc}")
        else:
            print(f"ok   {path}  ({profile.id} r{profile.revision}: "
                  f"{len(profile.buttons)} buttons, {len(profile.axes)} axes, "
                  f"contexts {sorted(profile.contexts.modes)})")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(profile_check())
