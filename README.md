# Take Two

**Client notes that edit themselves.**

A client sends a voice note: *"at like twelve seconds my bank app is showing, blur it. And when I turn, put LAGOS 2026 behind me."*
Take Two listens, finds each moment, tracks the right object frame by frame, and gives the editor a proposed edit they can approve. Then it exports a timeline the editor can open in their NLE.

Built for the Meta Global AI Developer Hackathon (Indie track · Build for Your World).

## How it works
```
the cut + the client's voice note (uploaded in the editor)
   ├─ ingest: 720p proxy, shot detection, frame-exact shot segments, timecoded keyframes
   ├─ transcript: local Whisper (Muse Voice Transcribe slots in through meta_client)
   └─ Muse Spark Open (Meta open weights; planner, tool calls) ──► timecoded edit ops, each quoting the client
        ├─ SAM 3.1 (Meta open weights; video, noun-phrase concept) per shot × target ──► per-frame masks, stable ids
        └─ render: matte hygiene → privacy mosaic · title behind the subject · make-him-pop grade
editor: live progress (masks paint in as they decode) → before/after diff → approve / reject → MP4
```
Every stage streams to the editor over server-sent events and caches its output, so a failed run resumes
at the stage that broke ("Retry from here"). The replay project needs no model calls at all.

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
- [x] Live route: upload a cut + a voice note in the editor → transcript → plan → SAM 3.1 masks → render, with a live progress view (stages, transcript and edit cards streaming in, masks painting in per shot), clean errors and resume-on-retry
- [ ] FCP7 XML export

## Run locally
```bash
# backend
cd backend && python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add PLANNER_API_KEY (Fireworks) and FAL_KEY (fal) for the live route
uvicorn app.main:app --port 8000

# frontend
cd frontend && npm install && npm run dev
```

Live route: open the editor, press **+ New revision**, drop the cut (up to 60 s) and the voice note, press **Run Take Two**.

Replay project (no model calls; a finished live run frozen with its media, plan, transcript and mattes):
```bash
cd backend && python -m app.demo --from-live <project id>
# the editor opens it by default: http://localhost:5173/
```

## Media credits
Demo footage is free stock from Mixkit, used under the Mixkit Stock Video Free License:
- "Man texting on WhatsApp" (mixkit.co #41173), a 4 s section cropped to 16:9 for shot 1
- "Man walking among traffic in the city" (mixkit.co #101)
- "A man being followed walking in a city" (mixkit.co #134)
The client voice notes in the demo are synthetic (text-to-speech); the clients are fictional.

## License
MIT
