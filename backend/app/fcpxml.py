"""FCP7 XML (xmeml v4) for an exported revision: V1 = the client cut, V2 = Take Two's rendered edits,
cut to each approved span, so the editor opens the revision in their NLE with every change on its own layer.
Each approved edit also lands as a sequence marker carrying the client's own words."""
from __future__ import annotations
from xml.sax.saxutils import escape


def _rate(fps: float) -> str:
    ntsc = abs(fps - round(fps)) > 0.01
    return f"<rate><timebase>{round(fps)}</timebase><ntsc>{'TRUE' if ntsc else 'FALSE'}</ntsc></rate>"


def _file(fid: str, name: str, frames: int, fps: float, w: int, h: int, audio: bool) -> str:
    a = "<audio><channelcount>2</channelcount></audio>" if audio else ""
    return (f'<file id="{fid}"><name>{escape(name)}</name><pathurl>{escape(name)}</pathurl>{_rate(fps)}'
            f"<duration>{frames}</duration><media><video><samplecharacteristics>{_rate(fps)}"
            f"<width>{w}</width><height>{h}</height></samplecharacteristics></video>{a}</media></file>")


def build(name: str, before: str, after: str, duration: float, fps: float, width: int, height: int,
          ops: list[dict], notes: dict[str, str], has_audio: bool = False) -> str:
    f = lambda t: int(round(t * fps))
    n = f(duration)
    rate = _rate(fps)
    v1 = (f'<clipitem id="v1-client-cut"><name>Client cut</name><duration>{n}</duration>{rate}'
          f"<start>0</start><end>{n}</end><in>0</in><out>{n}</out>"
          f"{_file('file-before', before, n, fps, width, height, has_audio)}</clipitem>")
    v2, markers, first = [], [], True
    for o in sorted(ops, key=lambda o: o["t0"]):
        s, e = max(0, f(o["t0"])), min(n, f(o["t1"]))
        if e <= s:
            continue
        body = o.get("body", {})
        what = body.get("kind", "edit") + (f" · {body['target']['concept']}" if body.get("target") else "")
        if body.get("kind") == "title_behind":
            what = f"title “{body.get('text', '')}” behind {body.get('target', {}).get('concept', '')}"
        fdef = _file("file-after", after, n, fps, width, height, False) if first else '<file id="file-after"/>'
        first = False
        v2.append(f'<clipitem id="v2-{escape(o["id"])}"><name>Take Two · {escape(what)}</name><duration>{n}</duration>{rate}'
                  f"<start>{s}</start><end>{e}</end><in>{s}</in><out>{e}</out>{fdef}</clipitem>")
        quote = notes.get(o.get("note_id") or "", "")
        markers.append(f"<marker><name>{escape(what)}</name><comment>{escape(quote)}</comment><in>{s}</in><out>{e}</out></marker>")
    audio = ""
    if has_audio:
        audio = (f'<audio><track><clipitem id="a1-client-cut"><name>Client cut</name><duration>{n}</duration>{rate}'
                 f'<start>0</start><end>{n}</end><in>0</in><out>{n}</out><file id="file-before"/>'
                 f"<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack></clipitem></track></audio>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4">'
            f'<sequence id="take-two"><name>{escape(name)}</name><duration>{n}</duration>{rate}'
            f"<timecode>{rate}<string>00:00:00:00</string><frame>0</frame><displayformat>NDF</displayformat></timecode>"
            f"<media><video><format><samplecharacteristics>{rate}<width>{width}</width><height>{height}</height>"
            f"</samplecharacteristics></format><track>{v1}</track><track>{''.join(v2)}</track></video>{audio}</media>"
            f"{''.join(markers)}</sequence></xmeml>\n")
