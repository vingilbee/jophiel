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


"""Columbina voice, switchable between three GPT-SoVITS setups for A/B testing.

Select with the TTS_MODE env var:

    TTS_MODE=finetune  -- your original fine-tuned v1 checkpoint
    TTS_MODE=v2pro      -- zero-shot on v2Pro base weights (default; the repo's
                            own docs recommend v2Pro for "best compatibility
                            and stability")
    TTS_MODE=v4         -- zero-shot on v4 base weights (most reference-
                            faithful, but heavier CFM vocoder -- slower on
                            CPU, per the earlier RTF discussion)

    TTS_DEVICE=cpu .venv/bin/uvicorn server:app --port 8787          # v2pro
    TTS_DEVICE=cpu TTS_MODE=v4 .venv/bin/uvicorn server:app --port 8787
    TTS_DEVICE=cpu TTS_MODE=finetune .venv/bin/uvicorn server:app --port 8787

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
        "version": "v1",
        "t2s_weights_path": os.path.join(VOICE, "columbina-e15-2.ckpt"),
        "vits_weights_path": os.path.join(VOICE, "columbina_e8_s488.pth"),
        # The clip your original fine-tune was built and validated against --
        # keep this distinct from the newer clean 18.9s ref used for the
        # zero-shot modes below, since re-using the wrong ref with a
        # fine-tuned checkpoint isn't an apples-to-apples comparison.
        "ref_wav": os.path.join(VOICE, "ref_columbina_6s.wav"),
        "ref_txt": os.path.join(VOICE, "ref_columbina_6s.txt"),
        "native_sample_rate": None,  # matches tts_sample_rate already; no resample
    },
    "v2pro": {
        "version": "v2Pro",
        "t2s_weights_path": os.path.join(PRETRAINED, "s1v3.ckpt"),
        "vits_weights_path": os.path.join(PRETRAINED, "v2Pro", "s2Gv2Pro.pth"),
        "ref_wav": os.path.join(VOICE, "ref_columbina_6s.wav"),
        "ref_txt": os.path.join(VOICE, "ref_columbina_6s.txt"),
        "native_sample_rate": None,
    },
    "v4": {
        "version": "v4",
        "t2s_weights_path": os.path.join(PRETRAINED, "s1v3.ckpt"),
        "vits_weights_path": os.path.join(PRETRAINED, "gsv-v4-pretrained", "s2Gv4.pth"),
        "ref_wav": os.path.join(VOICE, "ref_columbina_6s.wav"),
        "ref_txt": os.path.join(VOICE, "ref_columbina_6s.txt"),
        "native_sample_rate": 48000,  # v4 outputs 48k; resample to match config
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


def load_pipeline(device: str = "cpu"):
    """Build the TTS pipeline for the selected TTS_MODE. Slow (~2-3s); call
    once per process."""
    print(f"[tts] mode={MODE} version={_CFG['version']}")
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
                "is_half": False,
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
        # Only meaningful for v3/v4's CFM vocoder stage; harmless no-op on v1/v2Pro.
        "sample_steps": 32,
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
