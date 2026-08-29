"""Stage 6 — score beats.

Two scorers behind one interface. `HeuristicScorer` is a **control, not a
product**: lexical features only, no model. It exists so the pipeline runs
offline and so there is an honest answer to "how much of the quality comes from
the language model?"

Do not tune the heuristic against a fixture, and do not show its cuts to an
editor and treat their reaction as a verdict on the product.
"""

from __future__ import annotations

import json
import os
import re
from typing import Protocol

from .structure import Beat

PROMPT_VERSION = "2026-08-29.1"

# Lexical features, per language.
#
# These are English-centric by construction and there is no honest way around
# that: "does this sound like a usable soundbite" is a language-specific
# question. The Hebrew patterns below are a first pass written without native
# review, and the heuristic control is therefore **weaker on Hebrew than on
# English** — which matters, because it means an offline run on Hebrew material
# proves the plumbing and nothing else.
#
# The practical consequence: Hebrew needs the language model scorer. Do not
# judge Hebrew output produced by the control.
FEATURES = {
    "en": {
        "filler": re.compile(
            r"\b(um|uh|erm|like|you know|i mean|sort of|kind of|basically)\b", re.I),
        "hedge": re.compile(
            r"\b(maybe|perhaps|possibly|i think|i guess|probably)\b", re.I),
        "first_person": re.compile(r"\b(i|my|we|our)\b", re.I),
    },
    "he": {
        "filler": re.compile(r"(אה|אמ|כאילו|יעני|זהו|בעצם|אתה יודע|סוג של)"),
        "hedge": re.compile(r"(אולי|נדמה לי|אני חושב|אני מניח|כנראה|בערך)"),
        "first_person": re.compile(r"(אני|שלי|אנחנו|שלנו|לי|לנו)"),
    },
}
NUMERIC = re.compile(r"[\d\u0660-\u0669][\d,.\u0660-\u0669]*")


def features_for(language: str | None):
    return FEATURES.get((language or "en").split("-")[0].lower(), FEATURES["en"])

# Beats carrying these never belong in a cut, whatever they score.
DISQUALIFYING = {"false_start", "retake_signal", "superseded", "off_mic", "empty"}


class Scorer(Protocol):
    name: str

    def score(self, beats: list[Beat], brief) -> dict[str, float]:
        ...


class HeuristicScorer:
    name = "heuristic"

    def score(self, beats: list[Beat], brief) -> dict[str, float]:
        out: dict[str, float] = {}
        feats = features_for(getattr(brief, "language", "en"))
        lengths = [len(b.text.split()) for b in beats] or [1]
        median = sorted(lengths)[len(lengths) // 2] or 1

        for b in beats:
            words = b.text.split()
            n = len(words)
            s = 50.0

            # Longer is better, with diminishing returns. A beat an editor keeps
            # is usually a complete thought, and complete thoughts are long; the
            # short ones are questions and acknowledgements.
            ratio = min(3.0, n / median)
            s += 16 * (ratio / (ratio + 0.8))

            if NUMERIC.search(b.text):
                s += 8
            if feats["first_person"].search(b.text):
                s += 6
            s -= 9 * len(feats["filler"].findall(b.text))
            s -= 6 * len(feats["hedge"].findall(b.text))
            if n:
                s += 12 * (len({w.lower() for w in words}) / n - 0.6)

            for flag, penalty in (("filler", 25), ("false_start", 30),
                                  ("off_mic", 35), ("crosstalk", 15),
                                  ("low_confidence", 15), ("superseded", 40),
                                  ("retake_signal", 45)):
                if flag in b.flags:
                    s -= penalty

            out[b.id] = max(0.0, min(100.0, s))
        return out


class ModelScorer:
    """Scoring as it ships. Vendor-agnostic; the router chooses the model.

    The prompt is pinned and versioned so two runs are comparable. Which model
    ran it is recorded per job rather than fixed here — see llm/router.py.
    """

    name = "model"

    SYSTEM = (
        "You score transcript beats from raw interview or presenter footage for "
        "inclusion in a rough cut. You are not writing the cut; you judge each "
        "beat on its own merits against the brief.\n\n"
        "Score 0-100 on value to the piece. Reward a point made clearly and "
        "completely, concrete detail, quotable phrasing, earned emotional "
        "weight, and material that stands alone without setup. Penalise filler, "
        "false starts, repetition of a point made better elsewhere, hedging, and "
        "anything that only makes sense with context unlikely to survive the "
        "cut.\n\n"
        "Be decisive. A flat distribution around 50 is useless to the solver "
        "that consumes these scores — spread them out and mean it.\n\n"
        "`depends_on` lists beat ids this beat needs in order to make sense. A "
        "payoff without its setup reads as a non-sequitur; this field is how "
        "that gets prevented, so use it wherever a beat genuinely depends on "
        "earlier material.\n\n"
        "The transcript may be in any language, Hebrew included. Judge it in its "
        "own language and register — do not translate, do not penalise a beat "
        "for reading awkwardly in English, and write `rationale` in the same "
        "language as the transcript so the editor can read it."
    )

    def __init__(self, router, chunk: int = 40):
        self.router = router
        self.chunk = chunk

    def score(self, beats: list[Beat], brief) -> dict[str, float]:
        out: dict[str, float] = {}
        brief_json = json.dumps(
            {k: v for k, v in brief.to_dict().items() if k != "notes_raw"},
            indent=2)

        for i in range(0, len(beats), self.chunk):
            window = beats[i:i + self.chunk]
            payload = [{"id": b.id, "speaker": b.speaker,
                        "seconds": round(b.duration_ms / 1000, 1),
                        "text": b.text, "flags": b.flags} for b in window]
            completion = self.router.complete(
                "score", system=self.SYSTEM, max_tokens=4096,
                user=(f"Brief:\n{brief_json}\n\nBeats:\n"
                      f"{json.dumps(payload, ensure_ascii=False, indent=1)}\n\n"
                      'Return ONLY a JSON array: [{"id":"...","score":0-100,'
                      '"depends_on":[],"rationale":"one line"}]'))
            for row in completion.json():
                out[row["id"]] = float(row["score"])
                for b in window:
                    if b.id == row["id"]:
                        b.rationale = row.get("rationale", "")
                        b.depends_on = row.get("depends_on", []) or []

        for b in beats:
            out.setdefault(b.id, 0.0)
        return out


def get_scorer(name: str = "auto", router=None) -> Scorer:
    """`auto` uses a model when any vendor key is present, the control else."""
    if name == "auto":
        name = ("model" if router is not None
                and router.available_for("score") else "heuristic")
    if name == "heuristic":
        return HeuristicScorer()
    if name in ("model", "claude", "llm"):
        if router is None or not router.available_for("score"):
            raise RuntimeError(
                "No API key for scoring. Set ANTHROPIC_API_KEY, OPENAI_API_KEY,"
                " GEMINI_API_KEY or XAI_API_KEY — or use --scorer heuristic to "
                "run offline, and treat its cuts as a plumbing test, not a "
                "product.")
        return ModelScorer(router)
    raise ValueError(f"unknown scorer: {name}")


def apply_disqualifiers(beats: list[Beat], scores: dict[str, float],
                        keep_filler: bool = False) -> dict[str, float]:
    """Zero out beats no cut should contain, whatever the scorer said.

    A superseded take or an announced retake is not low quality — it is *wrong*,
    and a scorer that happens to like its wording should not be able to get it
    into the cut. Kept separate from scoring so the rule is visible rather than
    buried in a prompt.
    """
    out = dict(scores)
    for b in beats:
        bad = set(b.flags) & DISQUALIFYING
        if not keep_filler and "filler" in b.flags:
            bad.add("filler")
        if bad:
            out[b.id] = 0.0
    return out
