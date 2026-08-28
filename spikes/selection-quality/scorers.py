"""Beat scorers — stage 6.

Two implementations behind one interface, because the spike has to run today
without an API key and has to measure the real thing when there is one.

**HeuristicScorer** is a deliberate floor, not a product. It exists so the
harness is runnable and so there is an honest answer to "how much of the score
comes from the language model?" If the LLM scorer cannot beat crude lexical
features by a wide margin, that is the finding — and it would be an expensive
thing to discover after building the pipeline around it.

**AnthropicScorer** is the real stage 6, with the prompt versioned so results
are reproducible and comparable across runs.
"""

from __future__ import annotations

import json
import os
import re
from typing import Protocol

from corpus import Beat

PROMPT_VERSION = "2026-08-28.1"

FILLER = re.compile(
    r"\b(um|uh|erm|like|you know|i mean|sort of|kind of|basically)\b", re.I
)
HEDGE = re.compile(r"\b(maybe|perhaps|possibly|i think|i guess|probably)\b", re.I)
NUMERIC = re.compile(r"\b\d[\d,.]*\b")
FIRST_PERSON = re.compile(r"\b(i|my|we|our)\b", re.I)


class Scorer(Protocol):
    name: str

    def score(self, beats: list[Beat], brief: dict) -> dict[str, float]:
        """Return {beat_id: 0..100}."""
        ...


class HeuristicScorer:
    """Lexical features only. No model, no network, no judgement.

    The features are the obvious ones — length, concrete numbers, first-person
    voice, absence of filler and hedging. They approximate 'sounds like a usable
    soundbite' and nothing more. Treat its score as the number to beat.
    """

    name = "heuristic"

    def score(self, beats: list[Beat], brief: dict) -> dict[str, float]:
        out: dict[str, float] = {}
        lengths = [len(b.text.split()) for b in beats] or [1]
        median_len = sorted(lengths)[len(lengths) // 2] or 1

        for b in beats:
            words = b.text.split()
            n = len(words)
            s = 50.0

            # Length: longer is better, with diminishing returns.
            #
            # The first version peaked at the median length, on the theory that
            # extremes are suspect. That is backwards for interview material and
            # the diagnostic caught it: AUC 0.335, i.e. reliably *worse* than
            # chance. A soundbite an editor keeps is usually a complete thought,
            # and complete thoughts are long; the short beats are questions,
            # acknowledgements and half-sentences.
            #
            # Changed because the prior was wrong, not because it scored badly
            # on the fixture. Tuning this scorer against a fixture would destroy
            # its only purpose, which is to be an honest control.
            ratio = min(3.0, n / median_len)
            s += 16 * (ratio / (ratio + 0.8))

            if NUMERIC.search(b.text):
                s += 8
            if FIRST_PERSON.search(b.text):
                s += 6

            fillers = len(FILLER.findall(b.text))
            s -= 9 * fillers
            s -= 6 * len(HEDGE.findall(b.text))

            # Distinct vocabulary as a weak proxy for information density.
            if n:
                s += 12 * (len({w.lower() for w in words}) / n - 0.6)

            for flag, penalty in (
                ("filler", 25), ("false_start", 30), ("off_mic", 35),
                ("crosstalk", 25), ("low_confidence", 15), ("retake", -5),
            ):
                if flag in b.flags:
                    s -= penalty

            out[b.id] = max(0.0, min(100.0, s))
        return out


class AnthropicScorer:
    """Stage 6 as it will actually ship.

    Chunked with overlap for parallelism and consistent calibration, structured
    output, pinned model and prompt version. `depends_on` is requested but not
    yet consumed by the solver here — see select.py.
    """

    name = "anthropic"

    def __init__(self, model: str = "claude-sonnet-4-5", chunk: int = 40):
        import anthropic

        self.client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self.model = model
        self.chunk = chunk

    SYSTEM = (
        "You score transcript beats from raw interview or presenter footage for "
        "inclusion in a rough cut. You are not writing the cut; you are judging "
        "each beat on its own merits against the brief.\n\n"
        "Score 0-100 on overall value to the piece. Reward: a point made "
        "clearly and completely, concrete detail, quotable phrasing, emotional "
        "weight that is earned, and material that stands on its own without "
        "setup. Penalise: filler, false starts, repetition of a point already "
        "made better elsewhere, hedging, and anything that only makes sense with "
        "context that is unlikely to survive the cut.\n\n"
        "Be decisive. A flat distribution around 50 is useless to the solver "
        "that consumes these scores — spread them out and mean it."
    )

    def score(self, beats: list[Beat], brief: dict) -> dict[str, float]:
        out: dict[str, float] = {}
        for i in range(0, len(beats), self.chunk):
            window = beats[i : i + self.chunk]
            payload = [
                {"id": b.id, "speaker": b.speaker, "seconds": round(b.frames / 25, 1),
                 "text": b.text, "flags": b.flags}
                for b in window
            ]
            msg = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=self.SYSTEM,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Brief:\n{json.dumps(brief, indent=2)}\n\n"
                        f"Beats:\n{json.dumps(payload, indent=2)}\n\n"
                        "Return ONLY a JSON array: "
                        '[{"id": "...", "score": 0-100, "depends_on": [], '
                        '"rationale": "one line"}]'
                    ),
                }],
            )
            text = msg.content[0].text.strip()
            text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
            for row in json.loads(text):
                out[row["id"]] = float(row["score"])

        # Any beat the model skipped scores zero rather than silently vanishing.
        for b in beats:
            out.setdefault(b.id, 0.0)
        return out


def get_scorer(name: str) -> Scorer:
    if name == "heuristic":
        return HeuristicScorer()
    if name == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Run with --scorer heuristic to "
                "exercise the harness without a model."
            )
        return AnthropicScorer()
    raise ValueError(f"unknown scorer: {name}")
