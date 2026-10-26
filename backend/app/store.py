"""Projects, notes and ops in SQLite as JSON documents. Small on purpose."""
from __future__ import annotations
import json
import sqlite3
import time
import uuid
from . import config

DB = config.DATA_DIR / "take_two.sqlite"


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB)
    c.execute("create table if not exists projects (id text primary key, created real, doc text)")
    return c


def create(name: str) -> dict:
    pid = uuid.uuid4().hex[:12]
    doc = {"id": pid, "name": name, "media": None, "notes": [], "ops": []}
    with _conn() as c:
        c.execute("insert into projects values (?,?,?)", (pid, time.time(), json.dumps(doc)))
    return doc


def get(pid: str) -> dict | None:
    with _conn() as c:
        row = c.execute("select doc from projects where id=?", (pid,)).fetchone()
    return json.loads(row[0]) if row else None


def put(doc: dict) -> None:
    with _conn() as c:
        c.execute("update projects set doc=? where id=?", (json.dumps(doc), doc["id"]))


def all_projects() -> list[dict]:
    with _conn() as c:
        rows = c.execute("select doc from projects order by created desc").fetchall()
    return [json.loads(r[0]) for r in rows]


def upsert(doc: dict) -> None:
    """Insert or replace a document with a fixed id (the replay project)."""
    with _conn() as c:
        c.execute("insert or replace into projects values (?,?,?)", (doc["id"], time.time(), json.dumps(doc)))


def delete(pid: str) -> None:
    with _conn() as c:
        c.execute("delete from projects where id=?", (pid,))
