import { useEffect, useMemo, useRef, useState } from "react";
import { fmtf, KIND, liveApi, media, type LiveState, type Op, type Stage } from "./api";

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
      <div className="card" onClick={(e) => e.stopPropagation()}
        onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); Array.from(e.dataTransfer.files).forEach(pick); }}>
        <h3>New revision</h3>
        <p className="sub">Drop the cut and the client's voice note. Take Two listens, plans each change, tracks the targets and renders a second take.</p>
        <label className={`pick ${clip ? "has" : ""}`}>
          <input type="file" accept="video/*" onChange={(e) => pick(e.target.files?.[0])} />
          <b>The cut</b><span>{clip ? `${clip.name} · ${(clip.size / 1e6).toFixed(1)} MB` : "mp4 / mov, up to 60 s"}</span>
        </label>
        <label className={`pick ${voice ? "has" : ""}`}>
          <input type="file" accept="audio/*" onChange={(e) => pick(e.target.files?.[0])} />
          <b>Client voice note</b><span>{voice ? `${voice.name} · ${(voice.size / 1e6).toFixed(1)} MB` : "mp3 / m4a / ogg / wav"}</span>
        </label>
        {err && <p className="err">{err}</p>}
        <div className="row">
          <button className="ghost" onClick={onClose}>Cancel</button>
          <button className="go" disabled={!clip || !voice || busy} onClick={go}>{busy ? "Uploading…" : "Run Take Two"}</button>
        </div>
      </div>
    </div>
  );
}

/** The progress view: every stage streams in, masks paint in live, then it hands over to the editor. */
export function LiveRun({ pid, onDone, onReplay }: { pid: string; onDone: () => void; onReplay: () => void }) {
  const [s, setS] = useState<LiveState | null>(null);
  const [now, setNow] = useState(Date.now() / 1000);
  const [gen, setGen] = useState(0);
  const doneRef = useRef(false);

  useEffect(() => liveApi.watch(pid, setS), [pid, gen]);
  useEffect(() => { const t = setInterval(() => setNow(Date.now() / 1000), 200); return () => clearInterval(t); }, []);
  useEffect(() => {
    if (s?.state === "done" && !doneRef.current) { doneRef.current = true; setTimeout(onDone, 2200); }
  }, [s?.state, onDone]);

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
    const m: Record<string, { url: string; frame: number }[]> = {};
    for (const p of s?.paint ?? []) (m[p.job] ??= []).push(p);
    return m;
  }, [s?.paint]);
  const jobLabel = (id?: string) => { const j = s?.jobs.find((x) => x.id === id); return j ? `“${j.concept}” · shot ${j.shot}` : ""; };
  const ops = (s?.ops ?? []) as Op[];
  const noteFor = (o: Op) => s?.notes?.find((n) => n.id === o.note_id);

  async function retry() { await liveApi.retry(pid); doneRef.current = false; setGen((g) => g + 1); }

  if (!s) return <section className="stagewrap"><div className="drop"><strong>Connecting…</strong></div></section>;
  const phase = s.state === "error" ? "error" : s.state === "done" ? "done" : cur?.key ?? "ingest";

  return (
    <>
      <section className="stagewrap liverun">
        <div className="monitor">
          {(phase === "ingest" || phase === "transcribe" || phase === "plan") && (
            <div className={`sheet ${phase === "plan" ? "scan" : ""}`}>
              {s.keyframes.length ? s.keyframes.map((k, i) => (
                <figure key={k.t} style={{ animationDelay: `${i * 0.12}s` }}>
                  <img src={media(k.url)} alt="" /><figcaption>{fmtf(k.t)}</figcaption>
                </figure>
              )) : s.shots.map((sh) => <figure key={sh.index}><img src={media(sh.thumb)} alt="" /><figcaption>shot {sh.index}</figcaption></figure>)}
            </div>
          )}
          {(phase === "masks" || phase === "render" || phase === "done" || (phase === "error" && lastPaint)) && (
            <div className="paintview">
              {lastPaint ? <img key={flipping ? "flip" : lastPaint.url} src={media(lastPaint.url)} alt="" /> : <div className="waiting">SAM 3.1 is tracking {s.jobs.length} target(s)…</div>}
              {lastPaint && phase !== "render" && <span className="cap"><i style={{ background: lastPaint.colour }} />SAM 3.1 · {jobLabel(lastPaint.job)} · frame {lastPaint.frame}</span>}
              {phase === "render" && <div className="renderbar"><span>Rendering take two · {s.stages.find((x) => x.key === "render")?.msg}</span><div><b style={{ width: `${s.render * 100}%` }} /></div></div>}
            </div>
          )}
          {phase === "done" && <div className="endcard"><small>Revision done in</small><strong>{mmss(elapsed)}</strong><span>opening the editor…</span></div>}
          {phase === "error" && s.error && (
            <div className="errcard">
              <small>Stopped at · {s.stages.find((x) => x.key === s.error!.stage)?.label}</small>
              <strong>{s.error.message}</strong>
              <div className="row">
                <button className="go" onClick={retry}>Retry from here</button>
                <button className="ghost" onClick={onReplay}>Open the replay project</button>
              </div>
              {s.error.detail && <details><summary>details</summary><pre>{s.error.detail}</pre></details>}
            </div>
          )}
          <div className="timer">{mmss(elapsed)}</div>
        </div>

        {s.jobs.length > 0 && (
          <ul className="jobs">
            {s.jobs.map((jb) => (
              <li key={jb.id} className={jb.status}>
                <div className="row">
                  <i style={{ background: KIND[jb.kind]?.colour }} />
                  <b>{jb.concept}</b><span className="dim">shot {jb.shot}</span>
                  <span className="st">{JOB_WORD[jb.status] ?? jb.status}{jb.cached ? " · cached" : ""}{jb.objects != null && jb.status === "done" ? ` · ${jb.objects} object${jb.objects === 1 ? "" : "s"}` : ""}</span>
                </div>
                <div className="strip">
                  {(paintedFor[jb.id] ?? []).map((p) => <img key={p.url} src={media(p.url)} alt="" />)}
                  {!(paintedFor[jb.id]?.length) && <span className={`pulse ${jb.status}`} />}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <aside className="side">
        <div className="block">
          <h2>Take Two is working <span className="count">{mmss(elapsed)}</span></h2>
          <ol className="stages">
            {s.stages.map((x) => (
              <li key={x.key} className={x.status}>
                <span className="dot" />
                <div><b>{x.label}</b>{x.msg && <small>{x.msg}</small>}</div>
                <span className="tc">{x.t0 ? `${stageTime(x, now).toFixed(1)}s` : ""}</span>
              </li>
            ))}
          </ol>
        </div>
        {s.transcript && (
          <div className="block">
            <h2>Client voice note</h2>
            <ol className="transcript">
              {s.transcript.map((l, i) => <li key={i} className="in"><span className="tc">{mmss(l.t0)}</span>{l.text}</li>)}
            </ol>
          </div>
        )}
        {ops.length > 0 && (
          <div className="block grow">
            <h2>Proposed edits <span className="count">{ops.length}</span></h2>
            <ul className="ops">
              {ops.map((o, i) => {
                const k = KIND[o.body.kind];
                return (
                  <li key={o.id} className="op in" style={{ borderLeftColor: k.colour, animationDelay: `${i * 0.25}s` }}>
                    <div className="row">
                      <span className="kind" style={{ color: k.colour }}>{k.label}</span>
                      <span className="what">{o.body.kind === "title_behind" ? `“${o.body.text}” behind ${o.body.target?.concept}` : o.body.kind === "ask" ? String(o.body.question ?? "") : o.body.target?.concept}</span>
                      <span className="tc">{fmtf(o.t0)}–{fmtf(o.t1)}</span>
                    </div>
                    {noteFor(o) && <q>{noteFor(o)!.text}</q>}
                  </li>
                );
              })}
            </ul>
          </div>
        )}
      </aside>
    </>
  );
}
