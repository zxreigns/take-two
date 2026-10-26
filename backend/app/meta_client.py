"""Thin wrapper over Meta Model API (OpenAI-compatible, https://api.meta.ai/v1).
Endpoint shapes for ASR and SAM are verified against live responses on Day 1 and
pinned here; until a key is present every call raises MissingKey."""
from __future__ import annotations
import base64
from pathlib import Path
import httpx
from openai import OpenAI
from . import config


class MissingKey(RuntimeError):
    pass


def _key() -> str:
    if not config.MODEL_API_KEY:
        raise MissingKey("MODEL_API_KEY is not set")
    return config.MODEL_API_KEY


def client() -> OpenAI:
    return OpenAI(api_key=_key(), base_url=config.MODEL_API_BASE)


def ping() -> dict:
    """Cheapest possible call: one short completion."""
    r = client().chat.completions.create(
        model=config.VISION_MODEL,
        messages=[{"role": "user", "content": "Reply with the single word: ready"}],
        max_tokens=5,
    )
    return {"model": r.model, "text": r.choices[0].message.content}


def transcribe(wav: Path, keywords: list[str] | None = None, diarize: bool = False) -> dict:
    """Muse Voice Transcribe. WAV mono 16-bit, 16/24 kHz, <=10 min, <=32 MB per request.
    Returns turn-level timestamps (no word-level timing)."""
    data = {"model": "muse-voice-transcribe-1.0"}
    if diarize:
        data["mode"] = "DIARIZATION"
    if keywords:
        data["keywords"] = ",".join(keywords)
    with wav.open("rb") as f, httpx.Client(timeout=120) as h:
        r = h.post(
            f"{config.MODEL_API_BASE}/asr/transcribe",
            headers={"Authorization": f"Bearer {_key()}"},
            data=data,
            files={"file": (wav.name, f, "audio/wav")},
        )
    r.raise_for_status()
    return r.json()


def image_part(path: Path) -> dict:
    b64 = base64.b64encode(path.read_bytes()).decode()
    return {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"}
