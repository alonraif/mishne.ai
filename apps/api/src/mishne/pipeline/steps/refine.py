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
2. **Add handles, and by default add none.** This used to default to six
   frames each side, justified as "the editor has nothing to trim with". That
   reasoning does not hold here, for two reasons.

   Handles in this pipeline are not trim room — they *extend the region that
   plays*. Rule 5 below says so outright: two consecutive beats with no silence
   between them overlap once handles are added. Six frames at 25 fps is 240 ms
   of unasked-for material at each boundary, and the editor hears every bit of
   it.

   The trim room was never coming from here anyway. The AAF carries source
   MobIDs that relink to the original media (ADR-0001), so in Media Composer
   the full source sits behind every clip and the editor can pull a handle out
   as far as the rushes go. Baking frames into the sequence buys nothing that
   relinking does not already give, and costs precision.

   What protects the audio is rule 1 — snapping outward to real silence — which
   is the principled version of the same instinct and costs nothing. Handles
   remain available (`--handles`, `brief.handle_frames`) for a delivery that
   genuinely wants slack; they are no longer the default.
3. **Never cut inside a word.** Asserted against the actual word spans, and a
   hard failure if violated. A cut mid-word is not a matter of taste.
4. **Quantise to frame boundaries** in the sequence rate. Audio can be
   sample-accurate where the format allows, but the video edit is on frames.
5. **Merge near-adjacent selections.** Two clips 200 ms apart in both source
   and record order are one clip with a pointless cut in it.
6. **Enforce a minimum duration** after all of the above, so snapping and
   merging cannot produce a machine-gun micro-cut.

## Several assets in one cut

Every rule above is a property of one *source*, not of the timeline: the
silence map, the frame rate, the start timecode and the media extent all belong
to the asset a beat came from. A cut drawing on three uploads therefore runs
this stage three times over, once per asset, and the results interleave by the
order the selection asked for.

The one rule that crosses assets is merging, and it must not: two clips from
different files are never one clip, however close their frame numbers happen to
land. Merging now tests asset identity first, and adjacency in the *record*
explicitly rather than by list position — with several assets interleaved,
neighbours in one asset's subset are not neighbours in the cut.
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
    # Which upload these frames live in. Empty for a single-asset job, and
    # everything downstream treats "" as "the only asset there is".
    asset_id: str = ""
    # The beat this span was carved from — equal to `beat_id` when nothing was
    # carved. The transcript page lists beats rather than candidates, so it
    # needs this to show which beat a cut came from and what was trimmed away.
    # Defaulted, and therefore below the required fields: getting this wrong is
    # a TypeError at import, which is how it was caught the first time.
    parent_id: str = ""
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


@dataclass
class AssetContext:
    """What stage 9 needs to know about one upload.

    Bundled rather than passed as five parallel arguments because with several
    assets in play they must not be able to drift apart: the silence map, the
    rate, the start timecode and the extent all describe the same file, and
    mixing one asset's rate with another's timecode is a bug that produces a
    plausible-looking timeline pointing at the wrong frames.
    """

    rate: Rate
    start_tc_frames: int
    duration_frames: int
    asset_id: str = ""
    speech: SpeechMap | None = None
    # Position of the asset in the project. Ordering "chronologically" across
    # uploads shot on different days can mean nothing else.
    order: int = 0


def refine(selections: list[Selection], speech: SpeechMap | None,
           rate: Rate, source_start_frames: int, source_duration_frames: int,
           handle_frames: int = 0) -> list[Cut]:
    """Turn selected beats into frame-accurate cuts, for a single asset."""
    ctx = AssetContext(
        rate=rate, start_tc_frames=source_start_frames,
        duration_frames=source_duration_frames,
        asset_id=selections[0].beat.asset_id if selections else "",
        speech=speech)
    return refine_multi(selections, {ctx.asset_id: ctx},
                        handle_frames=handle_frames)


def refine_multi(selections: list[Selection],
                 contexts: dict[str, AssetContext],
                 handle_frames: int = 0) -> list[Cut]:
    """Turn selected beats into frame-accurate cuts across several assets.

    `contexts` is keyed by asset id. A selection whose beat names an asset that
    is not in the map is a wiring error, not a recoverable condition: refining
    it against some other file's silence and timecode would produce a clip that
    looks entirely reasonable and points at the wrong frames.
    """
    if not selections:
        return []

    cuts: list[Cut] = []
    for sel in selections:
        aid = sel.beat.asset_id
        ctx = contexts.get(aid)
        if ctx is None:
            raise KeyError(
                f"beat {sel.beat.id} belongs to asset {aid!r}, which was not "
                f"ingested for this job")
        cuts.append(_cut_for(sel, ctx, handle_frames))

    cuts = _merge_adjacent(cuts, contexts)
    cuts = _enforce_minimum(cuts, contexts)
    cuts.sort(key=lambda c: c.order_idx)
    for i, c in enumerate(cuts):
        c.order_idx = i
    return cuts


def _cut_for(sel: Selection, ctx: AssetContext, handle_frames: int) -> Cut:
    """Rules 1-4 for one beat, entirely within its own asset's coordinates."""
    b = sel.beat
    warnings: list[str] = []
    in_ms, out_ms = float(b.start_ms), float(b.end_ms)
    ms_per_frame = 1000.0 / ctx.rate.fps

    # 1. Snap outward to silence.
    snapped_head = snapped_tail = False
    if ctx.speech is not None:
        new_in, snapped_head = _snap_out(int(in_ms), ctx.speech, forward=False)
        new_out, snapped_tail = _snap_out(int(out_ms), ctx.speech, forward=True)
        in_ms, out_ms = min(in_ms, new_in), max(out_ms, new_out)
        if not snapped_head:
            warnings.append("no silence found before this beat")
        if not snapped_tail:
            warnings.append("no silence found after this beat")

    # 2. Handles.
    handle_ms = handle_frames * ms_per_frame
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
    lo = ctx.start_tc_frames
    hi = ctx.start_tc_frames + ctx.duration_frames
    src_in = lo + int(round(in_ms / ms_per_frame))
    src_out = lo + int(round(out_ms / ms_per_frame))
    src_in = max(lo, min(src_in, hi - 1))
    src_out = max(src_in + 1, min(src_out, hi))

    return Cut(
        beat_id=b.id, parent_id=b.parent_id or b.id, order_idx=sel.order_idx,
        src_in=src_in, src_out=src_out,
        speaker=b.speaker, text=b.text, asset_id=ctx.asset_id, score=sel.score,
        rationale=getattr(b, "rationale", ""),
        handle_head=handle_frames, handle_tail=handle_frames,
        snapped_head=snapped_head, snapped_tail=snapped_tail,
        warnings=warnings,
    )


def _merge_adjacent(cuts: list[Cut],
                    contexts: dict[str, AssetContext]) -> list[Cut]:
    """Fuse clips that are consecutive in the cut AND contiguous in the source.

    Three conditions, and all of them matter:

    * **Same asset.** Two clips from different uploads are never one clip.
      Frame numbers are per-file, so two unrelated sources will happily look
      contiguous near their starts and merge footage shot months apart.
    * **Consecutive in the record**, tested on `order_idx` rather than list
      position. With several assets interleaved, the previous clip from *this*
      asset may sit several positions back in the finished cut.
    * **Contiguous in the source**, meaning the next clip starts before, or
      shortly after, this one ends. Overlap counts: two consecutive beats with
      no silence between them always overlap once handles are added, and
      emitting them as two clips makes the overlapping quarter-second play
      twice. That stutter is the most common artefact of a handled rough cut
      and it is fixed here, by fusing them into the one continuous region they
      actually are.

    The "reuse of a region" case — the same source used twice, far apart in the
    finished piece — is excluded by the record-adjacency test above, not by the
    gap, which is why the gap can safely be negative.
    """
    ordered = sorted(cuts, key=lambda c: c.order_idx)
    out: list[Cut] = []
    last_of: dict[str, Cut] = {}

    for c in ordered:
        prev = last_of.get(c.asset_id)
        if prev is not None and prev.order_idx == c.order_idx - 1:
            gap_frames = int(MERGE_GAP_MS / 1000.0
                             * contexts[c.asset_id].rate.fps)
            if c.src_in - prev.src_out <= gap_frames and c.src_out > prev.src_out:
                prev.src_out = max(prev.src_out, c.src_out)
                prev.text = f"{prev.text} {c.text}".strip()
                prev.handle_tail = c.handle_tail
                prev.snapped_tail = c.snapped_tail
                prev.warnings.extend(c.warnings)
                # The merged clip now occupies the later slot too, so the next
                # clip from this asset is still adjacent to it.
                prev.order_idx = c.order_idx
                continue
        out.append(c)
        last_of[c.asset_id] = c

    return out


def _enforce_minimum(cuts: list[Cut],
                     contexts: dict[str, AssetContext]) -> list[Cut]:
    """Extend anything shorter than the floor, or drop it if it cannot grow.

    Both the floor and the room to grow into are the asset's own: a second is
    24 frames in one upload and 25 in the next, and the extent to clamp against
    is that file's media, not the timeline's.
    """
    out: list[Cut] = []

    for c in cuts:
        ctx = contexts[c.asset_id]
        min_frames = int(MIN_CLIP_MS / 1000.0 * ctx.rate.fps)
        if c.frames >= min_frames:
            out.append(c)
            continue
        lo = ctx.start_tc_frames
        hi = lo + ctx.duration_frames
        head = (min_frames - c.frames) // 2
        c.src_in = max(lo, c.src_in - head)
        c.src_out = min(hi, c.src_in + min_frames)
        if c.frames >= min_frames:
            c.warnings.append(
                f"extended to the {MIN_CLIP_MS} ms minimum clip length")
            out.append(c)
        else:
            c.warnings.append("dropped — too short and could not be extended")

    return out
