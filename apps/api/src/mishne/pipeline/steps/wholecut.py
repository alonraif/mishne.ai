"""One model call that reads the whole interview and makes the cut.

## Why this exists

Everywhere else in this pipeline, no model ever sees the whole piece. Span
proposal is one call per beat and sees one answer; scoring is windowed and sees
25 candidates; the solver assembles from scores. Nothing is ever handed the
4,542 words and asked to make a two-minute cut — which is exactly the act
anyone imagines when they compare this to asking a model to shorten a long
article, and it is the act the pipeline decomposes out of existence.

That decomposition bought real things. Per-beat calls parallelise, windowed
scoring bounds the context, and the solver enforces duration, speaker balance
and dependencies as guarantees rather than hopes. What it costs is the one
judgement that needs the whole piece in view: what this cut is *about*, and
therefore what can go.

So this is an alternative front half — stages 6 and 7 in a single call — and
the solver still runs afterwards. Selection stays swappable (ADR-0007), the
silence gate still refuses illegal cuts, and duration is still a constraint
rather than a request. What changes is that the material is chosen by something
that read all of it.

## What it does NOT do

Rewrite. Every span is a contiguous run of words the speaker actually said,
bounded by indices the recording permits. A summariser merges scattered
sentences, adds connective tissue and reorders freely; none of that survives
contact with a timeline. This is the constraint that makes an interview harder
than an email thread, not the model's capability.

## Cost

One call: roughly 8-12k input and 2-4k output on a 26-minute interview, against
43 calls and $1.20 for the decomposed path. If the cut is comparable, it is an
order of magnitude cheaper. If it is worse, the comparison is the point.
"""

from __future__ import annotations

import json

from ...llm.base import LLMError, Truncated
from .propose import cut_points, span
from .structure import Beat

SYSTEM = (
    "You are cutting a finished short piece out of a raw interview. You are "
    "given the entire transcript, and you decide what the piece is and what "
    "goes in it.\n\n"

    "## What an interview is\n\n"
    "A series of questions and answers. The questions are usually not in the "
    "cut. An answer is rarely usable whole: it has a run-up before the point, "
    "a restatement after it, an aside that goes nowhere, a qualification "
    "nobody needs. **You may take the part of an answer that carries it and "
    "leave the rest**, as long as what remains stands on its own and is still "
    "true to what was said. That is the ordinary work of editing and it is "
    "what you are here to do.\n\n"
    "Less is more. A viewer who hears one sharp line remembers it; a viewer "
    "who hears the same idea three times remembers none of them. When two "
    "passages make the same point, take the better one and drop the other "
    "entirely — do not include both because both are good.\n\n"

    "## The hard constraint\n\n"
    "Each beat lists CUT_POINTS: the only word indices where a cut is "
    "physically possible, because the recording has silence there. **A span "
    "must start and end on those indices.** A cut anywhere else clips the "
    "speech and is thrown away, however well-chosen the words. This is the "
    "difference between editing a recording and summarising a document: you "
    "cannot write a bridging phrase, cannot merge two sentences said minutes "
    "apart, and cannot cut where the speaker did not pause.\n\n"

    "## Shape\n\n"
    "Aim for the target duration in the brief, and get there with several "
    "short spans rather than a few long ones. A span that runs beyond about a "
    "fifth of the target had better be carrying the whole piece. Order the "
    "spans as the finished cut should play: they will be assembled in the "
    "order you return them, subject to the brief's narrative shape.\n\n"
    "Do not propose a span that depends on words you dropped — a pronoun with "
    "no referent, an answer with no question, a 'but' with nothing before "
    "it.\n\n"
    "Score each span 0-100 for how much it earns its place. Spread the scores "
    "and mean them: a downstream solver uses them to hit the duration exactly, "
    "so offer somewhat more material than the target and let the scores say "
    "what to lose first.\n\n"

    "The transcript may be in any language, Hebrew included. Judge it in its "
    "own language and register, and write `rationale` in that same language."
)


def _payload(beats: list[Beat], speech_for) -> list[dict]:
    """Every beat, its words numbered, and where it may legally be cut."""
    out = []
    for b in beats:
        points = cut_points(b, speech_for(b.asset_id))
        out.append({
            "beat": b.id,
            "speaker": b.speaker,
            "seconds": round(b.duration_ms / 1000, 1),
            "cut_points": points,
            # Numbered so a span is a pair of indices rather than a quotation
            # the model might paraphrase. Indices cannot be misremembered.
            "words": [{"i": i, "w": w.text} for i, w in enumerate(b.words)],
        })
    return out


#: Where the output budget starts, and how far it may climb. A 26-minute
#: interview truncated at 8_192: the answer is 20-40 spans of maybe 40 tokens
#: each, but the reasoning blocks in front of them are the bulk of the output
#: and scale with how much transcript there is to think about.
START_MAX_TOKENS = 16_384
CEILING_MAX_TOKENS = 65_536


def propose_cut(beats: list[Beat], speech_for, brief, router,
                max_tokens: int = START_MAX_TOKENS,
                ) -> tuple[list[Beat], dict[str, float]]:
    """One call. Returns candidate spans and their scores.

    Beats that the model did not choose are returned as candidates too, scored
    zero: the solver needs something to fall back on if the chosen spans cannot
    make the duration window, and a beat nobody picked is still real material.

    ## Truncation grows the budget rather than shrinking the question

    The windowed scorer answers a truncated window by halving it and asking
    again, which works because its question is a list that can be split. This
    question cannot be: the entire value of the call is that one model saw the
    whole transcript at once, and asking about half of it is asking a different
    question and getting the decomposed pipeline's answer by another route.

    So the budget doubles instead, up to a ceiling. If even the ceiling
    truncates, the material is too long for a single pass on this model and
    saying so is more useful than quietly returning half a cut.
    """
    payload = _payload(beats, speech_for)
    brief_json = json.dumps(
        {k: v for k, v in brief.to_dict().items() if k != "notes_raw"}, indent=1)
    user = (f"Brief:\n{brief_json}\n\n"
            f"TRANSCRIPT:\n{json.dumps(payload, ensure_ascii=False)}\n\n"
            'Return ONLY a JSON array, in cut order: '
            '[{"beat":"beat_0007","start":int,"end":int,"score":0-100,'
            '"rationale":"one line"}]\n'
            "Keep each rationale under 12 words. Return only the spans that "
            "make the cut — do not narrate your reasoning.")

    budget = max_tokens
    while True:
        completion = router.complete(
            "spans", system=SYSTEM, max_tokens=budget, user=user)
        try:
            rows = completion.json()
            break
        except Truncated:
            router.mark_unparsed(completion)
            if budget >= CEILING_MAX_TOKENS:
                raise
            budget = min(budget * 2, CEILING_MAX_TOKENS)
            propose_cut.grew_to = budget
        except LLMError:
            router.mark_unparsed(completion)
            raise

    by_id = {b.id: b for b in beats}
    legal = {b.id: set(cut_points(b, speech_for(b.asset_id))) for b in beats}

    candidates: list[Beat] = []
    scores: dict[str, float] = {}
    refused = 0
    for row in rows:
        parent = by_id.get(str(row.get("beat", "")))
        if parent is None:
            refused += 1
            continue
        try:
            lo, hi = int(row.get("start", -1)), int(row.get("end", -1))
        except (TypeError, ValueError):
            refused += 1
            continue
        # The same gate as the per-beat proposer, and for the same reason: a
        # span off the legal points is dropped rather than snapped to something
        # nearby, because snapping moves the cut away from the thought the
        # rationale describes.
        if lo not in legal[parent.id] or hi not in legal[parent.id] or hi <= lo:
            refused += 1
            continue
        s = span(parent, lo, hi, "trim", row.get("rationale", ""))
        if s is None or s is parent:
            refused += 1
            continue
        candidates.append(s)
        scores[s.id] = float(row.get("score", 50))

    router.note_violations("spans", refused, len(rows), completion)

    # Unchosen beats, at zero: material the solver may reach for only if the
    # chosen spans cannot fill the window. Scored zero rather than omitted so
    # `solve` sees the same universe of material either way.
    for b in beats:
        candidates.append(b)
        scores.setdefault(b.id, 0.0)

    propose_cut.refused = refused
    propose_cut.offered = len(rows)
    return candidates, scores
