import { useEffect, useMemo, useRef, useState } from "react";
import { fmtf, KIND, liveApi, media, opTitle, replayAt, replayClock, replayLength, type LiveState, type Op, type Stage } from "./api";
import { ModelRail, type RailKey, type RailState } from "./ModelRail";

const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const stageTime = (s: Stage, now: number) => (s.t0 ? ((s.t1 ?? now) - s.t0) : 0);
const JOB_WORD: Record<string, string> = {
  pending: "waiting", submitting: "uploading shot", queued: "in queue", tracking: "tracking", decoding: "decoding masks",
  painting: "painting", done: "done", retrying: "retrying",
};

/** Upload a clip and the client's voice note; starts a live run. */
export function NewProject({ onStarted, onClose }: { onStarted: (id: string) => void; onClose: () => void }) {
  const [clip, setClip] = useState<File | null>(null);
  const [voice, setVoice] = useState<File | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const pick = (f: File | undefined | null) => {
    if (!f) return;
    if (f.type.startsWith("video/")) setClip(f); else if (f.type.startsWith("audio/")) setVoice(f);
  };
  async function go() {
    if (!clip || !voice) return;
    setBusy(true); setErr("");
    try {
      const r = await liveApi.start(clip, voice, clip.name.replace(/\.[^.]+$/, "") + " · revision");
      onStarted(r.id);
    } catch (e) {
      const m = String(e).replace(/^Error: /, "");
      const detail = m.match(/"detail":"([^"]+)"/);
      setErr(detail ? detail[1] : m); setBusy(false);
    }
  }
  return (
    <div className="modal" onClick={onClose}>
      <div className="mcard tile" onClick={(e) => e.stopPropagation()}
        onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); Array.from(e.dataTransfer.files).forEach(pick); }}>
        <h3 className="disp">New revision</h3>
        <p className="sub">Drop the cut and the client's voice note. Take Two listens, plans each change, tracks every target frame by frame and renders a second take for you to approve.</p>
        <label className={`pick ${clip ? "has" : ""}`}>
          <input type="file" accept="video/*" onChange={(e) => pick(e.target.files?.[0])} />
          <i className="ico film" /><b>The cut</b><span>{clip ? `${clip.name} · ${(clip.size / 1e6).toFixed(1)} MB` : "mp4 / mov, up to 60 s"}</span>
        </label>
        <label className={`pick ${voice ? "has" : ""}`}>
          <input type="file" accept="audio/*" onChange={(e) => pick(e.target.files?.[0])} />
          <i className="ico mic" /><b>Client voice note</b><span>{voice ? `${voice.name} · ${(voice.size / 1e6).toFixed(1)} MB` : "mp3 / m4a / ogg / wav"}</span>
        </label>
        {err && <p className="err">{err}</p>}
        <div className="row">
          <button className="pill ghost2" onClick={onClose}>Cancel</button>
          <button className="pill blk" disabled={!clip || !voice || busy} onClick={go}>{busy ? "Uploading…" : "Run Take Two"}</button>
        </div>
      </div>
    </div>
  );
}

/** The progress view: every stage streams in, masks paint in live, then it hands over to the editor. */
export function LiveRun({ pid, onDone, onReplay, replay }: { pid: string; onDone: () => void; onReplay: () => void; replay?: LiveState | null }) {
  const [live, setLive] = useState<LiveState | null>(null);
  const [wall, setWall] = useState(Date.now() / 1000);
  const [tau, setTau] = useState(0);          // replay: display seconds since the replay started
  const [gen, setGen] = useState(0);
  const doneRef = useRef(false);

  useEffect(() => (replay ? undefined : liveApi.watch(pid, setLive)), [pid, gen, replay]);
  useEffect(() => {
    if (replay) {
      const t0 = performance.now(); let raf = 0;
      const tick = () => { const x = (performance.now() - t0) / 1000; setTau(x); if (x < replayLength(replay) + 0.1) raf = requestAnimationFrame(tick); };
      raf = requestAnimationFrame(tick);
      return () => cancelAnimationFrame(raf);
    }
    const t = setInterval(() => setWall(Date.now() / 1000), 200); return () => clearInterval(t);
  }, [replay]);
  const T = replay ? replayClock(replay, tau) : 0;
  const s = replay ? replayAt(replay, T) : live;
  const now = replay ? T : wall;
  useEffect(() => {
    if (s?.state === "done" && !doneRef.current) { doneRef.current = true; setTimeout(onDone, replay ? 3200 : 2200); }
  }, [s?.state, onDone, replay]);

  const elapsed = s ? ((s.finished ?? now) - s.started) : 0;
  const cur = s?.stages.find((x) => x.status === "running") ?? s?.stages.find((x) => x.status === "error");
  // while masks decode: the newest painted frame; once tracking is done: a flipbook of the masked cut in time order
  const [flip, setFlip] = useState(0);
  const ordered = useMemo(() => [...(s?.paint ?? [])].sort((a, b) => a.t - b.t), [s?.paint]);
  const flipping = !!s && (s.state !== "running" || s.stages.find((x) => x.key === "masks")?.status === "done") && ordered.length > 1;
  useEffect(() => {
    if (!flipping) return;
    const t = setInterval(() => setFlip((f) => f + 1), 140);
    return () => clearInterval(t);
  }, [flipping]);
  const lastPaint = flipping ? ordered[flip % ordered.length] : s?.paint[s.paint.length - 1];
  const paintedFor = useMemo(() => {
    const m: Record<string, { url: string; frame: number; colour: string }[]> = {};
    for (const p of s?.paint ?? []) (m[p.job] ??= []).push(p);
    return m;
  }, [s?.paint]);
  const jobLabel = (id?: string) => { const j = s?.jobs.find((x) => x.id === id); return j ? `“${j.concept}” · shot ${j.shot}` : ""; };
  const ops = (s?.ops ?? []) as Op[];
  const noteFor = (o: Op) => s?.notes?.find((n) => n.id === o.note_id);

  async function retry() { await liveApi.retry(pid); doneRef.current = false; setGen((g) => g + 1); }

  if (!s) return <section className="viewcell"><div className="viewer"><div className="empty"><b>Connecting…</b></div></div></section>;
  const phase = s.state === "error" ? "error" : s.state === "done" ? "done" : cur?.key ?? "ingest";
  const RK: Record<string, RailKey> = { transcribe: "listen", plan: "plan", masks: "track", render: "rebuild" };
  const rail = { listen: "wait", plan: "wait", track: "wait", rebuild: "wait" } as Record<RailKey, RailState>;
  const railSub: Partial<Record<RailKey, string>> = {};
  for (const x of s.stages) {
    const k = RK[x.key]; if (!k) continue;
    rail[k] = x.status === "done" || x.status === "skipped" ? "done" : x.status === "running" ? "run" : "wait";
    if (x.status === "done") railSub[k] = `${stageTime(x, now).toFixed(1)} s`;
  }
  const jobsDone = s.jobs.filter((j) => j.status === "done").length;
  if (rail.listen === "run") railSub.listen = "transcribing the note";
  if (rail.plan === "run") railSub.plan = `reading ${s.keyframes.length || s.shots.length} frames + the note`;
  if (rail.track === "run") railSub.track = s.jobs.length ? `${jobsDone} of ${s.jobs.length} targets` : "starting";
  if (rail.rebuild === "run") railSub.rebuild = `${Math.round(s.render * 100)}% rendered`;
  const hasRemove = ((replay?.ops ?? ops) as Op[]).some((o) => o.body.kind === "remove");
  const rebuild = hasRemove ? { model: "VOID", by: "open weights" } : { model: "Compositor", by: "Take Two" };
  const doneN = s.stages.filter((x) => x.status === "done" || x.status === "skipped").length;

  return (
    <>
      <div className="railcell"><ModelRail state={rail} sub={railSub} rebuild={rebuild} replay={!!replay} /></div>
      <section className="viewcell">
        <div className="viewer monitor">
          {(phase === "ingest" || phase === "transcribe" || phase === "plan") && (
            <div className={`sheet ${phase === "plan" ? "scan" : ""}`}>
              {s.keyframes.length ? s.keyframes.map((k, i) => (
                <figure key={k.t} style={{ animationDelay: `${i * 0.08}s` }}>
                  <img src={media(k.url)} alt="" /><figcaption>{fmtf(k.t)}</figcaption>
                </figure>
              )) : s.shots.map((sh) => <figure key={sh.index}><img src={media(sh.thumb)} alt="" /><figcaption>shot {sh.index}</figcaption></figure>)}
            </div>
          )}
          {(phase === "masks" || phase === "render" || phase === "done" || (phase === "error" && lastPaint)) && (
            <div className="paintview">
              {lastPaint ? <img key={flipping ? "flip" : lastPaint.url} src={media(lastPaint.url)} alt="" /> : <div className="waiting">SAM 3.1 is tracking {s.jobs.length} target{s.jobs.length === 1 ? "" : "s"}…</div>}
              {lastPaint && phase !== "render" && <span className="tag t-l dark"><i className="pulse" style={{ background: lastPaint.colour }} />SAM 3.1 · {jobLabel(lastPaint.job)} · frame {lastPaint.frame}</span>}
              {phase === "render" && <div className="renderbar"><span>Rendering the second take · {s.stages.find((x) => x.key === "render")?.msg}</span><div><b style={{ width: `${s.render * 100}%` }} /></div></div>}
            </div>
          )}
          {phase === "done" && <div className="endcard"><small>Revision done in</small><strong className="disp">{mmss(elapsed)}</strong><span>{replay ? "Recorded run · compute time, queue waits not shown" : "Opening the editor…"}</span></div>}
          {phase === "error" && s.error && (
            <div className="errcard tile">
              <small>Stopped at · {s.stages.find((x) => x.key === s.error!.stage)?.label}</small>
              <strong className="disp">{s.error.message}</strong>
              <div className="row">
                <button className="pill blk" onClick={retry}>Retry from here</button>
                <button className="pill ghost2" onClick={onReplay}>Open the showcase project</button>
              </div>
              {s.error.detail && <details><summary>Details</summary><pre>{s.error.detail}</pre></details>}
            </div>
          )}
          <div className={`timer ${replay ? "rp" : ""}`}><i />{replay ? "Replay · " : ""}{mmss(elapsed)}</div>
        </div>
      </section>

      <aside className="side">
        <div className="work tile">
          <div className="wh"><h2 className="disp">{phase === "done" ? "Second take ready" : phase === "error" ? "Paused" : "Take Two is working"}</h2><span className="big disp">{mmss(elapsed)}</span></div>
          <div className="prog"><i style={{ width: `${(doneN / Math.max(1, s.stages.length)) * 100}%` }} /></div>
          <ol className="stages">
            {s.stages.map((x) => (
              <li key={x.key} className={x.status}>
                <span className="sd" />
                <div><b>{x.label}</b>{x.msg && <small>{x.msg}</small>}</div>
                <span className="tc">{x.t0 ? `${stageTime(x, now).toFixed(1)}s` : ""}</span>
              </li>
            ))}
          </ol>
        </div>
        {s.transcript && (
          <div className="voice tile">
            <div className="vh"><span className="vb static" /><div className="vt"><b>Client voice note</b><span>{s.transcript.length} lines · transcribed</span></div></div>
            <ol className="lines">
              {s.transcript.map((l, i) => <li key={i} className="in" style={{ animationDelay: `${i * 0.06}s` }}><span className="tc">{mmss(l.t0)}</span><span className="tx">{l.text}</span></li>)}
            </ol>
          </div>
        )}
        {ops.length > 0 && (
          <div className="cards">
            {ops.map((o, i) => {
              const k = KIND[o.body.kind];
              return (
                <div key={o.id} className="card tile in" style={{ ["--c" as string]: k.colour, animationDelay: `${i * 0.2}s` }}>
                  <div className="ch"><span className="dot" /><span className="kn">{o.body.kind === "ask" ? "Question for you" : opTitle(o)}</span><span className="tcs">{fmtf(o.t0)} – {fmtf(o.t1)}</span></div>
                  {o.body.kind === "ask" ? <div className="q disp sm">{String(o.body.question ?? "")}</div> : noteFor(o) && <div className="q disp sm">“{noteFor(o)!.text}”</div>}
                </div>
              );
            })}
          </div>
        )}
      </aside>

      <footer className="tl tile jobs-tl">
        <div className="tlh"><span className="tcn">Tracking</span><span className="legend">{s.jobs.length ? `${s.jobs.filter((j) => j.status === "done").length} of ${s.jobs.length} targets tracked · SAM 3.1` : "waiting for the plan"}</span></div>
        <ul className="jobs">
          {s.jobs.map((jb) => (
            <li key={jb.id} className={jb.status} style={{ ["--c" as string]: paintedFor[jb.id]?.[0]?.colour ?? KIND[jb.kind]?.colour }}>
              <div className="row">
                <i />
                <b>{jb.concept}</b><span className="dim">shot {jb.shot}</span>
                <span className="st">{JOB_WORD[jb.status] ?? jb.status}{jb.cached ? " · cached" : ""}{jb.objects != null && jb.status === "done" ? ` · ${jb.objects} object${jb.objects === 1 ? "" : "s"}` : ""}</span>
              </div>
              <div className="strip">
                {(paintedFor[jb.id] ?? []).map((p) => <img key={p.url} src={media(p.url)} alt="" />)}
                {!(paintedFor[jb.id]?.length) && <span className={`pulse-bar ${jb.status}`} />}
              </div>
            </li>
          ))}
          {!s.jobs.length && s.shots.map((sh) => <li key={sh.index} className="pending"><div className="row"><i /><b>Shot {sh.index}</b><span className="dim">{fmtf(sh.t0)} – {fmtf(sh.t1)}</span></div><div className="strip"><img src={media(sh.thumb)} alt="" /></div></li>)}
        </ul>
      </footer>
    </>
  );
}
