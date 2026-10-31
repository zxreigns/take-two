/** The model rail: which model is working right now. Each step of a revision lights its model up while it runs,
 *  in the live progress view and in the editor (playback lights the models behind the edit on screen). */
export type RailKey = "listen" | "plan" | "track" | "rebuild";
export type RailState = "wait" | "run" | "done";

const NODES: { k: RailKey; step: string; model: string; by: string; meta: boolean }[] = [
  { k: "listen", step: "Listen", model: "Whisper", by: "runs locally", meta: false },
  { k: "plan", step: "Plan", model: "Muse Spark", by: "open weights", meta: true },
  { k: "track", step: "Track", model: "SAM 3.1", by: "open weights", meta: true },
  { k: "rebuild", step: "Rebuild", model: "VOID", by: "open weights", meta: false },
];

export function ModelRail({ state, sub, rebuild, replay }: {
  state: Record<RailKey, RailState>; sub?: Partial<Record<RailKey, string>>;
  rebuild?: { model: string; by: string }; replay?: boolean;
}) {
  return (
    <div className={`rail ${replay ? "replay" : ""}`} role="list" aria-label="Models">
      {NODES.map((n, i) => {
        const st = state[n.k];
        const model = n.k === "rebuild" && rebuild ? rebuild.model : n.model;
        const by = n.k === "rebuild" && rebuild ? rebuild.by : n.by;
        return (
          <div key={n.k} className="rail-seg" role="listitem">
            {i > 0 && <span className={`beam ${st === "run" ? "run" : state[NODES[i - 1].k] === "done" && st !== "wait" ? "done" : ""}`}><i /></span>}
            <div className={`node ${st} ${n.meta ? "meta" : ""}`}>
              <span className="ni"><b>{n.step}</b></span>
              <span className="nt">
                <span className="nm">{model}{n.meta && <em className="mb">Meta</em>}</span>
                <small>{st === "run" ? (sub?.[n.k] ?? "working") : st === "done" ? (sub?.[n.k] ?? "done") : by}</small>
              </span>
              <span className="ns" aria-hidden>{st === "done" ? "✓" : st === "run" ? <i className="live" /> : ""}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
