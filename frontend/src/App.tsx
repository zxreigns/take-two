import { useCallback, useEffect, useRef, useState } from "react";
import { api, fmt, fmtf, KIND, media, type ExportState, type Op, type Project } from "./api";

type View = "split" | "before" | "after";

/** H.264 first; a VP9 twin for browsers without it (open-source Chromium builds). */
const sources = (url?: string) => url ? (
  <>
    <source src={media(url)} type="video/mp4" />
    <source src={media(url.replace(/\.mp4$/, ".webm"))} type="video/webm" />
  </>
) : null;

export default function App() {
  const [project, setProject] = useState<Project | null>(null);
  const [status, setStatus] = useState("connecting…");
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [split, setSplit] = useState(0.5);
  const [view, setView] = useState<View>("split");
  const [masks, setMasks] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [exp, setExp] = useState<ExportState>({ state: "idle" });

  const before = useRef<HTMLVideoElement>(null);
  const after = useRef<HTMLVideoElement>(null);
  const mattes = useRef<HTMLVideoElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);

  useEffect(() => {
    const id = new URLSearchParams(location.search).get("project") ?? "demo";
    api.get(id).then((p) => { setProject(p); setStatus(p.replay ? "replay project · no model calls" : "live project"); })
      .catch(() => setStatus("API offline"));
  }, []);

  // keep the three videos locked to the "before" clock
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const b = before.current;
      if (b) {
        for (const v of [after.current, mattes.current]) {
          if (v && Math.abs(v.currentTime - b.currentTime) > 0.05) v.currentTime = b.currentTime;
        }
        setTime(b.currentTime);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  const seek = useCallback((t: number) => {
    for (const v of [before.current, after.current, mattes.current]) if (v) v.currentTime = t;
    setTime(t);
  }, []);

  function toggle() {
    const vs = [before.current, after.current, mattes.current].filter(Boolean) as HTMLVideoElement[];
    if (playing) { vs.forEach((v) => v.pause()); setPlaying(false); }
    else { vs.forEach((v) => v.play()); setPlaying(true); }
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.code === "Space") { e.preventDefault(); toggle(); }
      if (e.key === "m") setMasks((m) => !m);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  function onPointer(e: React.PointerEvent) {
    if (!dragging.current || !stage.current) return;
    const r = stage.current.getBoundingClientRect();
    setSplit(Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)));
  }

  async function decide(op: Op, status: Op["status"]) {
    if (!project) return;
    const next = op.status === status ? "proposed" : status;
    await api.decide(project.id, op.id, next);
    setProject({ ...project, ops: project.ops.map((o) => (o.id === op.id ? { ...o, status: next } : o)) });
    setExp({ state: "idle" });
  }

  async function startExport() {
    if (!project) return;
    setExp(await api.exportStart(project.id));
  }
  useEffect(() => {
    if (exp.state !== "running" || !project) return;
    const t = setInterval(async () => setExp(await api.exportStatus(project.id)), 2000);
    return () => clearInterval(t);
  }, [exp.state, project]);

  const m = project?.media;
  const duration = m?.duration ?? 1;
  const ops = (project?.ops ?? []).filter((o) => o.body.kind !== "ask");
  const live = ops.filter((o) => time >= o.t0 && time <= o.t1);
  const rejectedNow = live.some((o) => o.status === "rejected");
  const approved = ops.filter((o) => o.status === "approved").length;
  const noteFor = (o: Op) => project?.notes.find((n) => n.id === o.note_id);
  const afterClip = view === "after" ? "inset(0 0 0 0)" : view === "before" ? "inset(0 0 0 100%)" : `inset(0 0 0 ${split * 100}%)`;

  return (
    <div className="shell">
      <header>
        <div className="brand"><span className="mark">2</span> Take Two <span className="proj">{project?.name}</span></div>
        <div className="status">{status}</div>
      </header>

      <main>
        <section className="stagewrap">
          {m ? (
            <>
              <div className="stage" ref={stage} onPointerMove={onPointer} onPointerUp={() => (dragging.current = false)} onPointerLeave={() => (dragging.current = false)}>
                <video ref={before} muted playsInline onEnded={() => setPlaying(false)}>{sources(m.before_url ?? m.proxy_url)}</video>
                {m.after_url && (
                  <video ref={after} className="after" muted playsInline
                    style={{ clipPath: afterClip, opacity: rejectedNow ? 0 : 1 }}>{sources(m.after_url)}</video>
                )}
                {m.mattes_url && <video ref={mattes} className="mattes" muted playsInline style={{ opacity: masks ? (rejectedNow ? 0.15 : 0.6) : 0 }}>{sources(m.mattes_url)}</video>}
                {view === "split" && m.after_url && (
                  <div className="divider" style={{ left: `${split * 100}%` }} onPointerDown={(e) => { dragging.current = true; e.preventDefault(); }}>
                    <span className="knob">⟷</span>
                  </div>
                )}
                <span className="tag left">CLIENT CUT</span>
                <span className="tag right">TAKE TWO</span>
                {live.length > 0 && (
                  <div className="live">
                    {live.map((o) => (
                      <span key={o.id} className={`chip ${o.status}`} style={{ borderColor: KIND[o.body.kind].colour }}>
                        <i style={{ background: KIND[o.body.kind].colour }} />{KIND[o.body.kind].label}{o.status === "rejected" ? " · rejected" : ""}
                      </span>
                    ))}
                  </div>
                )}
              </div>
              <div className="controls">
                <button className="play" onClick={toggle}>{playing ? "❚❚" : "▶"}</button>
                <span className="clock">{fmtf(time)} / {fmtf(duration)}</span>
                <div className="seg">
                  {(["before", "split", "after"] as View[]).map((v) => (
                    <button key={v} className={view === v ? "on" : ""} onClick={() => setView(v)}>{v === "split" ? "Split" : v === "before" ? "Before" : "After"}</button>
                  ))}
                </div>
                <button className={`toggle ${masks ? "on" : ""}`} onClick={() => setMasks(!masks)}>Masks</button>
              </div>
            </>
          ) : (
            <div className="drop"><strong>No project loaded</strong><span>{status}</span></div>
          )}
        </section>

        <aside className="side">
          {m?.voice_url && (
            <div className="block">
              <h2>Client voice note</h2>
              <audio src={media(m.voice_url)} controls />
              <ol className="transcript">
                {project?.transcript?.map((l, i) => (
                  <li key={i}><span className="tc">{fmt(l.t0)}</span>{l.text}</li>
                ))}
              </ol>
            </div>
          )}
          <div className="block grow">
            <h2>
              Proposed edits <span className="count">{approved}/{ops.length} approved</span>
            </h2>
            {project?.planner?.model && <p className="sub">planned by {project.planner.model} in {project.planner.seconds?.toFixed(0)} s · masks by SAM 3.1</p>}
            <ul className="ops">
              {ops.map((o) => {
                const k = KIND[o.body.kind];
                return (
                  <li key={o.id} className={`op ${o.status} ${selected === o.id ? "sel" : ""}`} style={{ borderLeftColor: k.colour }}
                    onClick={() => { setSelected(o.id); seek(o.t0 + 0.05); }}>
                    <div className="row">
                      <span className="kind" style={{ color: k.colour }}>{k.label}</span>
                      <span className="what">{o.body.kind === "title_behind" ? `“${o.body.text}” behind ${o.body.target?.concept}` : o.body.target?.concept}</span>
                      <span className="tc">{fmtf(o.t0)}–{fmtf(o.t1)}</span>
                    </div>
                    {noteFor(o) && <q>{noteFor(o)!.text}</q>}
                    <div className="row actions">
                      <button className={`ok ${o.status === "approved" ? "on" : ""}`} onClick={(e) => { e.stopPropagation(); decide(o, "approved"); }}>✓ Approve</button>
                      <button className={`no ${o.status === "rejected" ? "on" : ""}`} onClick={(e) => { e.stopPropagation(); decide(o, "rejected"); }}>✕ Reject</button>
                    </div>
                  </li>
                );
              })}
            </ul>
            <button className="export" disabled={!approved || exp.state === "running"} onClick={startExport}>
              {exp.state === "running" ? "Rendering approved edits…" : `Export ${approved} approved`}
            </button>
            {exp.state === "done" && exp.url && <a className="dl" href={media(exp.url)} download>Download MP4</a>}
            {exp.state === "error" && <p className="err">{exp.error}</p>}
          </div>
        </aside>
      </main>

      <footer className="timeline">
        <div className="track" onPointerDown={(e) => {
          const r = e.currentTarget.getBoundingClientRect(); seek(((e.clientX - r.left) / r.width) * duration);
        }}>
          {ops.map((o, i) => (
            <div key={o.id} className={`span ${o.status} ${selected === o.id ? "sel" : ""}`}
              style={{ left: `${(o.t0 / duration) * 100}%`, width: `${((o.t1 - o.t0) / duration) * 100}%`, top: 8 + (i % 3) * 14, background: KIND[o.body.kind].colour }}
              title={`${KIND[o.body.kind].label} ${fmtf(o.t0)}–${fmtf(o.t1)}`} />
          ))}
          <div className="playhead" style={{ left: `${(time / duration) * 100}%` }} />
        </div>
      </footer>
    </div>
  );
}
