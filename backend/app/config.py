import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

MODEL_API_KEY = os.getenv("MODEL_API_KEY", "")
MODEL_API_BASE = os.getenv("MODEL_API_BASE", "https://api.meta.ai/v1")
PLANNER_MODEL = os.getenv("PLANNER_MODEL", "muse-wonder-1.0")
VISION_MODEL = os.getenv("VISION_MODEL", "muse-spark-1.3")
DATA_DIR = Path(os.getenv("DATA_DIR", "./data")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

PROXY_HEIGHT = 720          # work files are 720p proxies; masters never live on the server
KEYFRAME_EVERY_S = 1.0      # timecode-labelled keyframes for the planner

# --- live project route (open weights) ---
# Planner: any OpenAI-compatible chat endpoint with tool calls + image input (Muse Spark Open on Fireworks by default).
PLANNER_BASE_URL = os.getenv("PLANNER_BASE_URL", "https://api.fireworks.ai/inference/v1")
PLANNER_API_KEY = os.getenv("PLANNER_API_KEY", "")
LIVE_PLANNER_MODEL = os.getenv("LIVE_PLANNER_MODEL", "accounts/fireworks/models/muse-spark-open")
# SAM 3.1 on fal's queue. FAL_AUTH is the whole Authorization header value ("Key <fal key>").
FAL_BASE_URL = os.getenv("FAL_BASE_URL", "https://queue.fal.run")
FAL_AUTH = os.getenv("FAL_AUTH", ("Key " + os.getenv("FAL_KEY", "")) if os.getenv("FAL_KEY") else "")
SAM_MODEL = os.getenv("SAM_MODEL", "fal-ai/sam-3-1")
# Local transcript (Muse Spark Open has no audio input). faster-whisper model name.
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
# Vocabulary hint for names and titles whisper would otherwise "correct" (Eko, not eco).
WHISPER_PROMPT = os.getenv("WHISPER_PROMPT", "Client notes on a video edit. Names: Lagos, Eko, Abuja, Naija, Lekki, Ikoyi.")
# Limits for uploads on the live route
MAX_CLIP_S = float(os.getenv("MAX_CLIP_S", "60"))
MAX_VOICE_S = float(os.getenv("MAX_VOICE_S", "180"))
MAX_UPLOAD_MB = float(os.getenv("MAX_UPLOAD_MB", "300"))
WEBM_TWINS = os.getenv("WEBM_TWINS", "0") == "1"     # VP9 copies for browsers without H.264 (open-source Chromium)
