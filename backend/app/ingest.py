"""Upload -> 720p proxy (source fps kept), keyframes every second, 16 kHz mono WAV.
Every ffmpeg call is bounded (timeout + kill) so a stuck encode can't hold the server."""
from __future__ import annotations
import json
import subprocess
from pathlib import Path
from . import config

FF = ["nice", "-n", "10", "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-threads", "2"]


def _run(cmd: list[str], timeout: int = 900) -> None:
    subprocess.run(["timeout", "-k", "5", str(timeout), *cmd], check=True, capture_output=True)


def probe(src: Path) -> dict:
    out = subprocess.run(
        ["timeout", "-k", "5", "30", "ffprobe", "-v", "error", "-print_format", "json",
         "-show_streams", "-show_format", str(src)],
        check=True, capture_output=True, text=True,
    ).stdout
    j = json.loads(out)
    v = next(s for s in j["streams"] if s["codec_type"] == "video")
    num, den = (int(x) for x in v.get("r_frame_rate", "30/1").split("/"))
    return {
        "width": int(v["width"]), "height": int(v["height"]),
        "fps": num / den if den else 30.0,
        "duration": float(j["format"]["duration"]),
        "has_audio": any(s["codec_type"] == "audio" for s in j["streams"]),
    }


def make_proxy(src: Path, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    info = probe(src)
    proxy = out_dir / "proxy.mp4"
    _run([*FF, "-i", str(src), "-vf", f"scale=-2:{config.PROXY_HEIGHT}",
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(proxy)])
    kf = out_dir / "keyframes"
    kf.mkdir(exist_ok=True)
    _run([*FF, "-i", str(proxy), "-vf", f"fps=1/{config.KEYFRAME_EVERY_S},scale=-2:480",
          "-q:v", "4", str(kf / "kf_%05d.jpg")])
    wav = None
    if info["has_audio"]:
        wav = out_dir / "audio16k.wav"
        _run([*FF, "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", str(wav)])
    return {**info, "proxy": proxy.name, "keyframes": len(list(kf.glob("*.jpg"))),
            "wav": wav.name if wav else None}
