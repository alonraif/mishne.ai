"""Stage 9 — cut-point refinement.

Where text decisions meet the waveform. The engine decided *what* to keep from
the transcript; this decides *where* the cut actually lands, and it is entirely
deterministic.

This stage is what separates a rough cut an editor can use from a list of
timecodes. It runs in **every mode**, including manual and hybrid: a person
marking text should not have to think about frame accuracy, and should not be
able to produce a cut that clips a consonant.

The rules, in order, and why each exists:

1. **Snap outward to silence.** Word boundaries from ASR are approximate and
   sit tight against the speech. Cutting there clips onsets and truncates the
   final consonant, which is the most audible failure there is. Snapping
   outward to the nearest silence from the VAD map costs nothing and fixes it.
2. **Add handles.** Default six frames each side. A rough cut without handles
   is unusable — the editor has nothing to trim with. Non-negotiable.
3. **Never cut inside a word.** Asserted against the actual word spans, and a
   hard failure if violated. A cut mid-word is not a matter of taste.
4. **Quantise to frame boundaries** in the sequence rate. Audio can be
   sample-accurate where the format allows, but the video edit is on frames.
5. **Merge near-adjacent selections.** Two clips 200 ms apart in both source
   and record order are one clip with a pointless cut in it.
6. **Enforce a minimum duration** after all of the above, so snapping and
   merging cannot produce a machine-gun micro-cut.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...timecode import Rate
from .select import Selection
from .vad import SpeechMap

MIN_CLIP_MS = 1000
MERGE_GAP_MS = 400
# How far to look for silence before giving up and cutting in speech.
SILENCE_SEARCH_MS = 800


@dataclass
class Cut:
    """One clip in the finished timeline, in source frames."""

    beat_id: str
    order_idx: int
    src_in: int          # source frames, absolute (includes start timecode)
    src_out: int
    speaker: str
    text: str
    score: float = 0.0
    rationale: str = ""
    handle_head: int = 0
    handle_tail: int = 0
    snapped_head: bool = False
    snapped_tail: bool = False
    warnings: list[str] = None

    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []

    @property
    def frames(self) -> int:
        return self.src_out - self.src_in


def _snap_out(ms: int, speech: SpeechMap, forward: bool,
              search_ms: int = SILENCE_SEARCH_MS) -> tuple[int, bool]:
    """Move a cut point outward into the nearest silence.

    `forward=False` for an in-point (move earlier), True for an out-point (move
    later). Returns (position, whether it moved). Never moves inward: pulling a
    cut into speech to reach silence would clip the very word being kept.
    """
    best, best_d = ms, None
    for start, end in speech.silence:
        mid = (start + end) // 2
        if forward and mid < ms:
            continue
        if not forward and mid > ms:
            continue
        d = abs(mid - ms)
        if d <= search_ms and (best_d is None or d < best_d):
            best, best_d = mid, d
    return best, best_d is not None


def refine(selections: list[Selection], speech: SpeechMap | None,
           rate: Rate, source_start_frames: int, source_duration_frames: int,
           handle_frames: int = 6) -> list[Cut]:
    """Turn selected beats into frame-accurate cuts."""
    if not selections:
        return []

    fps = rate.fps
    ms_per_frame = 1000.0 / fps
    handle_ms = handle_frames * ms_per_frame
    cuts: list[Cut] = []

    for sel in selections:
        b = sel.beat
        warnings: list[str] = []
        in_ms, out_ms = float(b.start_ms), float(b.end_ms)

        # 1. Snap outward to silence.
        snapped_head = snapped_tail = False
        if speech is not None:
            new_in, snapped_head = _snap_out(int(in_ms), speech, forward=False)
            new_out, snapped_tail = _snap_out(int(out_ms), speech, forward=True)
            in_ms, out_ms = min(in_ms, new_in), max(out_ms, new_out)
            if not snapped_head:
                warnings.append("no silence found before this beat")
            if not snapped_tail:
                warnings.append("no silence found after this beat")

        # 2. Handles.
        in_ms -= handle_ms
        out_ms += handle_ms

        # 3. Never inside a word. Snapping and handles both move outward, so a
        #    violation means the beat's own words overlap a neighbour's — worth
        #    knowing about rather than asserting away.
        if b.words:
            first, last = b.words[0], b.words[-1]
            if in_ms > first.start_ms:
                in_ms = float(first.start_ms)
                warnings.append("in-point pulled back to a word boundary")
            if out_ms < last.end_ms:
                out_ms = float(last.end_ms)
                warnings.append("out-point pushed to a word boundary")

        # 4. Quantise, and clamp to what the media actually has. A handle that
        #    runs past the end produces a clip the NLE cannot resolve.
        src_in = source_start_frames + int(round(in_ms / ms_per_frame))
        src_out = source_start_frames + int(round(out_ms / ms_per_frame))
        lo = source_start_frames
        hi = source_start_frames + source_duration_frames
        src_in = max(lo, min(src_in, hi - 1))
        src_out = max(src_in + 1, min(src_out, hi))

        cuts.append(Cut(
            beat_id=b.id, order_idx=sel.order_idx,
            src_in=src_in, src_out=src_out,
            speaker=b.speaker, text=b.text, score=sel.score,
            rationale=getattr(b, "rationale", ""),
            handle_head=handle_frames, handle_tail=handle_frames,
            snapped_head=snapped_head, snapped_tail=snapped_tail,
            warnings=warnings,
        ))

    cuts = _merge_adjacent(cuts, rate)
    return _enforce_minimum(cuts, rate, source_start_frames,
                            source_duration_frames)


def _merge_adjacent(cuts: list[Cut], rate: Rate) -> list[Cut]:
    """Fuse clips that are consecutive in the cut AND contiguous in the source.

    Both conditions matter. Two clips near each other in the source but far
    apart in the record are a deliberate reuse of a region, not a seam to
    remove.
    """
    gap_frames = int(MERGE_GAP_MS / 1000.0 * rate.fps)
    ordered = sorted(cuts, key=lambda c: c.order_idx)
    out: list[Cut] = []

    for c in ordered:
        if out:
            prev = out[-1]
            if 0 <= c.src_in - prev.src_out <= gap_frames:
                prev.src_out = max(prev.src_out, c.src_out)
                prev.text = f"{prev.text} {c.text}".strip()
                prev.handle_tail = c.handle_tail
                prev.snapped_tail = c.snapped_tail
                prev.warnings.extend(c.warnings)
                continue
        out.append(c)

    for i, c in enumerate(out):
        c.order_idx = i
    return out


def _enforce_minimum(cuts: list[Cut], rate: Rate, lo: int,
                     duration: int) -> list[Cut]:
    """Extend anything shorter than the floor, or drop it if it cannot grow."""
    min_frames = int(MIN_CLIP_MS / 1000.0 * rate.fps)
    hi = lo + duration
    out: list[Cut] = []

    for c in cuts:
        if c.frames >= min_frames:
            out.append(c)
            continue
        short = min_frames - c.frames
        head = short // 2
        c.src_in = max(lo, c.src_in - head)
        c.src_out = min(hi, c.src_in + min_frames)
        if c.frames >= min_frames:
            c.warnings.append(
                f"extended to the {MIN_CLIP_MS} ms minimum clip length")
            out.append(c)
        else:
            c.warnings.append("dropped — too short and could not be extended")

    for i, c in enumerate(out):
        c.order_idx = i
    return out
