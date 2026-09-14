"""Pre-render short Columbina clips that mask TTS onset latency.

Measured time-to-first-audio-chunk is ~1.9s and flat regardless of sentence
length -- it's fixed setup cost, so streaming doesn't help short replies. Playing
a cached ~1s filler the instant the user stops talking covers that gap, and since
RTF is 0.63 the real audio is ready before the filler ends and stays ahead from
then on.

Written into the backend's own tree so fillers still work when the sidecar is
cold or down. Run once with the sidecar up:

    .venv/bin/python render_fillers.py
"""

from __future__ import annotations

import json
import os
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.normpath(
    os.path.join(SCRIPT_DIR, "..", "backend", "app", "ai", "voice", "fillers")
)
SIDECAR = os.environ.get("TTS_SIDECAR_URL", "http://localhost:8787")

# Short, content-free, and safe to precede any answer. Keep them under ~1.5s:
# a filler longer than the thing it's hiding just adds latency of its own.
FILLERS = {
    # Measured first-chunk latency is ~1.9s, so a filler needs to be ~1.5s+ to
    # actually cover it. The short ones below are kept because a small trailing
    # pause reads as thinking, but pick_filler prefers the long ones.
    "let_me_have_a_look": "Let me have a look at that.",
    "one_moment_please": "One moment, let me check.",
    "let_me_think": "Let me think about that.",
    "lets_see_now": "Let's see now.",
    "let_me_see": "Let me see.",
    "one_moment": "One moment.",
    "alright": "Alright.",
    "okay": "Okay.",
}


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    manifest = []

    for name, text in FILLERS.items():
        payload = json.dumps({"text": text, "streaming": False}).encode()
        req = urllib.request.Request(
            f"{SIDECAR}/tts.wav",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                wav = resp.read()
        except Exception as e:
            print(f"FAIL {name}: {e}")
            return 1

        path = os.path.join(OUT_DIR, f"{name}.wav")
        with open(path, "wb") as f:
            f.write(wav)

        # 44-byte canonical wav header, 16-bit mono @ 32kHz.
        dur = (len(wav) - 44) / 2 / 32000
        manifest.append({"name": name, "text": text, "file": f"{name}.wav", "seconds": round(dur, 2)})
        print(f"  {name:<14} {dur:4.2f}s  {text}")

    with open(os.path.join(OUT_DIR, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"\nwrote {len(manifest)} fillers -> {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
