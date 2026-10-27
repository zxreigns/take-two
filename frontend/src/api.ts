export const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export type Note = { id: string; t: number | null; text: string; source: "typed" | "voice"; said?: [number, number] };
export type OpKind = "blur" | "title_behind" | "spotlight" | "ask" | "remove" | "sky" | "recolour";
export type Op = {
  id: string; note_id: string; t0: number; t1: number;
  body: { kind: OpKind; target?: { concept: string; object_id: number | null }; text?: string; [k: string]: unknown };
  status: "proposed" | "approved" | "rejected"; rationale: string; frames?: number;
};
export type Line = { t0: number; t1: number; text: string };
export type Media = {
  proxy_url: string; duration: number; fps: number; width: number; height: number;
  before_url?: string; after_url?: string; mattes_url?: string; voice_url?: string | null;
  thumbs?: { before?: string[]; after?: string[] };
};
export type Project = {
  id: string; name: string; notes: Note[]; ops: Op[]; media: null | Media;
  transcript?: Line[] | null; planner?: { model?: string; seconds?: number; baked?: boolean }; replay?: boolean; client?: string;
  live?: LiveState | null;
};
export type ExportState = { state: "idle" | "running" | "done" | "error"; url?: string; error?: string; ops?: string[] };

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(API + path, init);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}
const json = (body: unknown): RequestInit => ({ method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });

export const api = {
  health: () => j<{ ok: boolean; model_key: boolean; planner: string }>("/health"),
  create: (name: string) => j<Project>("/projects", json({ name })),
  get: (id: string) => j<Project>(`/projects/${id}`),
  list: () => j<Project[]>("/projects"),
  upload: (id: string, file: File) => {
    const fd = new FormData(); fd.append("file", file);
    return j<Project>(`/projects/${id}/media`, { method: "POST", body: fd });
  },
  addNote: (id: string, text: string, t: number | null) => j<Project>(`/projects/${id}/notes`, json({ text, t, source: "typed" })),
  decide: (id: string, opId: string, status: Op["status"]) => j<Op>(`/projects/${id}/ops/${opId}`, json({ status })),
  exportStart: (id: string) => j<ExportState>(`/projects/${id}/export`, { method: "POST" }),
  exportStatus: (id: string) => j<ExportState>(`/projects/${id}/export`),
};

export const media = (path?: string | null) => (path ? `${API}${path}` : undefined);

/** "0:12 blur the phone" -> {t: 12, text: "blur the phone"} */
export function parseNoteLine(line: string): { t: number | null; text: string } {
  const m = line.trim().match(/^(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:\.(\d+))?\s*[-–:]?\s*(.*)$/);
  if (!m) return { t: null, text: line.trim() };
  const [, h, mm, ss, frac, rest] = m;
  const t = (h ? +h * 3600 : 0) + +mm * 60 + +ss + (frac ? +`0.${frac}` : 0);
  return { t, text: rest };
}

export const fmt = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
export const fmtf = (s: number) => `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`;

/** Colour carries meaning and nothing else: the client is blue, each kind of edit has its own hue. */
export const CLIENT = "#2F6BFF";
export const KIND: Record<OpKind, { label: string; colour: string; done: string }> = {
  remove: { label: "Remove", colour: "#7C3AED", done: "Removed" },
  sky: { label: "Sky", colour: "#F59E0B", done: "Sky replaced" },
  recolour: { label: "Recolour", colour: "#E5487A", done: "Recoloured" },
  blur: { label: "Blur", colour: "#0EA5A4", done: "Blurred" },
  title_behind: { label: "Title behind", colour: "#FF6A1A", done: "Title added" },
  spotlight: { label: "Make pop", colour: "#12B76A", done: "Popped" },
  ask: { label: "Question", colour: "#8A8A83", done: "Asked" },
};

/** The edit in a few words, the way an editor would write it on a card. */
export function opTitle(o: Op): string {
  const b = o.body; const c = b.target?.concept ?? "";
  switch (b.kind) {
    case "remove": return `Remove the ${c}`;
    case "sky": return `Sky to ${String(b.look ?? "golden hour")}`;
    case "recolour": return `${cap(c)} to ${String(b.colour ?? "")}`;
    case "blur": return `Blur the ${c}`;
    case "title_behind": return `“${b.text}” behind the ${c}`;
    case "spotlight": return `Make the ${c} pop`;
    default: return String(b.question ?? "Question");
  }
}
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

// ---------- live route ----------
export type Stage = { key: string; label: string; status: "pending" | "running" | "done" | "error" | "skipped"; t0: number | null; t1: number | null; msg: string };
export type LiveShot = { index: number; t0: number; t1: number; frames: number; thumb: string };
export type SamJob = { id: string; shot: number; concept: string; kind: OpKind; status: string; painted: number; objects: number | null; cached?: boolean };
export type Paint = { job: string; shot: number; frame: number; t: number; url: string; colour: string };
export type LiveState = {
  pid: string; state: "running" | "done" | "error"; version: number; started: number; finished: number | null;
  stages: Stage[]; shots: LiveShot[]; keyframes: { t: number; url: string }[]; transcript: Line[] | null;
  ops: Op[]; notes?: Note[]; jobs: SamJob[]; paint: Paint[]; render: number;
  error: null | { stage: string; message: string; detail: string };
};

export const liveApi = {
  start: async (clip: File, voice: File, name: string) => {
    const fd = new FormData(); fd.append("clip", clip); fd.append("voice", voice); fd.append("name", name);
    return j<{ id: string; live: LiveState }>("/projects/live", { method: "POST", body: fd });
  },
  state: (id: string) => j<LiveState>(`/projects/${id}/live`),
  retry: (id: string) => j<LiveState>(`/projects/${id}/retry`, { method: "POST" }),
  /** Server-sent events with a polling fallback. Returns an unsubscribe function. */
  watch: (id: string, on: (s: LiveState) => void) => {
    let closed = false; let poll: number | undefined;
    const es = new EventSource(`${API}/projects/${id}/events`);
    es.onmessage = (e) => { const s = JSON.parse(e.data) as LiveState; on(s); if (s.state !== "running") es.close(); };
    es.onerror = () => {
      es.close();
      if (closed || poll) return;
      poll = window.setInterval(async () => {
        try { const s = await liveApi.state(id); on(s); if (s.state !== "running") { clearInterval(poll); } } catch { /* keep trying */ }
      }, 1000);
    };
    return () => { closed = true; es.close(); if (poll) clearInterval(poll); };
  },
};
