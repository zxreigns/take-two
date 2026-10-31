"""Dev: the real planner run for the showcase project. Whisper transcript of the showcase voice note, then
Muse Spark Open plans the edits from transcript + timecoded keyframes.
Reads PLANNER_BASE_URL / PLANNER_API_KEY / LIVE_PLANNER_MODEL from backend/.env (see backend/.env.example).
Writes docs/showcase/planner_raw.json and docs/showcase/planner_ops.json."""
import json, sys, time
from pathlib import Path
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(root / "backend/.env")
from backend.app import config  # noqa: E402
from backend.app.planner import Shot, plan  # noqa: E402

data = root / "backend/data/wow"          # built by `python -m app.wow --wow <showcase folder>`
out_dir = root / "docs/showcase"
t = time.time()
from faster_whisper import WhisperModel  # noqa: E402
m = WhisperModel("small", device="cpu", compute_type="int8")
segs, _ = m.transcribe(str(data / "voice_note.mp3"), language="en", vad_filter=True)
tr = [{"t0": round(s.start, 2), "t1": round(s.end, 2), "text": s.text.strip()} for s in segs]
t_tr = round(time.time() - t, 1)
shots = [Shot(1, 0.0, 5.16), Shot(2, 5.16, 11.16), Shot(3, 11.16, 17.16)]
t = time.time()
ops, notes, raw = plan(tr, shots, data / "before.mp4",
    base_url=config.PLANNER_BASE_URL, api_key=config.PLANNER_API_KEY, model=config.LIVE_PLANNER_MODEL)
el = round(time.time() - t, 1)
out = {"model": config.LIVE_PLANNER_MODEL.split("/")[-1], "seconds": el, "transcribe_seconds": t_tr, "transcript": tr,
       "usage": raw.get("usage"), "ops": [o.model_dump() for o in ops], "notes": [n.model_dump() for n in notes],
       "ran_at": time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime())}
json.dump(raw, open(out_dir / "planner_raw.json", "w"), indent=1)
json.dump(out, open(out_dir / "planner_ops.json", "w"), indent=1)
print(json.dumps(out, indent=1)[:5000])
