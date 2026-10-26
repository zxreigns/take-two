# Take Two

**Client notes that edit themselves.**

A client sends a voice note: *"at like twelve seconds my bank app is showing, blur it. And when I turn, put LAGOS 2026 behind me."*
Take Two listens, finds each moment, tracks the right object frame by frame, and gives the editor a proposed edit they can approve. Then it exports a timeline the editor can open in their NLE.

Built for the Meta Global AI Developer Hackathon (Indie track · Build for Your World).

## How it works
```
client voice note / typed notes
   └─ Muse Voice Transcribe ──► transcript
        └─ Muse Wonder (planner, tool calls) ──► timecoded edit ops
             ├─ SAM 3.1 (video, noun-phrase concept) ──► per-frame masks, stable ids
             ├─ set-of-mark pick: numbered boxes on a keyframe → planner picks the id
             └─ render: matte hygiene → ffmpeg graph (blur · title-behind · spotlight)
editor: before/after diff → approve / reject → MP4 + FCP7 XML
```

## Built during the hackathon (Oct 26 – Nov 4, 2026)
Everything in this repository was written during the event window. The first commit is dated Oct 26, 2026.
<!-- keep this list current: it is what judges use to see what is new -->
- [x] Repo scaffold: FastAPI backend, React + Vite + TypeScript frontend
- [x] Ingest: 720p proxy, keyframes, 16 kHz WAV
- [x] Voice-note notes: timestamped transcript (local Whisper today; Muse Voice Transcribe slots in through `meta_client`)
- [x] Planner on Meta open weights (Muse Spark Open, tool calling → timecoded op JSON, grounded on keyframes)
- [x] SAM 3.1 video masks (open weights) with stable object ids; RLE loader + matte refine
- [x] Ops on real mattes: frosted privacy mosaic (fingers stay sharp) · title behind the subject (world-locked) · make-him-pop grade (subject +0.85 stop, background −1.1 stop, rim + light wrap)
- [x] Editor: split-wipe diff player, colour-coded mask overlay, approve/reject per edit, timeline of edit spans
- [x] Export: MP4 of the approved edits only (SAM mattes cached, no re-segmenting)
- [ ] FCP7 XML export

## Run locally
```bash
# backend
cd backend && python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add MODEL_API_KEY
uvicorn app.main:app --reload --port 8000

# frontend
cd frontend && npm install && npm run dev
```

Replay project (no model calls; the cut, the plan, the transcript and the mattes are prebuilt):
```bash
cd backend && python -m app.demo --dev <dir with sam31_*.json, planner_ops.json, cut_before.mp4> --stock <dir with the Mixkit 720p clips>
# then open the editor: http://localhost:5173/?project=demo
```

## Media credits
Demo footage is free stock from Mixkit, used under the Mixkit Stock Video Free License:
- "Young man on the street typing on his cell phone" (mixkit.co #4837)
- "Man walking among traffic in the city" (mixkit.co #101)
- "A man being followed walking in a city" (mixkit.co #134)
The client voice note in the demo is synthetic (text-to-speech); the client is fictional.

## License
MIT
