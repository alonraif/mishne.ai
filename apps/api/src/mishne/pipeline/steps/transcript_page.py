"""The transcript deliverable — a single self-contained HTML file.

This is the explainability layer and it is a real differentiator. Professional
editors do not trust automated selection by default; being able to see *why* a
soundbite was chosen, and what was considered and rejected, converts scepticism
into use faster than any accuracy improvement will.

Self-contained on purpose: no server, no build step, no fonts to fetch. The
editor opens it from the same folder as the AAF, offline, on whatever machine
the media is on.

## Right-to-left

Hebrew is a first-class target, and RTL is not a CSS afterthought:

- Beat text uses `dir="auto"`, so the browser decides per string. A transcript
  routinely mixes Hebrew with Latin product names and numerals, and per-string
  detection handles that correctly where a blanket `dir` on the page does not.
- **Timecode is forced `dir="ltr"` with `unicode-bidi: isolate`.** This is the
  one that actually breaks: `10:02:14:00` dropped into an RTL paragraph without
  isolation reorders around the colons and becomes unreadable. Timecode is the
  thing an editor is scanning for, so it has to be right.
- The transcript body flips to RTL when the source language is RTL, so the
  reading order and the gutter land where a Hebrew reader expects them.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from ...language import is_rtl_language
from ...timecode import Rate, frames_to_tc
from .refine import Cut
from .structure import Beat

CSS = """
.trimmed{margin-top:6px;padding:6px 8px;border-radius:4px;background:rgba(120,180,120,.10);font-size:12px;line-height:1.5}
.reel{display:block;font-size:10px;opacity:.55;unicode-bidi:isolate;direction:ltr;margin-top:2px}
:root{--bg:#16161a;--fg:#f2f2f4;--muted:#9a9aa4;--line:#2b2b33;
--card:#1d1d22;--used:#4ade80;--used-bg:#132a1d;--accent:#8b8bf5;
--flag:#e0a458;--tc:#a5a5e8}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1000px;margin:0 auto;padding:32px 24px 80px}
h1{font-size:24px;margin:0 0 4px;letter-spacing:-.01em}
.sub{color:var(--muted);font-size:14px;margin:0 0 24px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
gap:12px;margin-bottom:24px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:14px}
.stat .k{color:var(--muted);font-size:12px}
.stat .v{font-size:20px;font-weight:600;margin-top:4px}
.stat .v.used{color:var(--used)}
.brief{background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:16px;margin-bottom:24px}
.brief h2{font-size:13px;color:var(--muted);margin:0 0 10px;font-weight:500;
text-transform:uppercase;letter-spacing:.04em}
.notes{font-style:italic;color:var(--muted);margin:0 0 12px}
.kv{display:flex;flex-wrap:wrap;gap:8px 20px;font-size:13px}
.kv span b{color:var(--muted);font-weight:400;margin-inline-end:6px}
.clar{margin-top:12px;padding-top:12px;border-top:1px solid var(--line);
font-size:12px;color:var(--muted)}
.clar li{margin:4px 0}
.beat{display:flex;gap:14px;padding:10px 12px;border-radius:8px;
border:1px solid transparent;margin-bottom:3px;align-items:flex-start}
.beat.used{border-color:#1f4030;background:var(--used-bg)}
.gut{flex:0 0 84px;padding-top:2px}
.mark{flex:0 0 16px;margin-top:5px;height:16px;border-radius:50%;
border:1px solid var(--line)}
.beat.used .mark{background:var(--used);border-color:var(--used)}
.body{min-width:0;flex:1}
.meta{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:3px;
font-size:12px;color:var(--muted)}
.txt{margin:0;color:#8e8e98}
.beat.used .txt{color:var(--fg)}
.why{margin-top:8px;padding-inline-start:10px;border-inline-start:2px solid var(--line);
font-size:12.5px;color:var(--muted)}
.flag{border:1px solid var(--flag);color:var(--flag);border-radius:4px;
padding:0 5px;font-size:10.5px}
.spk{color:var(--accent);font-weight:500}
/* Timecode must never reorder inside RTL text, and must not reflow as digits
   change. Both matter more than they sound. */
.tc{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
font-variant-numeric:tabular-nums;color:var(--tc);font-size:12px;
direction:ltr;unicode-bidi:isolate;display:inline-block}
.filters{display:flex;gap:6px;margin-bottom:14px;flex-wrap:wrap}
.filters button{background:none;border:1px solid var(--line);color:var(--muted);
border-radius:6px;padding:5px 11px;font:inherit;font-size:12.5px;cursor:pointer}
.filters button[aria-pressed=true]{background:#2a2a33;color:var(--fg)}
.hidden{display:none}
footer{margin-top:40px;color:var(--muted);font-size:12px;
border-top:1px solid var(--line);padding-top:16px}
"""

JS = """
const btns=[...document.querySelectorAll('.filters button')];
btns.forEach(b=>b.onclick=()=>{
  btns.forEach(x=>x.setAttribute('aria-pressed',x===b));
  const m=b.dataset.mode;
  document.querySelectorAll('.beat').forEach(el=>{
    const used=el.classList.contains('used');
    el.classList.toggle('hidden', m==='used'&&!used || m==='unused'&&used);
  });
});
"""


def render(beats: list[Beat], cuts: list[Cut], brief, rate: Rate,
           source_start_frames: int, source_duration_frames: int,
           speakers: dict[str, str], media_name: str, language: str,
           out_path: Path, contexts: dict | None = None,
           asset_names: dict[str, str] | None = None) -> Path:
    """Render the page an editor is actually going to read.

    `contexts` maps asset id to a `refine.AssetContext`. When a job draws on
    several uploads every timecode on this page has to be read against its own
    reel — a beat at 00:04:12 of the second camera is not at 00:04:12 of the
    first — so the source column names the file as well. Omitted for a
    single-asset job, where the flat `rate`/`source_*` arguments say it all.
    """
    rtl = is_rtl_language(language)
    body_dir = "rtl" if rtl else "ltr"
    # Keyed on the PARENT beat. Stage 6 offers several candidate spans per
    # beat and only one can be selected, so listing candidates would show the
    # editor the same material six times over. The page lists what was
    # considered — the beats — and says which span of each one survived.
    used_ids = {c.parent_id or c.beat_id for c in cuts}
    by_beat = {(c.parent_id or c.beat_id): c for c in cuts}
    contexts = contexts or {}
    asset_names = asset_names or {}
    multi = len(contexts) > 1

    def ctx_for(asset_id: str):
        """That asset's (rate, start timecode), or the job's if there is one."""
        c = contexts.get(asset_id)
        if c is None:
            return rate, source_start_frames
        return c.rate, c.start_tc_frames

    cut_s = sum(c.frames / ctx_for(c.asset_id)[0].fps for c in cuts)
    src_s = (sum(c.duration_frames / c.rate.fps for c in contexts.values())
             if contexts else source_duration_frames / rate.fps)

    def esc(t: str) -> str:
        return html.escape(t or "")

    def dur(sec: float) -> str:
        m, s = divmod(int(round(sec)), 60)
        h, m = divmod(m, 60)
        return f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s"

    rows = []
    for b in beats:
        used = b.id in used_ids
        cut = by_beat.get(b.id)
        b_rate, b_start = ctx_for(b.asset_id)
        frame = b_start + int(round(b.start_ms / (1000.0 / b_rate.fps)))
        reel = (f'<span class="reel" dir="auto">'
                f'{esc(asset_names.get(b.asset_id, b.asset_id))}</span>'
                if multi else "")
        flags = "".join(
            f'<span class="flag">{esc(f.replace("_", " "))}</span>'
            for f in b.flags)
        trimmed = ""
        # Only when a span was actually carved. Comparing text instead would
        # also fire on cuts that stage 9 merged, where the beat was used whole
        # and the row would claim a trim that never happened.
        if used and cut and cut.parent_id and cut.parent_id != cut.beat_id:
            # What actually made the cut, when it is not the whole beat. This
            # is the line an editor checks when they want to know why the AAF
            # is shorter than the transcript row above it.
            trimmed = (f'<div class="trimmed" dir="auto">'
                       f'<b>{esc("used:")}</b> {esc(cut.text)}</div>')
        why = ""
        if used and cut and cut.rationale:
            why = f'<div class="why" dir="auto">{esc(cut.rationale)}</div>'
        elif not used:
            # dir="auto" matters here too: this string is English inside an
            # RTL container, and without it the full stop jumps to the front.
            why = ('<div class="why" dir="auto">Not selected — scored below '
                   'the threshold for the target duration.</div>')
        rows.append(
            f'<div class="beat{" used" if used else ""}">'
            f'<div class="gut"><span class="tc">'
            f'{frames_to_tc(frame, b_rate)}</span>{reel}</div>'
            f'<div class="mark"></div>'
            f'<div class="body">'
            f'<div class="meta"><span class="spk" dir="auto">'
            f'{esc(speakers.get(b.speaker, b.speaker))}</span>'
            f'<span class="tc">{b.duration_ms / 1000:.1f}s</span>{flags}</div>'
            f'<p class="txt" dir="auto">{esc(b.text)}</p>{trimmed}{why}</div></div>'
        )

    clar = "".join(f"<li>{esc(c)}</li>" for c in brief.clarifications)
    kv = "".join(
        f"<span><b>{k}</b>{esc(str(v))}</span>" for k, v in (
            ("Target", dur(brief.target_duration_s)),
            ("Structure", brief.narrative_shape.replace("_", " ")),
            ("Pacing", brief.pacing),
            ("Handles", f"{brief.handle_frames} frames"),
            ("Language", language),
        ) + ((("Sources", f"{len(contexts)} uploads"),) if multi else ()))

    out_path.write_text(f"""<!doctype html>
<html lang="{esc(language)}" dir="ltr">
<head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(media_name)} — transcript</title>
<style>{CSS}</style></head>
<body><div class="wrap">
<h1>Transcript</h1>
<p class="sub">{esc(media_name)} · every beat considered, what made the cut, and why.</p>

<div class="grid">
  <div class="stat"><div class="k">Source</div><div class="v">{dur(src_s)}</div></div>
  <div class="stat"><div class="k">Cut</div><div class="v used">{dur(cut_s)}</div></div>
  <div class="stat"><div class="k">Clips</div><div class="v">{len(cuts)}</div></div>
  <div class="stat"><div class="k">Reduction</div>
    <div class="v">{100 - (cut_s / src_s * 100 if src_s else 0):.1f}%</div></div>
</div>

<div class="brief"><h2>Brief</h2>
<p class="notes" dir="auto">&ldquo;{esc(brief.notes_raw)}&rdquo;</p>
<div class="kv">{kv}</div>
{f'<div class="clar"><b>Assumptions made</b><ul>{clar}</ul></div>' if clar else ''}
</div>

<div class="filters">
  <button data-mode="all" aria-pressed="true">All {len(beats)}</button>
  <button data-mode="used" aria-pressed="false">Used {len(used_ids)}</button>
  <button data-mode="unused" aria-pressed="false">Not used {len(beats) - len(used_ids)}</button>
</div>

<div dir="{body_dir}">{''.join(rows)}</div>

<footer>Generated by mishne.ai · rough cut, not a fine cut · rate {rate}</footer>
</div><script>{JS}</script></body></html>
""", encoding="utf-8")
    return out_path
