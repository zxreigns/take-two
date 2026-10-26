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
