from __future__ import annotations
import shutil
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import hashlib
import json
import threading
import time
from pathlib import Path
from . import config, ingest, meta_client, store
from .ops import Note, Op, OpStatus

app = FastAPI(title="Take Two API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/media", StaticFiles(directory=config.DATA_DIR), name="media")


@app.get("/health")
def health():
    return {"ok": True, "model_key": bool(config.MODEL_API_KEY), "planner": config.PLANNER_MODEL}


@app.get("/meta/ping")
def meta_ping():
    try:
        return meta_client.ping()
    except meta_client.MissingKey as e:
        raise HTTPException(503, str(e))


class NewProject(BaseModel):
    name: str


@app.post("/projects")
def new_project(body: NewProject):
    return store.create(body.name)


@app.get("/projects")
def list_projects():
    return store.all_projects()


@app.get("/projects/{pid}")
def get_project(pid: str):
    doc = store.get(pid)
    if not doc:
        raise HTTPException(404, "no such project")
    return doc


@app.post("/projects/{pid}/media")
def upload_media(pid: str, file: UploadFile = File(...)):
    doc = get_project(pid)
    pdir = config.DATA_DIR / pid
    pdir.mkdir(parents=True, exist_ok=True)
    src = pdir / f"source{(file.filename or 'clip.mp4')[-4:]}"
    with src.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    info = ingest.make_proxy(src, pdir)
    src.unlink(missing_ok=True)          # masters never stay on the server
    doc["media"] = {**info, "proxy_url": f"/media/{pid}/{info['proxy']}"}
    store.put(doc)
    return doc


@app.post("/projects/{pid}/notes")
def add_note(pid: str, note: Note):
    doc = get_project(pid)
    doc["notes"].append(note.model_dump(mode="json"))
    store.put(doc)
    return doc


class OpDecision(BaseModel):
    status: OpStatus


@app.post("/projects/{pid}/ops/{oid}")
def decide(pid: str, oid: str, body: OpDecision):
    doc = get_project(pid)
    for o in doc["ops"]:
        if o["id"] == oid:
            o["status"] = body.status.value
            store.put(doc)
            return Op(**o)
    raise HTTPException(404, "no such op")


# --- export: render only the approved ops (SAM mattes are cached per clip + concept, so no re-segmenting) ---
_exports: dict[str, dict] = {}


def _run_export(pid: str, doc: dict) -> None:
    from .pipeline import CutShot, render_cut
    try:
        shots = [CutShot(Path(s["src"]), s["t_in"], s["t_out"], {k: Path(v) for k, v in s["sam"].items()}) for s in doc["shots"]]
        ops = [Op(**o) for o in doc["ops"] if o["status"] == "approved"]
        out_dir = config.DATA_DIR / pid; out_dir.mkdir(parents=True, exist_ok=True)
        tag = "-".join(sorted(o.id for o in ops)) or "none"
        out = out_dir / f"export_{hashlib.sha1(tag.encode()).hexdigest()[:10]}.mp4"
        if not out.exists():
            m = doc.get("media") or {}
            render_cut(shots, ops, out, out_dir / "tmp", fps_out=str(m.get("fps", 24000 / 1001)), size=f"{m.get('width', 1280)}:{m.get('height', 720)}")
        _exports[pid] = {"state": "done", "url": f"/media/{pid}/{out.name}", "ops": [o.id for o in ops]}
    except Exception as e:  # surfaced to the editor as-is
        _exports[pid] = {"state": "error", "error": str(e)}


@app.post("/projects/{pid}/export")
def export(pid: str):
    doc = get_project(pid)
    if not doc.get("shots"):
        raise HTTPException(400, "project has no shot list yet")
    if _exports.get(pid, {}).get("state") == "running":
        return _exports[pid]
    _exports[pid] = {"state": "running"}
    threading.Thread(target=_run_export, args=(pid, doc), daemon=True).start()
    return _exports[pid]


@app.get("/projects/{pid}/export")
def export_status(pid: str):
    return _exports.get(pid, {"state": "idle"})


@app.get("/projects/{pid}/export.xml")
def export_xml(pid: str):
    """FCP7 XML for the last export: V1 the client cut, V2 the approved edits, markers with the client's words."""
    from fastapi.responses import Response
    from . import fcpxml
    st = _exports.get(pid, {})
    if st.get("state") != "done":
        raise HTTPException(409, "export the approved edits first")
    doc = get_project(pid)
    m = doc.get("media") or {}
    before = Path(m.get("before_url", "")).name
    after = Path(st["url"]).name
    has_audio = False
    try:
        import subprocess
        p = config.DATA_DIR / pid / before
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(p)],
                             capture_output=True, text=True, timeout=20).stdout.strip()
        has_audio = bool(out)
    except Exception:
        pass
    ops = [o for o in doc["ops"] if o["id"] in set(st.get("ops", []))]
    notes = {n["id"]: n["text"] for n in doc.get("notes", [])}
    xml = fcpxml.build(doc.get("name", "Take Two revision"), before, after, float(m.get("duration", 0)),
                       float(m.get("fps", 24000 / 1001)), int(m.get("width", 1280)), int(m.get("height", 720)), ops, notes, has_audio)
    return Response(xml, media_type="application/xml",
                    headers={"Content-Disposition": f'attachment; filename="take_two_{pid}.xml"'})


# --- live route: clip + voice note in -> transcript -> plan -> SAM masks -> render, streamed to the editor ---
from . import live  # noqa: E402

_VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
_AUDIO_EXT = {".mp3", ".m4a", ".wav", ".ogg", ".opus", ".aac", ".webm", ".mp4", ".flac"}


def _save_upload(up: UploadFile, dest: Path, allowed: set[str], what: str) -> Path:
    ext = Path(up.filename or "").suffix.lower()
    if ext not in allowed:
        raise HTTPException(400, f"The {what} should be one of {', '.join(sorted(allowed))} (got '{ext or 'no extension'}').")
    out = dest.with_suffix(ext); size = 0
    with out.open("wb") as f:
        while chunk := up.file.read(1 << 20):
            size += len(chunk)
            if size > config.MAX_UPLOAD_MB * 1e6:
                f.close(); out.unlink(missing_ok=True)
                raise HTTPException(413, f"The {what} is over {config.MAX_UPLOAD_MB:.0f} MB.")
            f.write(chunk)
    if size == 0:
        out.unlink(missing_ok=True)
        raise HTTPException(400, f"The {what} is empty.")
    return out


@app.post("/projects/live")
def live_project(clip: UploadFile = File(...), voice: UploadFile = File(...), name: str = Form("Live project")):
    if live.busy():
        raise HTTPException(409, "Another project is processing right now. Try again in a minute, or open the replay project.")
    doc = store.create(name)
    pdir = config.DATA_DIR / doc["id"]; pdir.mkdir(parents=True, exist_ok=True)
    try:
        c = _save_upload(clip, pdir / "upload_clip", _VIDEO_EXT, "clip")
        v = _save_upload(voice, pdir / "voice_note", _AUDIO_EXT, "voice note")
    except HTTPException:
        shutil.rmtree(pdir, ignore_errors=True); store.delete(doc["id"]); raise
    (pdir / "inputs.json").write_text(json.dumps({"clip": c.name, "voice": v.name, "clip_name": clip.filename, "voice_name": voice.filename}))
    try:
        st = live.start(doc["id"], c, v)
    except RuntimeError:
        shutil.rmtree(pdir, ignore_errors=True); store.delete(doc["id"])
        raise HTTPException(409, "Another project is processing right now. Try again in a minute, or open the replay project.")
    return {"id": doc["id"], "live": st}


@app.post("/projects/{pid}/retry")
def live_retry(pid: str):
    get_project(pid)
    st = live.state(pid)
    if st and st.get("state") == "running" and pid in live._runs:
        return st
    try:
        return live.retry(pid)
    except FileNotFoundError:
        raise HTTPException(400, "This project has no uploaded inputs to retry.")
    except RuntimeError:
        raise HTTPException(409, "Another project is processing right now.")


@app.get("/projects/{pid}/live")
def live_state(pid: str):
    st = live.state(pid)
    if st is None:
        raise HTTPException(404, "no live run for this project")
    return st


@app.get("/projects/{pid}/events")
def live_events(pid: str):
    """Server-sent events: a full state snapshot whenever it changes; closes once the run is done or failed."""
    if live.state(pid) is None:
        raise HTTPException(404, "no live run for this project")

    def gen():
        version = -1; last_beat = time.time()
        while True:
            st = live.wait_change(pid, version, timeout=10)
            if st is None:
                return
            if st["version"] != version:
                version = st["version"]
                yield f"data: {json.dumps(st)}\n\n"
                if st["state"] in ("done", "error"):
                    return
            elif time.time() - last_beat > 9:
                last_beat = time.time(); yield ": keep-alive\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
