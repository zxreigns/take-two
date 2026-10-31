"""Planner: a client's voice note -> timecoded, reversible edit proposals (tool calls).

Model-agnostic: any OpenAI-compatible endpoint with tool calling + image input. Development runs on
Muse Spark Open (Meta open weights) on Fireworks; the Meta Model API slots in by base_url/model.

Inputs the model sees:
  * the transcript with the voice note's own timestamps (what the client said, in order)
  * the cut's timeline: shots with their cut-time ranges
  * timecoded keyframes of the cut (one image per second, each labelled "t=..s · shot N")
It grounds vague time ("at the start", "when the man with the shades is walking", "that last shot")
to cut seconds by looking at the frames, and picks a short SAM noun phrase for each target.
"""
from __future__ import annotations
import base64
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import cv2
import httpx

from .ops import Ask, Blur, Note, NoteSource, Op, Recolour, Remove, SkyReplace, Spotlight, Target, TitleBehind

SYSTEM = """You are the planner inside Take Two, a revision tool for video editors.
A client sent a voice note about a cut. Turn every change they ask for into exactly one tool call.
Rules:
- Ground each change to the cut's timeline in seconds (t0, t1), using the shot list and the timecoded keyframes. Prefer whole-shot ranges when the client refers to a shot; tighten to the frames where the subject is actually visible when the note says "when ...".
- target_concept is a short noun phrase a segmentation model can find in the frame (e.g. "phone", "man in sunglasses", "man"). One object per call.
- The ops that exist: blur (redact an object), title_behind (text placed behind a person), spotlight (make a subject pop; the background is pulled down), remove (erase a person or object from every frame and rebuild what was behind it, with its shadow), sky (replace the sky with a new look such as golden hour), recolour (change the colour of one object, e.g. clothing). If a request fits none, or is ambiguous between two objects, call ask_client instead of guessing.
- For remove, name what must go with the object in `also` when it casts a shadow or reflection (e.g. "his shadow").
- Quote the client's exact words in `quote`. Keep `rationale` to one short sentence.
- Do not invent requests the client did not make. Praise and greetings are not requests."""

TOOLS = [
    {"type": "function", "function": {"name": "propose_blur", "description": "Blur (redact) one tracked object over a time range.",
        "parameters": {"type": "object", "properties": {
            "target_concept": {"type": "string"}, "t0": {"type": "number"}, "t1": {"type": "number"},
            "strength": {"type": "number", "description": "0..1"}, "quote": {"type": "string"}, "rationale": {"type": "string"}},
            "required": ["target_concept", "t0", "t1", "quote", "rationale"]}}},
    {"type": "function", "function": {"name": "propose_title_behind", "description": "Place a title behind a person so they occlude it.",
        "parameters": {"type": "object", "properties": {
            "target_concept": {"type": "string"}, "text": {"type": "string"}, "t0": {"type": "number"}, "t1": {"type": "number"},
            "style": {"type": "string", "enum": ["bold", "serif", "outline"]}, "quote": {"type": "string"}, "rationale": {"type": "string"}},
            "required": ["target_concept", "text", "t0", "t1", "quote", "rationale"]}}},
    {"type": "function", "function": {"name": "propose_spotlight", "description": "Make a subject pop: subject stays in colour, background is desaturated, dimmed and softened.",
        "parameters": {"type": "object", "properties": {
            "target_concept": {"type": "string"}, "t0": {"type": "number"}, "t1": {"type": "number"},
            "amount": {"type": "number", "description": "0..1"}, "quote": {"type": "string"}, "rationale": {"type": "string"}},
            "required": ["target_concept", "t0", "t1", "quote", "rationale"]}}},
    {"type": "function", "function": {"name": "propose_remove", "description": "Erase one person or object from every frame of a range; the background behind it is rebuilt by video inpainting.",
        "parameters": {"type": "object", "properties": {
            "target_concept": {"type": "string"}, "also": {"type": "string", "description": "interaction regions removed with it, e.g. 'his shadow'"},
            "t0": {"type": "number"}, "t1": {"type": "number"}, "quote": {"type": "string"}, "rationale": {"type": "string"}},
            "required": ["target_concept", "t0", "t1", "quote", "rationale"]}}},
    {"type": "function", "function": {"name": "propose_sky", "description": "Replace the sky over a range with a new look; the land is regraded to match.",
        "parameters": {"type": "object", "properties": {
            "look": {"type": "string", "description": "e.g. 'golden hour', 'overcast', 'night'"}, "t0": {"type": "number"}, "t1": {"type": "number"},
            "quote": {"type": "string"}, "rationale": {"type": "string"}},
            "required": ["look", "t0", "t1", "quote", "rationale"]}}},
    {"type": "function", "function": {"name": "propose_recolour", "description": "Change the colour of one tracked object (keeps its shading and folds).",
        "parameters": {"type": "object", "properties": {
            "target_concept": {"type": "string"}, "colour": {"type": "string", "description": "the colour the client named"},
            "hex": {"type": "string", "description": "#RRGGBB for that colour"}, "t0": {"type": "number"}, "t1": {"type": "number"},
            "quote": {"type": "string"}, "rationale": {"type": "string"}},
            "required": ["target_concept", "colour", "t0", "t1", "quote", "rationale"]}}},
    {"type": "function", "function": {"name": "ask_client", "description": "Ask one short clarifying question with options when a request is ambiguous.",
        "parameters": {"type": "object", "properties": {
            "question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}}, "quote": {"type": "string"}},
            "required": ["question", "quote"]}}},
]


@dataclass
class Shot:
    index: int
    t0: float
    t1: float
    label: str = ""


def keyframes(video: Path, shots: list[Shot], every_s: float = 1.0, width: int = 448) -> list[tuple[float, int, bytes]]:
    """(t, shot index, jpeg bytes) every `every_s` seconds, sampled mid-interval."""
    cap = cv2.VideoCapture(str(video)); fps = cap.get(cv2.CAP_PROP_FPS); n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    out = []; t = every_s / 2
    while t < n / fps:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps)); ok, f = cap.read()
        if not ok: break
        h = int(f.shape[0] * width / f.shape[1])
        ok, jpg = cv2.imencode(".jpg", cv2.resize(f, (width, h), interpolation=cv2.INTER_AREA), [cv2.IMWRITE_JPEG_QUALITY, 80])
        shot = next((s.index for s in shots if s.t0 <= t < s.t1), shots[-1].index)
        out.append((round(t, 2), shot, jpg.tobytes())); t += every_s
    cap.release()
    return out


def build_messages(transcript: list[dict], shots: list[Shot], frames: list[tuple[float, int, bytes]]) -> list[dict]:
    note = "\n".join(f"[{s['t0']:.1f}-{s['t1']:.1f}s of the voice note] {s['text']}" for s in transcript)
    timeline = "\n".join(f"shot {s.index}: {s.t0:.2f}-{s.t1:.2f}s of the cut" + (f" ({s.label})" if s.label else "") for s in shots)
    content: list[dict] = [{"type": "text", "text": f"CLIENT VOICE NOTE (transcript):\n{note}\n\nTHE CUT ({shots[-1].t1:.2f}s):\n{timeline}\n\nTIMECODED KEYFRAMES OF THE CUT follow."}]
    for t, shot, jpg in frames:
        content.append({"type": "text", "text": f"t={t:.1f}s · shot {shot}"})
        content.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpg).decode()}})
    content.append({"type": "text", "text": "Now call one tool per requested change, in the order the client asked."})
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}]


def plan(transcript: list[dict], shots: list[Shot], video: Path, *, base_url: str, api_key: str, model: str,
         extra_headers: dict | None = None, timeout: float = 300) -> tuple[list[Op], list[Note], dict]:
    """Returns (ops, notes, raw response). Each op cites its note (the quoted words)."""
    msgs = build_messages(transcript, shots, keyframes(video, shots))
    body = {"model": model, "messages": msgs, "tools": TOOLS, "tool_choice": "auto", "temperature": 0.2, "max_tokens": 6000}
    headers = {"Authorization": f"Bearer {api_key}", **(extra_headers or {})}
    r = httpx.post(f"{base_url.rstrip('/')}/chat/completions", json=body, headers=headers, timeout=timeout)
    r.raise_for_status()
    raw = r.json()
    ops: list[Op] = []; notes: list[Note] = []
    for call in (raw["choices"][0]["message"].get("tool_calls") or []):
        name = call["function"]["name"]; a = json.loads(call["function"]["arguments"] or "{}")
        note = Note(text=a.get("quote", ""), source=NoteSource.voice, t=a.get("t0")); notes.append(note)
        tgt = Target(concept=a.get("target_concept", ""))
        if name == "propose_blur":
            body_ = Blur(target=tgt, strength=float(a.get("strength", 0.9)))
        elif name == "propose_title_behind":
            body_ = TitleBehind(target=tgt, text=a["text"], style=a.get("style", "bold"))
        elif name == "propose_spotlight":
            body_ = Spotlight(target=tgt, amount=float(a.get("amount", 0.7)))
        elif name == "propose_remove":
            body_ = Remove(target=tgt, also=a.get("also"), engine="VOID video inpainting")
        elif name == "propose_sky":
            body_ = SkyReplace(look=a.get("look", "golden hour"))
        elif name == "propose_recolour":
            body_ = Recolour(target=tgt, colour=a.get("colour", ""), hex=a.get("hex"))
        elif name == "ask_client":
            body_ = Ask(question=a["question"], options=a.get("options", []))
        else:
            continue
        ops.append(Op(note_id=note.id, t0=float(a.get("t0", 0)), t1=float(a.get("t1", 0)), body=body_, rationale=a.get("rationale", "")))
    return ops, notes, raw
