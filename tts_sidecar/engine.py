# """Zero-shot Columbina voice via GPT-SoVITS v4 base weights.

# No fine-tuning: v4 leans much more heavily on the reference audio itself than
# v1/v2 did, so a clean 15-20s reference clip gets you most of what fine-tuning
# was trying to buy -- without the training step that was producing static.

# Shared by server.py (the HTTP sidecar) and render_fillers.py.

# Importing this module chdir()s into the GPT-SoVITS repo: the upstream code
# resolves config and pretrained-model paths relative to its own root, so
# there's no supported way to drive it from another working directory.
# """

# from __future__ import annotations

# import os
# import sys
# from typing import Iterator

# import numpy as np

# SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))  # resolve before chdir
# REPO = os.path.join(SCRIPT_DIR, "GPT-SoVITS")
# VOICE = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "backend", "app", "ai", "voice"))

# REF_WAV = os.path.join(VOICE, "ref_columbina_6s.wav")
# REF_TXT = os.path.join(VOICE, "ref_columbina_6s.txt")

# # v4 base pretrained weights (not a fine-tuned checkpoint). The GPT/T2S stage
# # is shared with v3 -- only the VITS/vocoder stage is v4-specific.
# T2S_BASE = os.path.join(REPO, "GPT_SoVITS", "pretrained_models", "s1v3.ckpt")
# VITS_BASE = os.path.join(
#     REPO, "GPT_SoVITS", "pretrained_models", "gsv-v4-pretrained", "s2Gv4.pth"
# )

# # v4 natively outputs 48kHz; resample down to match tts_sample_rate in the
# # backend's config (32000) so server.py/tts.py don't need to know v4 changed.
# TARGET_SAMPLE_RATE = 32000

# os.chdir(REPO)
# sys.path.insert(0, REPO)
# sys.path.insert(0, os.path.join(REPO, "GPT_SoVITS"))


# def _install_soundfile_shim() -> None:
#     """Route torchaudio.load through soundfile.

#     torchaudio 2.11 removed its native backends and delegates to torchcodec,
#     which needs FFmpeg system libraries. The reference clip is plain PCM wav, so
#     soundfile (already present via librosa) reads it without that dependency.
#     """
#     import soundfile as sf
#     import torch
#     import torchaudio

#     def _load(path, *args, **kwargs):
#         data, sr = sf.read(str(path), dtype="float32", always_2d=True)
#         return torch.from_numpy(data.T.copy()), sr  # [channels, samples]

#     torchaudio.load = _load


# def load_pipeline(device: str = "cpu"):
#     """Build the TTS pipeline from v4 base weights. Slow (~2-3s); call once
#     per process."""
#     for p in (T2S_BASE, VITS_BASE, REF_WAV):
#         if not os.path.exists(p):
#             raise FileNotFoundError(p)

#     _install_soundfile_shim()
#     from TTS_infer_pack.TTS import TTS, TTS_Config

#     cfg = TTS_Config(
#         {
#             "custom": {
#                 "device": device,
#                 "is_half": False,
#                 "version": "v4",
#                 "t2s_weights_path": T2S_BASE,
#                 "vits_weights_path": VITS_BASE,
#                 "bert_base_path": "GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large",
#                 "cnhuhbert_base_path": "GPT_SoVITS/pretrained_models/chinese-hubert-base",
#             }
#         }
#     )
#     # TTS_Config silently falls back to stock v2 weights when a path is bad,
#     # which would serve the wrong version/voice. Fail loudly instead.
#     if cfg.t2s_weights_path != T2S_BASE:
#         raise RuntimeError(f"GPT weights fell back to {cfg.t2s_weights_path}")
#     if cfg.vits_weights_path != VITS_BASE:
#         raise RuntimeError(f"SoVITS weights fell back to {cfg.vits_weights_path}")

#     return TTS(cfg)


# def prompt_text() -> str:
#     """Transcript of REF_WAV. Optional for v4 zero-shot (empty string works),
#     but supplying it improves alignment quality -- write it once by hand."""
#     if os.path.exists(REF_TXT):
#         with open(REF_TXT) as f:
#             return f.read().strip()
#     return ""


# def _resample(arr: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
#     if src_sr == dst_sr:
#         return arr
#     import librosa

#     float_audio = arr.astype(np.float32) / 32767.0
#     resampled = librosa.resample(float_audio, orig_sr=src_sr, target_sr=dst_sr)
#     return (np.clip(resampled, -1.0, 1.0) * 32767.0).astype(np.int16)


# def synthesize(pipeline, text: str, streaming: bool = True) -> Iterator[tuple[int, np.ndarray]]:
#     """Yield (sample_rate, int16 mono PCM) chunks for `text`, resampled to
#     TARGET_SAMPLE_RATE."""
#     inputs = {
#         "text": text,
#         "text_lang": "en",
#         "ref_audio_path": REF_WAV,
#         "prompt_text": prompt_text(),
#         "prompt_lang": "en",
#         "text_split_method": "cut5",
#         "batch_size": 1,
#         "parallel_infer": False,
#         "streaming_mode": streaming,
#         "min_chunk_length": 16,
#         # v4-specific: CFM sampling steps for the flow-matching vocoder stage.
#         "sample_steps": 32,
#     }
#     for sr, audio in pipeline.run(inputs):
#         if audio is None or len(audio) == 0:
#             continue
#         arr = np.asarray(audio)
#         if arr.dtype != np.int16:
#             arr = np.clip(arr, -1.0, 1.0)
#             arr = (arr * 32767.0).astype(np.int16)
#         yield TARGET_SAMPLE_RATE, _resample(arr, sr, TARGET_SAMPLE_RATE)


# def warm(pipeline) -> None:
#     """First run pays lazy init; burn it at startup, not on a user's turn."""
#     for _ in synthesize(pipeline, "Warming up.", streaming=False):
#         pass


"""Columbina voice, switchable between four GPT-SoVITS setups for A/B testing.

Select with the TTS_MODE env var:

    TTS_MODE=finetune  -- your fine-tuned v2Pro checkpoint (Nov 2026)
    TTS_MODE=v2pro      -- zero-shot on v2Pro base weights (default; the repo's
                            own docs recommend v2Pro for "best compatibility
                            and stability")
    TTS_MODE=v4         -- zero-shot on v4 base weights (most reference-
                            faithful, but heavier CFM vocoder -- slower on
                            CPU, per the earlier RTF discussion)
    TTS_MODE=v5         -- zero-shot on V5 base weights (Oct 2026: better
                            similarity without fine-tuning). TTS_V5_VARIANT=turbo
                            (default, 4 steps, RTF ~0.30 on the RTX 5070
                            Laptop) or dev (32 steps, RTF ~0.65)

    .venv/bin/uvicorn server:app --port 8787                         # v2pro
    TTS_MODE=v4 .venv/bin/uvicorn server:app --port 8787
    TTS_MODE=finetune .venv/bin/uvicorn server:app --port 8787
    TTS_MODE=v5 .venv/bin/uvicorn server:app --port 8787             # v5 turbo
    TTS_MODE=v5 TTS_V5_VARIANT=dev .venv/bin/uvicorn server:app --port 8787

All modes share the Nov 2026 reference clip (ref_columbina_v5.wav): the voice
changed with the new recordings, so the old 6s clip was retired.

TTS_DEVICE defaults to "auto": cuda with fp16 on an NVIDIA machine, cpu
otherwise (see resolve_device / resolve_is_half).

Only one mode is loaded per process -- restart the sidecar to switch. They
don't stack in memory, so keeping all three sets of weights on disk doesn't
cost you anything at runtime, just some extra disk space.

Shared by server.py (the HTTP sidecar) and render_fillers.py.

Importing this module chdir()s into the GPT-SoVITS repo: the upstream code
resolves config and pretrained-model paths relative to its own root, so
there's no supported way to drive it from another working directory.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from typing import Iterator

import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))  # resolve before chdir
REPO = os.path.join(SCRIPT_DIR, "GPT-SoVITS")
VOICE = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "backend", "app", "ai", "voice"))
PRETRAINED = os.path.join(REPO, "GPT_SoVITS", "pretrained_models")

MODE = os.environ.get("TTS_MODE", "v2pro").lower()

# --- Per-mode weight paths and reference clips -----------------------------

_MODES = {
    "finetune": {
        # v2Pro fine-tune (Nov 2026): 148 UVR5-cleaned clips, GPT 15 epochs,
        # SoVITS 8 epochs. Replaced the old v1 fine-tune.
        "version": "v2Pro",
        "t2s_weights_path": os.path.join(VOICE, "columbina_v2pro-e15.ckpt"),
        "vits_weights_path": os.path.join(VOICE, "columbina_v2pro_e8_s304.pth"),
        # One of the training clips, so the ref matches what the model learned.
        "ref_wav": os.path.join(VOICE, "ref_columbina_v5.wav"),
        "ref_txt": os.path.join(VOICE, "ref_columbina_v5.txt"),
        "native_sample_rate": None,  # v2Pro outputs 32k already; no resample
    },
    "v2pro": {
        "version": "v2Pro",
        "t2s_weights_path": os.path.join(PRETRAINED, "s1v3.ckpt"),
        "vits_weights_path": os.path.join(PRETRAINED, "v2Pro", "s2Gv2Pro.pth"),
        "ref_wav": os.path.join(VOICE, "ref_columbina_v5.wav"),
        "ref_txt": os.path.join(VOICE, "ref_columbina_v5.txt"),
        "native_sample_rate": None,
    },
    "v4": {
        "version": "v4",
        "t2s_weights_path": os.path.join(PRETRAINED, "s1v3.ckpt"),
        "vits_weights_path": os.path.join(PRETRAINED, "gsv-v4-pretrained", "s2Gv4.pth"),
        "ref_wav": os.path.join(VOICE, "ref_columbina_v5.wav"),
        "ref_txt": os.path.join(VOICE, "ref_columbina_v5.txt"),
        "native_sample_rate": 48000,  # v4 outputs 48k; resample to match config
    },
    "v5": {
        # v5turbo/v5dev share s1v3 and the v5 vocoder; only the SoVITS weights differ.
        "version": "v5" + os.environ.get("TTS_V5_VARIANT", "turbo").lower(),
        "t2s_weights_path": os.path.join(PRETRAINED, "s1v3.ckpt"),
        "vits_weights_path": os.path.join(
            PRETRAINED, "gsv-v5-pretrained",
            "s2Gv5" + os.environ.get("TTS_V5_VARIANT", "turbo").lower() + ".pth",
        ),
        # Clean (UVR5 de-musicked) 6.7s clip from the Nov 2026 recordings.
        "ref_wav": os.path.join(VOICE, "ref_columbina_v5.wav"),
        "ref_txt": os.path.join(VOICE, "ref_columbina_v5.txt"),
        "native_sample_rate": 48000,
        # None lets upstream pick: 4 for v5turbo, 32 for v5dev.
        "sample_steps": None,
    },
}

if MODE not in _MODES:
    raise ValueError(f"TTS_MODE={MODE!r} not recognized. Use one of {list(_MODES)}.")

_CFG = _MODES[MODE]
REF_WAV = _CFG["ref_wav"]
REF_TXT = _CFG["ref_txt"]

# Everything downstream (server.py, tts.py, the frontend) expects this rate.
TARGET_SAMPLE_RATE = 32000
# Streaming chunk size in semantic tokens (~20ms of audio each).
STREAM_MIN_CHUNK_LENGTH = int(os.environ.get("TTS_STREAM_MIN_CHUNK_LENGTH", "16"))
# Upstream GPT-SoVITS only cuts a streaming chunk where the voice pauses, so a
# clause with no internal pause comes back all at once: first audio ~= full
# synthesis time (measured 0.8-1.5s on CPU, finetune mode). Fixed-length
# chunking cuts every STREAM_MIN_CHUNK_LENGTH tokens instead: first audio
# ~0.5s at 16 tokens (~0.3s at 8). Upstream labels it lower quality at the
# seams; at 16 tokens no seams were audible in an A/B listening test, while 8
# sounded worse. Set TTS_STREAM_FIXED_LENGTH_CHUNK=false to restore
# pause-only cuts.
STREAM_FIXED_LENGTH_CHUNK = os.environ.get("TTS_STREAM_FIXED_LENGTH_CHUNK", "true").lower() in {"1", "true", "yes"}


def resolve_device(requested: str | None = None) -> str:
    """Turn TTS_DEVICE into a concrete torch device.

    "auto" (the default) picks cuda when an NVIDIA GPU is usable, else cpu.
    It deliberately never picks mps: on Apple silicon the GPU benchmarked ~2x
    slower than the CPU (results_mps.json vs results_cpu.json). Set
    TTS_DEVICE=mps|cpu|cuda to force one.
    """
    device = (requested or os.environ.get("TTS_DEVICE", "auto")).lower()
    if device != "auto":
        return device
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


def resolve_is_half(device: str) -> bool:
    """fp16 halves VRAM and speeds up inference on CUDA; CPU/MPS stay fp32.

    Override with TTS_IS_HALF=true|false.
    """
    value = os.environ.get("TTS_IS_HALF")
    if value is not None:
        return value.lower() in {"1", "true", "yes"}
    return device.startswith("cuda")


os.chdir(REPO)
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "GPT_SoVITS"))


def _install_soundfile_shim() -> None:
    """Route torchaudio.load through soundfile.

    torchaudio 2.11 removed its native backends and delegates to torchcodec,
    which needs FFmpeg system libraries. The reference clip is plain PCM wav, so
    soundfile (already present via librosa) reads it without that dependency.
    """
    import soundfile as sf
    import torch
    import torchaudio

    def _load(path, *args, **kwargs):
        data, sr = sf.read(str(path), dtype="float32", always_2d=True)
        return torch.from_numpy(data.T.copy()), sr  # [channels, samples]

    torchaudio.load = _load


def load_pipeline(device: str | None = None):
    """Build the TTS pipeline for the selected TTS_MODE. Slow (~2-3s); call
    once per process. ``device`` defaults to TTS_DEVICE (see resolve_device)."""
    device = resolve_device(device)
    is_half = resolve_is_half(device)
    print(f"[tts] mode={MODE} version={_CFG['version']} device={device} is_half={is_half}")
    for key in ("t2s_weights_path", "vits_weights_path"):
        p = _CFG[key]
        if not os.path.exists(p):
            raise FileNotFoundError(
                f"{key} not found: {p} (TTS_MODE={MODE})"
            )
    if not os.path.exists(REF_WAV):
        raise FileNotFoundError(f"reference clip not found: {REF_WAV}")

    _install_soundfile_shim()
    from TTS_infer_pack.TTS import TTS, TTS_Config

    cfg = TTS_Config(
        {
            "custom": {
                "device": device,
                "is_half": is_half,
                "version": _CFG["version"],
                "t2s_weights_path": _CFG["t2s_weights_path"],
                "vits_weights_path": _CFG["vits_weights_path"],
                "bert_base_path": "GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large",
                "cnhuhbert_base_path": "GPT_SoVITS/pretrained_models/chinese-hubert-base",
                # v2Pro/v2ProPlus need a speaker-verification embedding model.
                # TTS_Config falls back to its own per-version default path
                # (pretrained_models/sv/pretrained_eres2netv2w24s4ep4.ckpt) if
                # this key is omitted -- only set it if that default is wrong
                # for your layout.
            }
        }
    )
    # TTS_Config silently falls back to stock weights when a path is bad,
    # which would silently serve the wrong voice/version. Fail loudly instead.
    if cfg.t2s_weights_path != _CFG["t2s_weights_path"]:
        raise RuntimeError(f"GPT weights fell back to {cfg.t2s_weights_path}")
    if cfg.vits_weights_path != _CFG["vits_weights_path"]:
        raise RuntimeError(f"SoVITS weights fell back to {cfg.vits_weights_path}")

    return TTS(cfg)


@lru_cache(maxsize=1)
def prompt_text() -> str:
    """Read the fixed reference transcript once per sidecar process."""
    if os.path.exists(REF_TXT):
        with open(REF_TXT) as f:
            return f.read().strip()
    return ""


def _resample(arr: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    if src_sr == dst_sr:
        return arr
    import librosa

    float_audio = arr.astype(np.float32) / 32767.0
    resampled = librosa.resample(float_audio, orig_sr=src_sr, target_sr=dst_sr)
    return (np.clip(resampled, -1.0, 1.0) * 32767.0).astype(np.int16)


def synthesize(pipeline, text: str, streaming: bool = True) -> Iterator[tuple[int, np.ndarray]]:
    """Yield (sample_rate, int16 mono PCM) chunks for `text`."""
    inputs = {
        "text": text,
        "text_lang": "en",
        # After startup preparation, None tells GPT-SoVITS to consume its
        # resident prompt_cache directly. Passing the same path also hits the
        # upstream cache, but making this explicit prevents future path/string
        # changes from silently re-running HuBERT and spectrogram extraction.
        "ref_audio_path": (
            None if getattr(pipeline, "_macrovision_reference_cached", False)
            else REF_WAV
        ),
        "prompt_text": prompt_text(),
        "prompt_lang": "en",
        # cut5 = split on punctuation with length limits -- resets AR drift
        # regularly instead of letting one long unbroken sequence degrade.
        "text_split_method": "cut5",
        "batch_size": 1,
        "parallel_infer": False,
        "streaming_mode": streaming,
        "min_chunk_length": STREAM_MIN_CHUNK_LENGTH,
        "fixed_length_chunk": STREAM_FIXED_LENGTH_CHUNK,
        # Only meaningful for v3/v4/v5's CFM vocoder stage; harmless no-op on v1/v2Pro.
        "sample_steps": _CFG.get("sample_steps", 32),
        # Upstream's CUDA Graph AR accelerator (on by default since the V5
        # release) re-captures for every new sentence length: ~20s stall per
        # new utterance on the RTX 5070 Laptop. The plain AR path is ~1s.
        "use_cuda_graph": os.environ.get("TTS_CUDA_GRAPH", "false").lower() in {"1", "true", "yes"},
        "use_flash_attention": False,
    }
    for sr, audio in pipeline.run(inputs):
        if audio is None or len(audio) == 0:
            continue
        arr = np.asarray(audio)
        if arr.dtype != np.int16:
            arr = np.clip(arr, -1.0, 1.0)
            arr = (arr * 32767.0).astype(np.int16)
        yield TARGET_SAMPLE_RATE, _resample(arr, sr, TARGET_SAMPLE_RATE)


def warm(pipeline) -> None:
    """Prepare the fixed voice and burn lazy model initialization at startup.

    ``set_ref_audio`` computes the reference HuBERT semantics and spectrogram.
    The warm synthesis additionally populates GPT-SoVITS' prompt-text cache.
    Every live request then uses those resident tensors without touching or
    reprocessing the reference audio.
    """
    pipeline.set_ref_audio(REF_WAV)
    for _ in synthesize(pipeline, "Warming up.", streaming=False):
        pass
    pipeline._macrovision_reference_cached = True


def reference_cached(pipeline) -> bool:
    """Whether startup prepared all fixed speaker-conditioning inputs."""
    cache = getattr(pipeline, "prompt_cache", {})
    return bool(
        getattr(pipeline, "_macrovision_reference_cached", False)
        and cache.get("prompt_semantic") is not None
        and cache.get("refer_spec")
        and cache.get("phones") is not None
        and cache.get("bert_features") is not None
    )
