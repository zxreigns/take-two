export const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export type Note = { id: string; t: number | null; text: string; source: "typed" | "voice" };
export type Op = {
  id: string; note_id: string; t0: number; t1: number;
  body: { kind: "blur" | "title_behind" | "spotlight" | "ask"; [k: string]: unknown };
  status: "proposed" | "approved" | "rejected"; rationale: string;
};
export type Project = {
  id: string; name: string; notes: Note[]; ops: Op[];
  media: null | { proxy_url: string; duration: number; fps: number; width: number; height: number };
};

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(API + path, init);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

export const api = {
  health: () => j<{ ok: boolean; model_key: boolean; planner: string }>("/health"),
  create: (name: string) =>
    j<Project>("/projects", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ name }) }),
  get: (id: string) => j<Project>(`/projects/${id}`),
  upload: (id: string, file: File) => {
    const fd = new FormData(); fd.append("file", file);
    return j<Project>(`/projects/${id}/media`, { method: "POST", body: fd });
  },
  addNote: (id: string, text: string, t: number | null) =>
    j<Project>(`/projects/${id}/notes`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ text, t, source: "typed" }),
    }),
};

/** "0:12 blur the phone" -> {t: 12, text: "blur the phone"} */
export function parseNoteLine(line: string): { t: number | null; text: string } {
  const m = line.trim().match(/^(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:\.(\d+))?\s*[-–:]?\s*(.*)$/);
  if (!m) return { t: null, text: line.trim() };
  const [, h, mm, ss, frac, rest] = m;
  const t = (h ? +h * 3600 : 0) + +mm * 60 + +ss + (frac ? +`0.${frac}` : 0);
  return { t, text: rest };
}

export const fmt = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
