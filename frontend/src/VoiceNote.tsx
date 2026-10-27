import { useEffect, useMemo, useRef, useState } from "react";
import { CLIENT, KIND, type Line, type Note, type Op } from "./api";

const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const BARS = 64;

/** The client's voice note: waveform, transcript that follows the audio, and each line tied to the edit it asked for. */
export function VoiceNote({ url, client, lines, notes, ops, onHeard, onPick }: {
  url: string; client: string; lines: Line[]; notes: Note[]; ops: Op[];
  onHeard: (opId: string | null) => void; onPick: (o: Op) => void;
}) {
  const audio = useRef<HTMLAudioElement>(null);
  const [t, setT] = useState(0);
  const [dur, setDur] = useState(0);
  const [on, setOn] = useState(false);
  const [peaks, setPeaks] = useState<number[] | null>(null);
  const [open, setOpen] = useState(true);
  const list = useRef<HTMLOListElement>(null);

  // real waveform from the file itself
  useEffect(() => {
    let gone = false;
    (async () => {
      try {
        const buf = await (await fetch(url)).arrayBuffer();
        const ctx = new (window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext)();
        const a = await ctx.decodeAudioData(buf); ctx.close();
        const ch = a.getChannelData(0); const step = Math.floor(ch.length / BARS); const out: number[] = [];
        for (let i = 0; i < BARS; i++) { let s = 0; for (let j = i * step; j < (i + 1) * step; j += 16) s = Math.max(s, Math.abs(ch[j])); out.push(s); }
        const mx = Math.max(...out, 1e-3);
        if (!gone) { setPeaks(out.map((v) => 0.12 + 0.88 * Math.pow(v / mx, 0.8))); setDur(a.duration); }
      } catch { if (!gone) setPeaks(Array.from({ length: BARS }, () => 0.3)); }
    })();
    return () => { gone = true; };
  }, [url]);

  useEffect(() => {
    if (!on) return;
    let raf = 0;
    const tick = () => { if (audio.current) setT(audio.current.currentTime); raf = requestAnimationFrame(tick); };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [on]);

  /** which edit each transcript line belongs to (by the note's spoken time, else by shared words) */
  const lineOp = useMemo(() => lines.map((l) => {
    for (const o of ops) {
      const n = notes.find((x) => x.id === o.note_id);
      if (n?.said && l.t0 < n.said[1] - 0.05 && l.t1 > n.said[0] + 0.05) return o;
      if (!n?.said && n && overlap(n.text, l.text) >= 0.5) return o;
    }
    return null;
  }), [lines, notes, ops]);

  const cur = lines.findIndex((l) => t >= l.t0 && t < l.t1 + 0.3);
  const heard = on && cur >= 0 ? lineOp[cur] : null;
  useEffect(() => { onHeard(heard?.id ?? null); }, [heard?.id, onHeard]);
  useEffect(() => {
    if (cur < 0 || !list.current) return;
    const el = list.current.children[cur] as HTMLElement | undefined;
    if (el) list.current.scrollTo({ top: Math.max(0, el.offsetTop - 30), behavior: "smooth" });
  }, [cur]);

  const play = () => {
    const a = audio.current; if (!a) return;
    if (a.paused) { a.play().then(() => setOn(true)).catch(() => {}); } else { a.pause(); setOn(false); }
  };
  const jump = (s: number) => { const a = audio.current; if (!a) return; a.currentTime = s; setT(s); if (a.paused) a.play().then(() => setOn(true)).catch(() => {}); };
  const D = dur || (lines.length ? lines[lines.length - 1].t1 : 1);

  return (
    <div className={`voice tile ${open ? "" : "shut"}`}>
      <audio ref={audio} src={url} preload="auto" onEnded={() => { setOn(false); onHeard(null); }} onPause={() => setOn(false)} />
      <div className="vh">
        <button className={`vb ${on ? "on" : ""}`} onClick={play} aria-label={on ? "Pause voice note" : "Play voice note"} />
        <div className="vt">
          <b>{client}'s voice note</b>
          <span>{on || t > 0 ? `${mmss(t)} / ${mmss(D)}` : `${mmss(D)} · transcribed`}</span>
        </div>
        <div className="wave" onClick={(e) => { const r = e.currentTarget.getBoundingClientRect(); jump(((e.clientX - r.left) / r.width) * D); }}>
          {(peaks ?? Array.from({ length: BARS }, () => 0.15)).map((p, i) => {
            const at = (i + 0.5) / BARS * D;
            const o = lines.findIndex((l) => at >= l.t0 && at < l.t1);
            const op = o >= 0 ? lineOp[o] : null;
            return <i key={i} className={at <= t ? "past" : ""} style={{ height: `${p * 100}%`, background: at <= t ? (op ? KIND[op.body.kind].colour : CLIENT) : undefined }} />;
          })}
        </div>
        <button className="fold" onClick={() => setOpen(!open)} aria-label="Show transcript">{open ? "–" : "+"}</button>
      </div>
      {open && (
        <ol className="lines" ref={list}>
          {lines.map((l, i) => {
            const o = lineOp[i];
            return (
              <li key={i} className={`${i === cur && (on || t > 0) ? "now" : ""} ${o ? "ask" : ""}`} style={o ? { ["--c" as string]: KIND[o.body.kind].colour } : undefined}
                onClick={() => { jump(l.t0); if (o) onPick(o); }}>
                <span className="tc">{mmss(l.t0)}</span><span className="tx">{l.text}</span>{o && <span className="to">{KIND[o.body.kind].label}</span>}
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}

function overlap(a: string, b: string): number {
  const w = (s: string) => new Set(s.toLowerCase().match(/[a-z']{3,}/g) ?? []);
  const A = w(a), B = w(b); if (!B.size) return 0;
  let n = 0; B.forEach((x) => { if (A.has(x)) n++; });
  return n / B.size;
}
