from __future__ import annotations
import shutil
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import hashlib
import threading
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
            render_cut(shots, ops, out, out_dir / "tmp")
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
