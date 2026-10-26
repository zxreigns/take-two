export const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export type Note = { id: string; t: number | null; text: string; source: "typed" | "voice" };
export type OpKind = "blur" | "title_behind" | "spotlight" | "ask";
export type Op = {
  id: string; note_id: string; t0: number; t1: number;
  body: { kind: OpKind; target?: { concept: string; object_id: number | null }; text?: string; [k: string]: unknown };
  status: "proposed" | "approved" | "rejected"; rationale: string;
};
export type Line = { t0: number; t1: number; text: string };
export type Media = {
  proxy_url: string; duration: number; fps: number; width: number; height: number;
  before_url?: string; after_url?: string; mattes_url?: string; voice_url?: string | null;
};
export type Project = {
  id: string; name: string; notes: Note[]; ops: Op[]; media: null | Media;
  transcript?: Line[] | null; planner?: { model?: string; seconds?: number }; replay?: boolean;
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

export const KIND: Record<OpKind, { label: string; colour: string }> = {
  blur: { label: "Blur", colour: "#ff4db4" },
  title_behind: { label: "Title behind", colour: "#ffb000" },
  spotlight: { label: "Make pop", colour: "#c6ff3d" },
  ask: { label: "Question", colour: "#8a8a96" },
};
