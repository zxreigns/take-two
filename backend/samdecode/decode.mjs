
// Fast SAM mask decoding for the renderer: raw SAM stream text in, packed binary out.
// Python's reference decoder is ~50x slower; V8 runs Meta's own TS decoder.
// Output per mask: int32 frame, int32 idLen, id bytes, float32 l,t,r,b, int32 h, int32 w, then h*w uint8.
import fs from "node:fs";
import { formats, decodeMaskToRaster } from "@meta-sam/parser";
const [inPath, outPath, f0, f1] = process.argv.slice(2);
const lo = f0 ? +f0 : -Infinity, hi = f1 ? +f1 : Infinity;
const raw = fs.readFileSync(inPath, "utf8");
const p = formats.segmentation.video().createParser();
p.push(raw.endsWith("\n") ? raw : raw + "\n", { emit: false });
const res = p.finish({ status: "completed" }).result;
if (process.env.SAM_DEBUG) { const r = res.records.find(x => x.kind === "mask"); console.error(JSON.stringify(Object.keys(r||{})), JSON.stringify(r && r.frame), JSON.stringify(r && r.bounds)); }
const out = fs.openSync(outPath, "w"); let n = 0;
for (const r of res.records) {
  if (r.kind !== "mask" || !r.frame) continue;
  const fi = r.frame.frameIndex ?? r.frame.frame_index;
  if (fi < lo || fi > hi) continue;
  const raster = decodeMaskToRaster(r.mask);
  const id = Buffer.from(String(r.objectId ?? r.object_id));
  const head = Buffer.alloc(4 + 4 + id.length + 16 + 8);
  let o = head.writeInt32LE(fi, 0); o = head.writeInt32LE(id.length, o); id.copy(head, o); o += id.length;
  for (const v of [r.bounds.left, r.bounds.top, r.bounds.right, r.bounds.bottom]) o = head.writeFloatLE(v, o);
  o = head.writeInt32LE(r.mask.height, o); head.writeInt32LE(r.mask.width, o);
  fs.writeSync(out, head); fs.writeSync(out, Buffer.from(raster.buffer, raster.byteOffset, raster.byteLength)); n++;
}
fs.closeSync(out);
console.log(JSON.stringify({ masks: n, diagnostics: res.diagnostics.length }));
