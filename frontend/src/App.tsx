import { useEffect, useRef, useState } from "react";
import { api, fmt, parseNoteLine, type Project } from "./api";

export default function App() {
  const [project, setProject] = useState<Project | null>(null);
  const [status, setStatus] = useState("connecting…");
  const [draft, setDraft] = useState("0:12 blur the phone whenever it shows\n0:30 put the title \"LAGOS, 2026\" behind him");
  const [time, setTime] = useState(0);
  const video = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    api.health()
      .then((h) => setStatus(h.model_key ? `ready · planner ${h.planner}` : "API up · no model key yet"))
      .catch(() => setStatus("API offline"));
  }, []);

  async function ensureProject() {
    if (project) return project;
    const p = await api.create("Untitled cut");
    setProject(p);
    return p;
  }

  async function onFile(f: File) {
    const p = await ensureProject();
    setStatus("making proxy…");
    setProject(await api.upload(p.id, f));
    setStatus("proxy ready");
  }

  async function addNotes() {
    let p = await ensureProject();
    for (const line of draft.split("\n").filter((l) => l.trim())) {
      const { t, text } = parseNoteLine(line);
      p = await api.addNote(p.id, text, t);
    }
    setProject(p);
    setDraft("");
  }

  const duration = project?.media?.duration ?? 60;
  const src = project?.media ? `${import.meta.env.VITE_API_URL ?? "http://localhost:8000"}${project.media.proxy_url}` : undefined;

  return (
    <div className="shell">
      <header>
        <div className="brand"><span className="mark">2</span> Take Two</div>
        <div className="status">{status}</div>
      </header>

      <main>
        <section className="stage">
          {src ? (
            <video ref={video} src={src} controls onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)} />
          ) : (
            <label className="drop">
              <input type="file" accept="video/*" hidden onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])} />
              <strong>Drop a cut</strong>
              <span>MP4 or MOV · we work on a 720p proxy</span>
            </label>
          )}
        </section>

        <aside className="notes">
          <h2>Client notes</h2>
          <textarea value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="0:12 blur the phone…" />
          <button onClick={addNotes} disabled={!draft.trim()}>Pin to timeline</button>
          <ul>
            {project?.notes.map((n) => (
              <li key={n.id} onClick={() => n.t != null && video.current && (video.current.currentTime = n.t)}>
                <span className="tc">{n.t != null ? fmt(n.t) : "—"}</span> {n.text}
              </li>
            ))}
          </ul>
        </aside>
      </main>

      <footer className="timeline">
        <div className="track">
          <div className="playhead" style={{ left: `${(time / duration) * 100}%` }} />
          {project?.notes.filter((n) => n.t != null).map((n) => (
            <div key={n.id} className="pin" style={{ left: `${((n.t ?? 0) / duration) * 100}%` }} title={n.text} />
          ))}
        </div>
      </footer>
    </div>
  );
}
