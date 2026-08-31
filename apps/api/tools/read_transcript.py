"""Turn a stored `.asr.json` into something a person can read.

The transcripts `compare_asr.py` writes are word-level JSON, which is the right
shape for the pipeline and the wrong shape for the one question a machine
cannot answer: **is this what was actually said?**

Engine agreement says nothing about correctness in a language only one engine
speaks. Hebrew routes to a single vendor, so the only check available is an
editor reading it — and that check needs the words joined into turns, with the
timecode of each turn so a doubtful line can be found in the footage.

    python tools/read_transcript.py work-asr/SyncDaniel_flat_a0.*.asr.json

Writes `<name>.txt` and `<name>.html` beside the input. The HTML is the one to
open for Hebrew: a terminal renders right-to-left text by its own rules and a
timecode inside an RTL paragraph comes out backwards often enough that people
start distrusting the timecodes rather than the terminal.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mishne.language import is_rtl_language  # noqa: E402

#: A gap this long, or a change of speaker, starts a new paragraph. Not a
#: sentence-boundary heuristic: punctuation is unreliable across engines and
#: absent from some, while a pause is in the timestamps we already trust.
TURN_GAP_MS = 1200


def turns(words: list[dict]) -> list[dict]:
    out: list[dict] = []
    for w in words:
        text, start, end = w["t"], w["s"], w["e"]
        speaker = w.get("spk", "")
        # A word `asr/script.py` repaired reads as ordinary Hebrew, and the
        # reader has no way to tell it was not heard that way. Underlined here
        # so the check this file exists for can include "is that the right
        # letter" as well as "is that the right word".
        marked = (f'<u class="fixed" title="script repaired">{html.escape(text)}</u>'
                  if w.get("n") else html.escape(text))
        last = out[-1] if out else None
        if (last is None or speaker != last["speaker"]
                or start - last["end"] > TURN_GAP_MS):
            out.append({"speaker": speaker, "start": start, "end": end,
                        "words": [text], "html": [marked]})
        else:
            last["words"].append(text)
            last["html"].append(marked)
            last["end"] = end
    return out


def tc(ms: int) -> str:
    s, ms = divmod(int(ms), 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms // 100}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("transcript", type=Path)
    args = ap.parse_args(argv)

    data = json.loads(args.transcript.read_text(encoding="utf-8"))
    language = data.get("language", "")
    rtl = is_rtl_language(language)
    blocks = turns(data.get("words", []))
    engine = f"{data.get('provider', '?')}/{data.get('model', '?')}"
    repaired = sum(1 for w in data.get("words", []) if w.get("n"))
    heading = (f"{args.transcript.name} · {engine} · {language} · "
               f"{len(data.get('words', []))} words · {len(blocks)} turns"
               + (f" · {repaired} script-repaired" if repaired else ""))

    txt = args.transcript.with_suffix(".txt")
    txt.write_text(
        heading + "\n\n" + "\n\n".join(
            f"[{tc(b['start'])}] {b['speaker'] or '—'}\n{' '.join(b['words'])}"
            for b in blocks
        ) + "\n",
        encoding="utf-8",
    )

    # Direction is set per element and the timecode is isolated, because a
    # Hebrew paragraph routinely contains Latin names and numbers and each of
    # those runs left-to-right inside it. Getting this wrong is not cosmetic:
    # a timecode rendered backwards is unusable.
    rows = "\n".join(
        f'<div class="turn"><div class="meta"><span class="tc" dir="ltr">'
        f'{tc(b["start"])}</span> <span class="spk">'
        f'{html.escape(b["speaker"] or "—")}</span></div>'
        f'<p dir="auto">{" ".join(b["html"])}</p></div>'
        for b in blocks
    )
    args.transcript.with_suffix(".html").write_text(
        f"""<!doctype html><meta charset="utf-8">
<title>{html.escape(args.transcript.stem)}</title>
<style>
 body {{ font: 16px/1.7 -apple-system, system-ui, sans-serif; max-width: 46rem;
        margin: 3rem auto; padding: 0 1.5rem; color: #1a1a1a; }}
 h1 {{ font-size: 0.9rem; font-weight: 600; color: #666; margin-bottom: 2rem; }}
 .turn {{ margin: 0 0 1.4rem; }}
 .meta {{ font-size: 0.72rem; color: #999; margin-bottom: 0.2rem; }}
 .tc {{ font-family: ui-monospace, monospace; unicode-bidi: isolate; }}
 .spk {{ text-transform: uppercase; letter-spacing: 0.04em; }}
 p {{ margin: 0; }}
 .fixed {{ text-decoration: underline wavy #c98a00 1px; text-underline-offset: 3px; }}
 [dir="rtl"] {{ text-align: right; }}
 @media (prefers-color-scheme: dark) {{
   body {{ background: #14161a; color: #e8e8e8; }}
   .meta {{ color: #7a7f88; }}
 }}
</style>
<h1 dir="ltr">{html.escape(heading)}</h1>
{rows}
""",
        encoding="utf-8",
    )

    print(f"{heading}\n  {txt}\n  {args.transcript.with_suffix('.html')}")
    if rtl:
        print("\n  RTL material — open the .html. A terminal renders "
              "right-to-left text by its own rules and puts timecodes "
              "backwards.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
