"""Measure GPT-SoVITS v1 inference speed for the Columbina voice on this machine.

Answers one question: can this run the realtime voice loop, or does it need to be
a non-realtime backend behind a cloud TTS?

Two numbers matter:
  RTF          - compute seconds per audio second. <1.0 means synthesis outruns
                 playback, so sentence-chunked streaming builds a buffer. >1.0
                 means playback catches up and long replies stutter.
  first-chunk  - wall time until the first audio bytes exist. This is what the
                 user actually experiences as "lag" before the voice starts.

Usage:  .venv/bin/python bench_rtf.py [cpu|mps]
"""

import os
import sys
import time
import statistics

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))  # resolve before chdir
REPO = os.path.join(SCRIPT_DIR, "GPT-SoVITS")
VOICE = "/Users/vincentgilbert/Desktop/NutritionDetection/app/backend/app/ai/voice"

# The repo resolves config/model paths relative to its own root.
os.chdir(REPO)
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "GPT_SoVITS"))

GPT_CKPT = os.path.join(VOICE, "columbina-e15-2.ckpt")
SOVITS_PTH = os.path.join(VOICE, "columbina_e8_s488.pth")
REF_WAV = os.path.join(VOICE, "ref_columbina_6s.wav")
REF_TXT = os.path.join(VOICE, "ref_columbina_6s.txt")

# Representative of what a nutrition assistant actually says out loud.
SENTENCES = [
    "About four hundred and twenty calories.",
    "That looks like grilled chicken with rice and broccoli.",
    "I'd estimate five hundred and forty calories, with forty grams of protein and twelve grams of fat.",
    "You're about three hundred calories under your target for today, so there's room for a snack if you want one.",
]


def main() -> int:
    device = sys.argv[1] if len(sys.argv) > 1 else "cpu"

    for p in (GPT_CKPT, SOVITS_PTH, REF_WAV, REF_TXT):
        if not os.path.exists(p):
            print(f"MISSING: {p}")
            return 1
    prompt_text = open(REF_TXT).read().strip()

    # torchaudio 2.11 removed its native backends and delegates to torchcodec,
    # which needs FFmpeg system libs that aren't installed. The reference clip is
    # plain PCM wav, so route loads through soundfile instead. Returns torchaudio's
    # convention: float32 tensor shaped [channels, samples], plus sample rate.
    import soundfile as sf
    import torch
    import torchaudio

    def _load_via_soundfile(path, *args, **kwargs):
        data, sr = sf.read(str(path), dtype="float32", always_2d=True)
        return torch.from_numpy(data.T.copy()), sr

    torchaudio.load = _load_via_soundfile

    from TTS_infer_pack.TTS import TTS, TTS_Config

    cfg = TTS_Config(
        {
            "custom": {
                "device": device,
                "is_half": False,
                "version": "v1",
                "t2s_weights_path": GPT_CKPT,
                "vits_weights_path": SOVITS_PTH,
                "bert_base_path": "GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large",
                "cnhuhbert_base_path": "GPT_SoVITS/pretrained_models/chinese-hubert-base",
            }
        }
    )

    # TTS_Config silently falls back to stock weights if a path is bad -- that
    # would benchmark a model that isn't Columbina, so fail loudly instead.
    assert cfg.t2s_weights_path == GPT_CKPT, f"GPT weights fell back to {cfg.t2s_weights_path}"
    assert cfg.vits_weights_path == SOVITS_PTH, f"SoVITS weights fell back to {cfg.vits_weights_path}"

    print(f"device={cfg.device}  version={cfg.version}  is_half={cfg.is_half}")

    t0 = time.perf_counter()
    pipeline = TTS(cfg)
    load_s = time.perf_counter() - t0
    print(f"model load: {load_s:.1f}s\n")

    def base(text):
        return {
            "text": text,
            "text_lang": "en",
            "ref_audio_path": REF_WAV,
            "prompt_text": prompt_text,
            "prompt_lang": "en",
            "text_split_method": "cut0",
            "batch_size": 1,
            "parallel_infer": False,
        }

    # First run pays lazy init + allocator warmup; don't let it pollute the stats.
    print("warmup...", flush=True)
    for sr, _ in pipeline.run(base("Warming up the model.")):
        pass

    results = {"device": str(cfg.device), "version": cfg.version, "load_s": load_s}

    print(f"\n{'sentence':<58} {'audio':>7} {'wall':>7} {'RTF':>6}")
    print("-" * 82)
    rtfs, durs, walls = [], [], []
    for s in SENTENCES:
        t = time.perf_counter()
        chunks, sr = [], None
        for sr, audio in pipeline.run(base(s)):
            chunks.append(audio)
        wall = time.perf_counter() - t
        dur = sum(len(c) for c in chunks) / sr
        rtf = wall / dur if dur else float("nan")
        rtfs.append(rtf)
        durs.append(dur)
        walls.append(wall)
        label = s if len(s) <= 56 else s[:53] + "..."
        print(f"{label:<58} {dur:6.2f}s {wall:6.2f}s {rtf:6.2f}")

    print("-" * 82)
    print(f"median RTF: {statistics.median(rtfs):.2f}   mean RTF: {statistics.mean(rtfs):.2f}")
    results["batch"] = [
        {"text": s, "audio_s": d, "wall_s": w, "rtf": r}
        for s, d, w, r in zip(SENTENCES, durs, walls, rtfs)
    ]
    results["median_rtf"] = statistics.median(rtfs)

    # Streaming: the metric that actually maps to perceived latency.
    results["streaming"] = []
    print("\ntime-to-first-audio-chunk (streaming_mode):")
    for s in SENTENCES[:3]:
        inp = base(s)
        inp.update({"streaming_mode": True, "min_chunk_length": 16})
        t = time.perf_counter()
        first = None
        total = 0
        sr = None
        for sr, audio in pipeline.run(inp):
            if first is None:
                first = time.perf_counter() - t
            total += len(audio)
        wall = time.perf_counter() - t
        dur = total / sr if sr else 0
        label = s if len(s) <= 50 else s[:47] + "..."
        print(f"  {label:<50} first={first:5.2f}s  full={wall:5.2f}s  audio={dur:5.2f}s")
        results["streaming"].append(
            {"text": s, "first_chunk_s": first, "wall_s": wall, "audio_s": dur}
        )

    # stdout is drowned in tqdm output; the JSON is the artifact worth reading.
    import json

    out = os.path.join(SCRIPT_DIR, f"results_{device}.json")
    with open(out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nwrote {out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
