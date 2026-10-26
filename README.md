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
- [ ] Ingest: 720p proxy, keyframes, 16 kHz WAV
- [ ] Voice-note notes (Muse Voice Transcribe)
- [ ] Planner (Muse Wonder tool calling → op JSON)
- [ ] SAM 3.1 tracking + matte hygiene
- [ ] Ops: tracked blur · title-behind-subject · spotlight grade
- [ ] Diff player + approve/reject
- [ ] Export: MP4 + FCP7 XML

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

## License
MIT
