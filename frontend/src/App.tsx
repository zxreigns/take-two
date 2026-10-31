import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, CLIENT, fmtf, KIND, media, opTitle, type ExportState, type Op, type Project } from "./api";
import { LiveRun, NewProject } from "./Live";
import { VoiceNote } from "./VoiceNote";
import { ModelRail, type RailKey, type RailState } from "./ModelRail";

type View = "compare" | "before" | "after" | "masks";
const VIEWS: { k: View; label: string }[] = [
  { k: "compare", label: "Compare" }, { k: "before", label: "Original" }, { k: "after", label: "Edited" }, { k: "masks", label: "Masks" },
];
const HOME = "wow";

/** H.264 first; a VP9 twin for browsers without it (open-source Chromium builds). */
const sources = (url?: string) => url ? (
  <>
    <source src={media(url)} type="video/mp4" />
    <source src={media(url.replace(/\.mp4$/, ".webm"))} type="video/webm" />
  </>
) : null;

const ease = (x: number) => (x <= 0 ? 0 : x >= 1 ? 1 : x * x * (3 - 2 * x));
/** Auto wipe: inside each edit the line sweeps in (EDITED from the left), holds, then sweeps back to the original. */
function autoSplit(t: number, ops: Op[]): number {
  const o = ops.find((x) => t >= x.t0 && t <= x.t1);
  if (!o) return 0;
  const len = o.t1 - o.t0, sweep = Math.min(1.4, len * 0.24), lead = Math.min(0.5, len * 0.08);
  const inn = ease((t - o.t0 - lead) / sweep);
  const out = ease((t - (o.t1 - lead - sweep)) / sweep);
  return Math.max(0, inn - out);
}

export default function App() {
  const [project, setProject] = useState<Project | null>(null);
  const [status, setStatus] = useState("Connecting…");
  const [offline, setOffline] = useState(false);
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [manual, setManual] = useState<number | null>(null);   // null = auto wipe
  const [view, setView] = useState<View>("compare");
  const [selected, setSelected] = useState<string | null>(null);
  const [heard, setHeard] = useState<string | null>(null);      // the edit the voice note is talking about right now
  const [exp, setExp] = useState<ExportState>({ state: "idle" });
  const [expOpen, setExpOpen] = useState(false);
  const [projects, setProjects] = useState<Project[]>([]);
  const [menu, setMenu] = useState(false);

  const before = useRef<HTMLVideoElement>(null);
  const after = useRef<HTMLVideoElement>(null);
  const mattes = useRef<HTMLVideoElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const activeCard = useRef<HTMLDivElement>(null);
  const lastHeard = useRef<string | null>(null);
  const dragging = useRef(false);

  const [pid, setPid] = useState(() => new URLSearchParams(location.search).get("project") ?? HOME);
  const [showNew, setShowNew] = useState(false);
  const [running, setRunning] = useState(false);
  const [replaying, setReplaying] = useState(() => new URLSearchParams(location.search).get("run") === "replay");
  const [voiceOn, setVoiceOn] = useState(false);

  const open = useCallback((id: string) => {
    if (id !== (new URLSearchParams(location.search).get("project") ?? HOME)) history.pushState(null, "", id === HOME ? location.pathname : `?project=${id}`);
    setPid(id); setExp({ state: "idle" }); setExpOpen(false); setSelected(null); setPlaying(false); setManual(null); setMenu(false);
  }, []);

  useEffect(() => {
    let gone = false;
    api.get(pid).then((p) => {
      if (gone) return;
      setProject(p); setOffline(false);
      const live = p.live && p.live.state !== "done";
      setRunning(!!live);
      setStatus(p.replay ? "Replay · no model calls" : live ? "Processing" : "Live project");
    }).catch(() => {
      if (gone) return;
      if (pid !== HOME) { open(HOME); }
      else { setStatus("API offline"); setOffline(true); }
    });
    return () => { gone = true; };
  }, [pid, open]);

  useEffect(() => { if (menu) api.list().then(setProjects).catch(() => {}); }, [menu]);

  useEffect(() => {
    const onPop = () => setPid(new URLSearchParams(location.search).get("project") ?? HOME);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const finished = useCallback(() => {
    api.get(pid).then((p) => { setProject(p); setRunning(false); setStatus("Live project"); });
  }, [pid]);

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

  const toggle = useCallback(() => {
    const vs = [before.current, after.current, mattes.current].filter(Boolean) as HTMLVideoElement[];
    if (!vs.length) return;
    if (!vs[0].paused) { vs.forEach((v) => v.pause()); setPlaying(false); }
    else {
      if (vs[0].ended || vs[0].currentTime >= (vs[0].duration || 1) - 0.05) seek(0);
      vs.forEach((v) => v.play().catch(() => {})); setPlaying(true);
    }
  }, [seek]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.code === "Space") { e.preventDefault(); toggle(); }
      if (e.key === "m") setView((v) => (v === "masks" ? "compare" : "masks"));
      if (e.key === "1") setView("compare");
      if (e.key === "2") setView("before");
      if (e.key === "3") setView("after");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggle]);

  function onPointer(e: React.PointerEvent) {
    if (!dragging.current || !stage.current) return;
    const r = stage.current.getBoundingClientRect();
    setManual(Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)));
  }

  async function decide(op: Op, status: Op["status"]) {
    if (!project) return;
    const next = op.status === status ? "proposed" : status;
    setProject({ ...project, ops: project.ops.map((o) => (o.id === op.id ? { ...o, status: next } : o)) });
    setExp({ state: "idle" });
    try { await api.decide(project.id, op.id, next); } catch { /* the card already shows it; a reload re-reads the server */ }
  }

  async function startExport() {
    if (!project) return;
    setExpOpen(true);
    setExp(await api.exportStart(project.id));
  }
  useEffect(() => {
    if (exp.state !== "running" || !project) return;
    const t = setInterval(async () => setExp(await api.exportStatus(project.id)), 1500);
    return () => clearInterval(t);
  }, [exp.state, project]);

  const m = project?.media;
  const duration = m?.duration ?? 1;
  const ops = useMemo(() => (project?.ops ?? []).filter((o) => o.body.kind !== "ask"), [project]);
  const liveOp = ops.find((o) => time >= o.t0 && time <= o.t1);
  const approved = ops.filter((o) => o.status === "approved").length;
  const decided = ops.filter((o) => o.status !== "proposed").length;
  const noteFor = (o?: Op) => (o ? project?.notes.find((n) => n.id === o.note_id) : undefined);
  const client = project?.client ?? "Client";
  const split = view === "after" ? 1 : view === "before" || view === "masks" ? 0 : manual ?? autoSplit(time, ops);
  const editedHidden = liveOp?.status === "rejected";
  const k = liveOp ? KIND[liveOp.body.kind] : null;
  const [crumbA, crumbB] = (project?.name ?? "").split(" · ");
  const thumbs = m?.thumbs?.before ?? [];
  const ticks = useMemo(() => {
    const step = duration > 40 ? 5 : 2; const out: number[] = [];
    for (let t = 0; t <= duration + 1e-6; t += step) out.push(t);
    return out;
  }, [duration]);

  const activeId = liveOp?.id ?? selected;

  /** The editor's model rail: the voice note lights Whisper, a line tied to an edit lights the planner,
   *  playback inside an edit lights SAM 3.1 (the track) and the model that rebuilt the shot. */
  const heardOp = ops.find((o) => o.id === heard);
  const showOp = heardOp ?? (playing || view === "masks" ? liveOp : undefined);
  const rail = { listen: "done", plan: "done", track: "done", rebuild: "done" } as Record<RailKey, RailState>;
  const railSub: Partial<Record<RailKey, string>> = {
    listen: `${project?.transcript?.length ?? 0} lines`,
    plan: `${ops.length} edits${project?.planner?.seconds ? ` · ${project.planner.seconds.toFixed(0)} s` : ""}`,
    track: `${ops.length} targets tracked`,
    rebuild: `${ops.length} shots rebuilt`,
  };
  if (voiceOn) { rail.listen = "run"; railSub.listen = "hearing the note"; }
  if (heardOp) { rail.plan = "run"; railSub.plan = `→ ${opTitle(heardOp)}`; }
  if (showOp && !voiceOn) {
    rail.track = "run"; railSub.track = `“${showOp.body.target?.concept ?? ""}”${showOp.frames ? ` · ${showOp.frames} frames` : ""}`;
    if (view !== "masks") { rail.rebuild = "run"; railSub.rebuild = KIND[showOp.body.kind].done.toLowerCase(); }
  }
  const rebuildModel = showOp && showOp.body.kind !== "remove" ? { model: "Compositor", by: "Take Two" }
    : ops.some((o) => o.body.kind === "remove") ? { model: "VOID", by: "open weights" } : { model: "Compositor", by: "Take Two" };
  useEffect(() => { if (window.innerWidth > 860) activeCard.current?.scrollIntoView({ block: "nearest", behavior: "smooth" }); }, [activeId]);

  /** Voice note -> plan -> reveal: when the client starts talking about an edit, the viewer jumps to that shot and wipes it in. */
  const onHeard = useCallback((id: string | null) => {
    setHeard(id);
    if (!id || id === lastHeard.current) { if (!id) lastHeard.current = null; return; }
    lastHeard.current = id;
    const o = ops.find((x) => x.id === id); if (!o) return;
    seek(o.t0); setManual(null); setSelected(o.id);
    setView((v) => (v === "compare" || v === "masks" ? v : "compare"));
    const vs = [before.current, after.current, mattes.current].filter(Boolean) as HTMLVideoElement[];
    vs.forEach((v) => v.play().catch(() => {})); setPlaying(true);
  }, [ops, seek]);

  const scrub = (e: React.PointerEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    seek(Math.max(0, Math.min(duration, ((e.clientX - r.left) / r.width) * duration)));
  };
  const pick = (o: Op) => { setSelected(o.id); seek(o.t0 + 0.05); if (view === "before" || view === "after") setView("compare"); setManual(null); };

  return (
    <div className="app">
      <header className="top">
        <div className="logo" onClick={() => open(HOME)} role="button"><i />Take Two</div>
        <div className="crumb-wrap">
          <button className="crumb" onClick={() => setMenu(!menu)}>
            {crumbA}{crumbB && <> · <b>{cap(crumbB)}</b></>}<span className="chev" />
          </button>
          {menu && (
            <div className="menu tile" onMouseLeave={() => setMenu(false)}>
              <div className="mh">Projects</div>
              {[{ id: "wow", name: "Plaza spot · revision 2", sub: "Showcase · remove, sky, recolour" }, { id: "demo", name: "Lagos walk · revision 5", sub: "Live run, frozen · blur, title, pop" }].map((x) => (
                <button key={x.id} className={x.id === pid ? "on" : ""} onClick={() => open(x.id)}><b>{x.name}</b><span>{x.sub}</span></button>
              ))}
              {projects.filter((p) => !["wow", "demo"].includes(p.id) && p.media).slice(-4).reverse().map((p) => (
                <button key={p.id} className={p.id === pid ? "on" : ""} onClick={() => open(p.id)}><b>{p.name}</b><span>Live project · {p.ops.length} edits</span></button>
              ))}
            </div>
          )}
        </div>
        {!running && !replaying && (
          <div className="seg">
            {VIEWS.map((v) => <button key={v.k} className={view === v.k ? "on" : ""} onClick={() => setView(v.k)}>{v.label}</button>)}
          </div>
        )}
        <div className="top-r">
          <span className={`state ${offline ? "bad" : ""}`}><i />{status}</span>
          {project?.run && !running && !replaying && <button className="pill ghost runbtn" onClick={() => { setPlaying(false); [before.current, after.current, mattes.current].forEach((v) => v?.pause()); setReplaying(true); }}><i />Watch the run</button>}
          <button className="pill ghost" onClick={() => setShowNew(true)}>New revision</button>
          {!running && !replaying && (
            <div className="exp-wrap">
              <button className="pill blk" disabled={!approved} onClick={() => (exp.state === "idle" ? startExport() : setExpOpen(!expOpen))}>
                {exp.state === "running" ? <><span className="spin" />Rendering</> : `Export ${approved || ""} approved`.replace("  ", " ")}<span className="arrow">↓</span>
              </button>
              {expOpen && exp.state !== "idle" && (
                <div className="exp tile">
                  {exp.state === "running" && <><b>Rendering the approved edits</b><p>Only what you approved goes into the file. Masks are cached, nothing is re-tracked.</p><div className="bar"><i /></div></>}
                  {exp.state === "done" && exp.url && <>
                    <b>Ready</b><p>{approved} approved edit{approved === 1 ? "" : "s"}, rendered into the cut.</p>
                    <a className="pill blk full" href={media(exp.url)} download>Download MP4</a>
                    {!project?.planner?.baked && project && <a className="pill ghost2 full" href={media(`/projects/${project.id}/export.xml`)} download>Premiere / Resolve XML</a>}
                  </>}
                  {exp.state === "error" && <><b>Export stopped</b><p className="err">{exp.error}</p></>}
                </div>
              )}
            </div>
          )}
        </div>
      </header>
      {showNew && <NewProject onClose={() => setShowNew(false)} onStarted={(id) => { setShowNew(false); setRunning(true); setProject(null); open(id); }} />}

      {running || (replaying && project?.run) ? (
        <LiveRun key={pid + (replaying ? ":replay" : "")} pid={pid} replay={replaying ? project?.run : null}
          onDone={replaying ? () => setReplaying(false) : finished} onReplay={() => { setRunning(false); open(HOME); }} />
      ) : (
        <>
          <div className="railcell"><ModelRail state={rail} sub={railSub} rebuild={rebuildModel} /></div>
          <section className="viewcell">
            <div className={`viewer ${view}`} ref={stage} onPointerMove={onPointer} onPointerUp={() => (dragging.current = false)} onPointerLeave={() => (dragging.current = false)}>
              {m ? (
                <>
                  <video key={`b-${project?.id}`} ref={before} muted playsInline preload="auto" onEnded={() => setPlaying(false)} onClick={toggle}>{sources(m.before_url ?? m.proxy_url)}</video>
                  {m.after_url && (
                    <video key={`a-${project?.id}`} ref={after} className="after" muted playsInline preload="auto" onClick={toggle}
                      style={{ clipPath: `inset(0 ${(1 - split) * 100}% 0 0)`, opacity: editedHidden ? 0 : 1 }}>{sources(m.after_url)}</video>
                  )}
                  {m.mattes_url && <video key={`m-${project?.id}`} ref={mattes} className="mattes" muted playsInline preload="auto" style={{ opacity: view === "masks" ? 0.85 : 0 }}>{sources(m.mattes_url)}</video>}
                  {view === "compare" && m.after_url && (
                    <div className={`wipe ${split <= 0.02 || split >= 0.98 ? "rest" : ""}`} style={{ left: `${split * 100}%` }}
                      onPointerDown={(e) => { dragging.current = true; e.preventDefault(); setManual(split <= 0.001 ? 0.5 : split); }}>
                      <span className="knob">‹ ›</span>
                    </div>
                  )}
                  {/* labels: always say which side is which, so a reveal never reads as a glitch */}
                  {view === "masks" ? (
                    <span className="tag t-l dark"><i className="pulse" style={{ background: k?.colour ?? "#fff" }} />SAM 3.1 MASKS{liveOp ? ` · ${liveOp.body.target?.concept}` : ""}</span>
                  ) : (
                    <>
                      {split > 0.03 && !editedHidden && (
                        <span className="tag t-l edited" style={{ background: k?.colour ?? "#0D0D0D" }}>EDITED{k ? <em>{k.done}</em> : null}</span>
                      )}
                      {split < 0.97 && <span className="tag t-r dark">ORIGINAL</span>}
                      {editedHidden && <span className="tag t-l rej">REJECTED · showing original</span>}
                    </>
                  )}
                  {liveOp && noteFor(liveOp) && (
                    <div className="cap" key={liveOp.id}>
                      <span className="vb" />
                      <div><div className="ct">“{shortQuote(noteFor(liveOp)!.text)}”</div>
                        <div className="cs">{client} · voice note{noteFor(liveOp)!.said ? ` · ${fmtf(noteFor(liveOp)!.said![0])}` : ""}</div></div>
                    </div>
                  )}
                  <span className="tcbig">{fmtf(time)}</span>
                  {!playing && time < 0.05 && <button className="bigplay" onClick={toggle} aria-label="Play" />}
                </>
              ) : (
                <div className="empty"><b>{offline ? "Can't reach the editor's server" : "Loading the project"}</b><span>{offline ? "Start the API on port 8000, then reload." : status}</span></div>
              )}
            </div>
          </section>

          <aside className="side">
            {m?.voice_url && (
              <VoiceNote url={media(m.voice_url)!} client={client} lines={project?.transcript ?? []} notes={project?.notes ?? []} ops={ops}
                onHeard={onHeard} onPick={pick} onPlaying={setVoiceOn} />
            )}
            <div className="sh"><h2 className="disp">{ops.length} edit{ops.length === 1 ? "" : "s"} from {client}</h2><span className="n">{decided ? `${approved} of ${ops.length} approved` : "Review each one"}</span></div>
            <div className="cards">
              {ops.map((o) => {
                const kk = KIND[o.body.kind]; const n = noteFor(o);
                const sel = liveOp ? liveOp.id === o.id : selected === o.id;
                return (
                  <div key={o.id} ref={sel ? activeCard : undefined} className={`card tile ${o.status} ${sel ? "sel" : ""} ${heard === o.id ? "heard" : ""}`} style={{ ["--c" as string]: kk.colour }} onClick={() => pick(o)}>
                    <div className="ch"><span className="dot" /><span className="kn">{opTitle(o)}</span><span className="tcs">{fmtf(o.t0)} – {fmtf(o.t1)}</span></div>
                    {n && <div className="q disp">“{sel ? n.text : shortQuote(n.text)}”</div>}
                    {sel && (
                      <div className="meta">
                        <span>Tracked</span><b>{o.body.target?.concept}{o.body.kind === "remove" && o.body.also ? ` + ${String(o.body.also)}` : ""}</b>
                        <span className="sep">·</span><span>{o.frames ? `${o.frames} frames` : "every frame"}</span><span className="sep">·</span><span>SAM 3.1</span>
                      </div>
                    )}
                    <div className="acts">
                      {o.status === "proposed" ? (
                        <>
                          <button className="pill blk ap" onClick={(e) => { e.stopPropagation(); decide(o, "approved"); }}>Approve</button>
                          <button className="pill rj" onClick={(e) => { e.stopPropagation(); decide(o, "rejected"); }}>Reject</button>
                        </>
                      ) : (
                        <>
                          <span className={o.status === "approved" ? "okb" : "nob"}>{o.status === "approved" ? "✓ Approved" : "Rejected"}</span>
                          <button className="undo" onClick={(e) => { e.stopPropagation(); decide(o, o.status); }}>Undo</button>
                        </>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </aside>
        </>
      )}

      <footer className="tl tile" style={running || (replaying && project?.run) ? { display: "none" } : undefined}>
        <div className="tlh">
          <button className={`play ${playing ? "on" : ""}`} onClick={toggle} aria-label={playing ? "Pause" : "Play"} />
          <span className="tcn">{fmtf(time)} <span>/ {fmtf(duration)}</span></span>
          <div className="tl-r">
            <button className={`chip ${manual === null ? "on" : ""}`} onClick={() => setManual(manual === null ? 0.5 : null)} title="Sweep between original and edited inside each edit">
              <i />Auto wipe
            </button>
            <span className="legend">{[...new Set(ops.map((o) => o.body.kind))].map((kd) => <span key={kd}><i style={{ background: KIND[kd].colour }} />{KIND[kd].label}</span>)}<span><i style={{ background: CLIENT }} />Client</span></span>
          </div>
        </div>
        <div className="lanes" onPointerDown={(e) => { (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId); scrub(e); }} onPointerMove={(e) => { if (e.buttons) scrub(e); }}>
          <div className="ruler">{ticks.map((t) => <span key={t} style={{ left: `${(t / duration) * 100}%` }}>{fmtf(t).replace(/\.\d$/, "")}</span>)}</div>
          <div className="lane picture">
            <div className="strip">{thumbs.length ? thumbs.map((u) => <img key={u} src={media(u)} alt="" draggable={false} />) : <div className="nostrip" />}</div>
            {ops.map((o) => <div key={o.id} className="cut" style={{ left: `${(o.t0 / duration) * 100}%` }} />)}
          </div>
          <div className="lane edits">
            {ops.map((o) => (
              <div key={o.id} className={`clip ${o.status} ${selected === o.id ? "sel" : ""}`} style={{ left: `calc(${(o.t0 / duration) * 100}% + 2px)`, width: `calc(${((o.t1 - o.t0) / duration) * 100}% - 4px)`, ["--c" as string]: KIND[o.body.kind].colour }}
                onPointerDown={(e) => { e.stopPropagation(); pick(o); }}>
                <i />{o.status === "approved" ? "✓ " : ""}{opTitle(o)}
              </div>
            ))}
          </div>
          <div className="lane client">
            {(project?.notes ?? []).filter((n) => n.t != null).map((n) => (
              <div key={n.id} className="pin" style={{ left: `${((n.t ?? 0) / duration) * 100}%` }}>
                <span className="b" /><span className="pq">“{shortQuote(n.text, 34)}”</span>
              </div>
            ))}
          </div>
          <div className="phl" style={{ left: `${(time / duration) * 100}%` }} />
        </div>
        <div className="lablist"><span>PICTURE</span><span>EDITS</span><span>CLIENT</span></div>
      </footer>
    </div>
  );
}

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** The client's ask without the throat-clearing: from the clause that carries the instruction to the end of its sentence. */
function shortQuote(text: string, max = 60): string {
  const verb = /\b(get rid|remove|make|making|blur|put|change|swap|turn|needs?|want)\b/i;
  const sentences = text.split(/(?<=[.?!])\s+/).map((s) => s.trim()).filter(Boolean);
  const sent = [...sentences].reverse().find((p) => verb.test(p) && !/^that's/i.test(p)) ?? sentences[sentences.length - 1] ?? text;
  const clauses = sent.split(/,\s+/);
  let i = clauses.findIndex((c) => verb.test(c)); if (i < 0) i = 0;
  if (i > 0 && clauses.slice(i).join(", ").split(/\s+/).length < 4) i -= 1;
  let s = clauses.slice(i).join(", ").replace(/^(and|then|so|also)\s+/i, "").replace(/[.!]$/, "");
  s = s.charAt(0).toLowerCase() + s.slice(1);
  return s.length > max ? s.slice(0, max - 1).replace(/\s+\S*$/, "") + "…" : s;
}
