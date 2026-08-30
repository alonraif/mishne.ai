"""Stage 6 — candidate spans.

## Why this stage exists

Until now the beat was the atomic unit of selection: the scorer scored beats,
the solver picked whole beats, and nothing downstream was ever offered a
boundary that stage 4 had not produced. On material with real pauses that is
fine — a beat is an answer, and answers are what you select.

On material without them it is the whole problem. A 26-minute English interview
asked for a two-minute cut came back as 55 beats with a median of 30 seconds,
64% of the running time sitting in beats over 30 seconds, and a "two minute cut"
that was four blocks. Sweeping the pause threshold from 1200 ms to 600 ms moved
the median to 21 s and changed nothing that mattered: the speaker does not
pause, so there are no seams to find. One selected block ran 47.8 seconds and
contained two unrelated things — the end of an answer about honouring the
victims, and the interviewer starting a new question.

An editor's response to that block is obvious and unavailable to us: cut inside
it. This stage makes that move available, by proposing *spans* — narrower
versions of a beat that may start or end mid-sentence — as additional
candidates. Everything downstream treats them as ordinary beats.

## Every boundary is gated on silence

The gate is the point of the stage, not a detail of it. A span whose endpoints
have no silence behind them produces a clipped consonant, which is the most
audible failure a cut can have, and no rationale from a language model makes it
audible. So legal cut points are computed first, from the VAD, and **every**
proposal — heuristic or model — is snapped to them and rejected if it cannot be.

That budget is small and worth knowing. In the reference interview there are 147
silences over 300 ms in 26 minutes, and inside that 47.8-second block exactly 8,
of which 3 exceed 500 ms. The model is not choosing from a continuum; it is
choosing from a handful of places the recording allows.

## What the model adds

Given the legal points, enumerating spans between them is mechanical and this
module does it as the control. What a model adds is judgement about *which*
span is a coherent thought — that trimming "And I suppose I wanted to use it as
a way to, you know, process the trauma of those events" to start at "process
the trauma" keeps a sentence, and that cutting two words later does not. That is
a text problem, and it is the one thing here worth paying a model for.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace

from .structure import Beat
from .vad import SpeechMap

# A boundary needs at least this much silence behind it to be cuttable. Below
# roughly this the handles in stage 9 have nowhere to go and the cut clips.
MIN_CUT_SILENCE_MS = 300
# Only beats longer than this are worth carving up; shorter ones are already an
# editorial unit and splitting them produces fragments.
# Below this a beat is left whole. Was 12s, which left every 8-12s answer as a
# single take-it-or-leave-it candidate — and at a 120s target those are 7-10%
# of the finished piece each. Eight seconds is still comfortably above
# MIN_SPAN_MS * 2, so a carve can produce two viable thoughts.
CARVE_ABOVE_MS = 8_000
# A proposed span shorter than this is not a thought.
MIN_SPAN_MS = 2_000
# Cap per parent, so the solver is not handed a combinatorial explosion. Eight
# rather than six: the proposer is now asked for four to eight on a long block,
# and a cap below what the prompt requests silently discards the model's later
# — often tighter — offers.
MAX_SPANS_PER_BEAT = 8


def cut_points(beat: Beat, speech: SpeechMap | None) -> list[int]:
    """Word indices inside `beat` where a cut is physically possible.

    Returned as indices into `beat.words`: index i means "cut before word i", so
    0 and len(words) are the beat's own edges and are always legal. Everything
    between has to be paid for with real silence.
    """
    n = len(beat.words)
    legal = {0, n}
    if speech is None or n < 2:
        return sorted(legal)

    silences = speech.silence
    for i in range(1, n):
        a, b = beat.words[i - 1], beat.words[i]
        lo, hi = a.end_ms - 200, b.start_ms + 200
        for s0, s1 in silences:
            if s0 >= hi:
                break
            if min(hi, s1) > max(lo, s0) and (s1 - s0) >= MIN_CUT_SILENCE_MS:
                legal.add(i)
                break
    return sorted(legal)


def span(parent: Beat, lo: int, hi: int, kind: str,
         rationale: str = "") -> Beat | None:
    """Carve `parent.words[lo:hi]` into a candidate, or None if not viable."""
    words = parent.words[lo:hi]
    if not words:
        return None
    start, end = words[0].start_ms, words[-1].end_ms
    if end - start < MIN_SPAN_MS:
        return None
    if lo == 0 and hi == len(parent.words):
        return parent

    text = re.sub(r"\s+([,.!?;:])", r"\1",
                  " ".join(w.text for w in words).strip())
    return replace(
        parent,
        id=f"{parent.id}_{lo:03d}_{hi:03d}",
        parent_id=parent.parent_id,
        kind=kind,
        start_ms=start,
        end_ms=end,
        text=text,
        words=words,
        rationale=rationale,
    )


def enumerate_spans(beat: Beat, speech: SpeechMap | None) -> list[Beat]:
    """The control proposer: every span between legal cut points, capped.

    Deterministic and free. It proves the machinery — that a sub-span survives
    scoring, selection, refinement and assembly — without claiming any judgement
    about which span is worth keeping. That judgement is what `ClaudeProposer`
    is for, and this is what runs when there is no key.
    """
    out = [beat]
    if beat.duration_ms < CARVE_ABOVE_MS:
        return out

    points = cut_points(beat, speech)
    if len(points) <= 2:
        return out

    n = len(beat.words)
    interior = points[1:-1]

    # Three families, kept apart on purpose. Whichever the cap keeps, it must
    # keep some of each.
    heads, tails, blocks = [], [], []
    for p in interior:
        s = span(beat, 0, p, "trim", "trimmed at a pause in the recording")
        if s is not None and s is not beat:
            heads.append(s)
        s = span(beat, p, n, "trim", "run-up dropped, starts at a pause")
        if s is not None and s is not beat:
            tails.append(s)
    for a, b in zip(points, points[1:]):
        s = span(beat, a, b, "split", "one block of a long unbroken stretch")
        if s is not None and s is not beat:
            blocks.append(s)

    # Interleave rather than sort by length.
    #
    # Sorting longest-first looks sensible and is the wrong shape: on a beat
    # holding a question and its answer, the six longest spans all began at word
    # zero and differed only in where they stopped, so the cap threw away the
    # one boundary that mattered — the one between the question and the answer.
    # Taking a head, a tail and a block in turn guarantees the offers differ at
    # both ends.
    #
    # Within a family, the most central boundary first: a cut near the middle
    # divides the material, one near an edge only shaves it.
    mid = n / 2
    for group in (heads, tails, blocks):
        group.sort(key=lambda s: abs(len(s.words) - mid))

    seen, unique = {(beat.start_ms, beat.end_ms)}, []
    for row in zip_longest_(heads, tails, blocks):
        for s in row:
            key = (s.start_ms, s.end_ms) if s else None
            if s is None or key in seen:
                continue
            seen.add(key)
            unique.append(s)
    return [beat, *unique[:MAX_SPANS_PER_BEAT]]


def zip_longest_(*groups):
    """itertools.zip_longest, without importing it for one call."""
    for i in range(max((len(g) for g in groups), default=0)):
        yield tuple(g[i] if i < len(g) else None for g in groups)


class ModelProposer:
    """Asks for spans that are coherent thoughts, within the legal points.

    Vendor-agnostic: the router decides which model runs this, and this class
    never learns which one it was. What it does own is the gate — the answer is
    checked against CUT_POINTS here, and the number of proposals refused is
    handed back to the router as a measurement of that model's obedience.
    """

    name = "model"

    SYSTEM = (
        "You choose where to cut inside a long stretch of interview or "
        "presenter speech, so that an editor gets usable pieces instead of one "
        "unbroken block.\n\n"
        "You are given a beat's words, each numbered, and CUT_POINTS: the only "
        "indices where a cut is physically possible, because the recording has "
        "silence there. **You may only use those indices.** A cut anywhere else "
        "clips the speech and cannot be used, however good the wording would "
        "be.\n\n"
        "Propose spans as [start, end) index pairs. A span must be a coherent "
        "thought that stands on its own: a complete statement, or a question, "
        "or a self-contained observation. Starting mid-sentence is allowed and "
        "often right — dropping a run-up like \"And I suppose I wanted to say "
        "that\" leaves a stronger line. Ending mid-sentence is allowed where "
        "what follows is a change of subject.\n\n"
        "Do not propose a span that depends on words you cut away: a pronoun "
        "with no referent, an answer with no question, a \"but\" with nothing "
        "before it. If the best available cut point still leaves a fragment, "
        "propose nothing for that region and say so.\n\n"
        "**Offer the tight version.** A 45-second answer almost always "
        "contains a 10-second line that carries it, and that line is what an "
        "editor wants. Propose the short, punchy span AND the fuller one where "
        "both stand up — the solver downstream chooses between them against a "
        "target length, so offering only the long version decides for it and "
        "hands the viewer a slab.\n\n"
        "Four to eight spans per beat is normal on a long block. Do not "
        "self-censor to two: a span that is merely good is still worth "
        "offering, because the alternative it competes against is the whole "
        "unbroken beat. The one thing that is never worth offering is a "
        "fragment that does not stand on its own.\n\n"
        "As a rule of thumb, a span longer than a fifth of the finished piece "
        "is doing too much work — for a 120-second cut, prefer spans under "
        "about 25 seconds and look hard for ones under 15.\n\n"
        "The transcript may be in any language, Hebrew included. Judge it in "
        "its own language and register, and write `rationale` in that same "
        "language so the editor can read it."
    )

    def __init__(self, router):
        self.router = router

    def propose(self, beat: Beat, speech: SpeechMap | None,
                brief) -> list[Beat]:
        points = cut_points(beat, speech)
        if beat.duration_ms < CARVE_ABOVE_MS or len(points) <= 2:
            return [beat]

        numbered = [{"i": i, "w": w.text} for i, w in enumerate(beat.words)]
        completion = self.router.complete(
            # 4096, not 2048: the prompt now asks for four to eight spans per
            # beat instead of two or three, and a budget sized for the old
            # request truncates the new one. A truncated answer here does not
            # raise into the operator's view — `build` catches everything and
            # returns the unbroken beat — so it would read as a model choosing
            # not to carve, which is precisely the bug this prompt is fixing.
            "spans", system=self.SYSTEM, max_tokens=4096,
            user=(f"Target for the whole piece: {brief.target_duration_s}s. "
                  f"Tone: {', '.join(getattr(brief, 'tone', [])) or 'unspecified'}.\n\n"
                  f"CUT_POINTS: {json.dumps(points)}\n\n"
                  f"WORDS: {json.dumps(numbered, ensure_ascii=False)}\n\n"
                  'Return ONLY a JSON array: '
                  '[{"start":int,"end":int,"rationale":"one line"}]\n'
                  "Keep each rationale under 12 words."))

        out, refused, offered = [beat], 0, 0
        legal = set(points)
        for row in completion.json():
            offered += 1
            try:
                lo, hi = int(row.get("start", -1)), int(row.get("end", -1))
            except (TypeError, ValueError):
                refused += 1
                continue
            # The gate. A model that ignores CUT_POINTS gets its span dropped,
            # not snapped to something nearby — snapping would silently move the
            # cut off the thought the rationale describes.
            if lo not in legal or hi not in legal or hi <= lo:
                refused += 1
                continue
            s = span(beat, lo, hi, "trim", row.get("rationale", ""))
            if s is not None and s is not beat:
                out.append(s)

        # Obedience, measured. This is the one quality signal available without
        # a corpus: how often this model asked for a cut the recording cannot
        # make. Recorded per call, reported per job, and not yet allowed to
        # change routing — see llm/router.py.
        self.router.note_violations("spans", refused, offered)
        return out[:MAX_SPANS_PER_BEAT + 1]


def get_proposer(name: str = "auto", router=None):
    """`auto` uses a model when any vendor key is present, else enumeration."""
    if name in ("enumerate", "heuristic", "none"):
        return None
    if name == "auto":
        if router is None or not router.available_for("spans"):
            return None
        return ModelProposer(router)
    if name in ("model", "claude", "llm"):
        if router is None or not router.available_for("spans"):
            raise RuntimeError(
                "no API key for span proposal — set ANTHROPIC_API_KEY, "
                "OPENAI_API_KEY, GEMINI_API_KEY or XAI_API_KEY, or use "
                "--spans enumerate")
        return ModelProposer(router)
    raise ValueError(f"unknown proposer: {name}")


def build(beats: list[Beat], speech_for, brief, proposer=None) -> list[Beat]:
    """Beats in, candidates out — originals always included.

    `speech_for` maps an asset id to that asset's `SpeechMap`. Silence is a
    property of one recording, and reading one asset's silence against another's
    timings is the class of bug that produces a plausible cut in the wrong place.
    """
    out: list[Beat] = []
    failed: list[str] = []
    for b in beats:
        speech = speech_for(b.asset_id)
        if proposer is None:
            out.extend(enumerate_spans(b, speech))
            continue
        try:
            out.extend(proposer.propose(b, speech, brief))
        except Exception as exc:  # noqa: BLE001
            # A proposer failing is a degraded cut, not a failed job: the
            # original beat is still a perfectly valid candidate.
            #
            # But it IS a degraded cut, and this used to say nothing at all.
            # Every beat can fall back and the run looks identical to one where
            # the model simply chose not to carve — which is how a cache that
            # dropped its words went unnoticed until somebody complained the
            # edit was too gentle. Counted here, reported by the caller.
            failed.append(type(exc).__name__)
            out.append(b)

    build.carved = sum(1 for s in out if s.kind != "beat")
    build.failed = failed
    return out
