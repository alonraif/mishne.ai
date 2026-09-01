"""Word timings every downstream stage is allowed to trust.

## Why this exists

A managed engine will occasionally return a word with a timestamp that is not
merely imprecise but impossible. From one 46-minute Hebrew podcast, one word out
of 6,812: start 100 ms, end 1,412,600 ms — a word claiming to span almost the
whole recording, sitting in the middle of the transcript where it was actually
spoken. It is rare and it is not harmless, because everything after stage 2
treats a word span as a fact:

- `steps/structure` gives a beat the span of the words in it, so that one word
  produced a 23-minute beat.
- `steps/speakers` totals `duration_ms` per voice, so the legend reported 49m
  51s of talking on a 46-minute recording — and on another run of the same
  material, 24.6 hours.
- The solver reads durations, so a job's target length is computed against a
  clip that does not exist.

None of those stages can tell a broken timestamp from a real one; only the
sequence can, and only here, where the whole sequence is in hand.

## The word order is never changed

This is the load-bearing decision. Given a word whose timestamp disagrees with
its neighbours, either the order is wrong or the timestamp is — and for an ASR
engine the order is the primary product. The words *are* the transcript; the
timestamps are metadata attached to them. Sorting by start time would have moved
that word from 23 minutes in to the very beginning of the file, putting its text
at the top of a delivered transcript. So the order is treated as ground truth
and the timestamps are repaired against it.

Concretely: the longest run of words whose starts already increase is taken as
the trustworthy skeleton, and anything outside it is an *outlier* placed back
into the gap between its surviving neighbours — which is where it was spoken.
The broken word above lands in a few milliseconds at 23 minutes, keeping its
place in the sentence, and the beat around it becomes an ordinary three-word
beat.

## The invariant this leaves

Starts never decrease, every span is positive, nothing falls outside the audio,
and no word runs past the start of the next one — except by the single
millisecond it needs in order to exist at all. That exception is only reachable
where several unusable timestamps have to share a gap with no room in it: a word
must have positive duration for `ck_beats_positive_duration` to hold, so a
handful of milliseconds of overlap is the honest answer there rather than a word
of zero length. It is worth stating because the point of the rule is that
durations can be summed, and a few milliseconds across a transcript is not a
number anybody reads. The no-overlap rule costs
something on genuinely overlapping speech, where two people talk at once and a
diarizing engine reports both — that word loses the overlap off its end. That is
tens of milliseconds against a 23-minute beat, stage 9 re-snaps every cut point
to real silence anyway, and crosstalk is already flagged rather than trusted
(`steps/speakers`).

Text is never touched and no word is ever dropped. A word with an unusable
timestamp is still a word somebody said, and removing it would quietly rewrite
the transcript — the same property `asr/script.py` protects, for the same
reason.
"""

from __future__ import annotations

from bisect import bisect_right

from ..logging import get_logger

log = get_logger(__name__)

#: No word runs ten seconds. Loose enough never to touch a drawl or a held
#: vowel, tight enough that the *last* word of a transcript — which has no next
#: word to bound it — cannot run away either.
MAX_WORD_MS = 10_000


def _skeleton(starts: list[int]) -> list[int]:
    """Indices of the longest non-decreasing run of starts.

    The words whose timings agree with each other, which is the largest set that
    can all be right. Everything else is an outlier — and this formulation is
    symmetric, so it catches a start that is impossibly early (the case above)
    and one that is impossibly late with the same rule, rather than letting one
    bad value poison the sequence after it.

    Patience sort with parent pointers: O(n log n), and n is a transcript.

    Where two skeletons are equally long the choice between them is arbitrary,
    and that is not a case worth engineering for: it needs as many disagreeing
    words as agreeing ones. One bad word among thousands is never ambiguous —
    dropping it costs one word, keeping it costs every word on the other side.
    """
    tails: list[int] = []        # index of the word ending a run of each length
    tail_starts: list[int] = []  # its start, kept parallel for the search
    parent: list[int] = [-1] * len(starts)

    for i, start in enumerate(starts):
        k = bisect_right(tail_starts, start)
        parent[i] = tails[k - 1] if k else -1
        if k == len(tails):
            tails.append(i)
            tail_starts.append(start)
        else:
            tails[k] = i
            tail_starts[k] = start

    out: list[int] = []
    i = tails[-1] if tails else -1
    while i >= 0:
        out.append(i)
        i = parent[i]
    out.reverse()
    return out


def sanitise(result) -> int:
    """Enforce the invariant on `result.words` in place. Returns words changed.

    Idempotent, which matters: it runs on a fresh transcription and again on
    every replay of the cached one (`steps/transcribe`), so a second pass must
    be a no-op.
    """
    words = result.words
    if not words:
        return 0

    was = [(w.start_ms, w.end_ms) for w in words]
    # The provider's own measure of the audio, when it gave one. A word outside
    # it is outside the file.
    audio_ms = int(round(result.audio_seconds * 1000)) if result.audio_seconds else 0
    limit = audio_ms or max(w.end_ms for w in words)

    # ── starts: keep the skeleton, place the outliers between their neighbours
    keep = set(_skeleton([w.start_ms for w in words]))
    run: list[int] = []
    for i in range(len(words) + 1):
        if i < len(words) and i not in keep:
            run.append(i)
            continue
        if run:
            # The gap the run has to fit in. Outside the first or last anchor
            # there is only one neighbour, so the other end is the file's.
            lo = words[run[0] - 1].start_ms if run[0] else 0
            hi = words[i].start_ms if i < len(words) else limit
            span = max(0, hi - lo)
            for n, idx in enumerate(run, start=1):
                # Evenly through the gap, in order. A zero-width gap leaves them
                # all at `lo`, and the end pass below gives each its own frame.
                words[idx].start_ms = lo + span * n // (len(run) + 1)
            run = []

    # ── the per-word rules, in sequence order ──────────────────────────────
    previous = 0
    # `limit - 1` so that the millisecond every word is entitled to below still
    # falls inside the audio.
    ceiling = max(0, limit - 1)
    for w in words:
        w.start_ms = min(max(w.start_ms, previous), ceiling)
        previous = w.start_ms

    for i, w in enumerate(words):
        end = w.end_ms
        if i + 1 < len(words):
            end = min(end, words[i + 1].start_ms)
        end = min(end, w.start_ms + MAX_WORD_MS, limit)
        # A millisecond, so a beat built from this word still has positive
        # duration and `ck_beats_positive_duration` holds.
        w.end_ms = max(end, w.start_ms + 1)

    changed = sum(
        1 for w, before in zip(words, was) if (w.start_ms, w.end_ms) != before
    )
    if changed:
        # Counts and durations only — the words themselves are customer content.
        log.info("asr.timings_repaired", words=changed, total=len(words),
                 outliers=len(words) - len(keep),
                 audio_seconds=round(result.audio_seconds, 1))
    return changed
