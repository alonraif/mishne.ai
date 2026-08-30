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

from ...llm.base import LLMError, Truncated
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
        "**Score density, not size.** You are shown both whole answers and "
        "tighter spans carved out of them, and the solver spends a fixed number "
        "of seconds on whatever scores highest. So a 45-second answer and the "
        "12-second line at the heart of it are direct competitors for the same "
        "budget, and scoring them alike hands the viewer the 45-second one.\n\n"
        "When a long span and a shorter span carry substantially the same "
        "point, the shorter one scores HIGHER. The long version's extra "
        "material is not free — it is seconds taken from something else that "
        "could have been in the cut. Score the long version above the short one "
        "only when the extra seconds genuinely add something: a turn in the "
        "thought, a second idea, a payoff the short version sets up and never "
        "reaches.\n\n"
        "Read `pacing` in the brief and mean it. On a tight or hard-cutting "
        "brief, a span that runs beyond about a fifth of the target duration "
        "needs to justify every second, and usually cannot.\n\n"
        "`depends_on` lists beat ids this beat needs in order to make sense. A "
        "payoff without its setup reads as a non-sequitur; this field is how "
        "that gets prevented, so use it wherever a beat genuinely depends on "
        "earlier material.\n\n"
        "The transcript may be in any language, Hebrew included. Judge it in its "
        "own language and register — do not translate, do not penalise a beat "
        "for reading awkwardly in English, and write `rationale` in the same "
        "language as the transcript so the editor can read it."
    )

    #: Output budget per window. One row is roughly 45 tokens by the estimate
    #: in `llm/TASKS`, and was observed at 128 on real material because
    #: "one line" is a request rather than a constraint. The budget is generous
    #: because being wrong in this direction costs a fraction of a cent and
    #: being wrong in the other direction used to cost the whole job.
    MAX_TOKENS = 8192

    #: A window this small that still truncates is not a budget problem.
    MIN_CHUNK = 5

    def __init__(self, router, chunk: int = 25):
        self.router = router
        self.chunk = chunk

    def _score_window(self, window: list[Beat], brief_json: str,
                      out: dict[str, float], on_progress=None) -> None:
        """One window, halving and retrying if the answer comes back cut off.

        Truncation is not a model failing to follow instructions — it is us
        asking for more than the budget holds — so retrying the same request
        unchanged reproduces it exactly. What has to change is the size of the
        question, and halving converges in a couple of steps from any starting
        point.

        This replaces the failure mode that killed a 26-minute interview: one
        window of 40 candidates overran the budget, `.json()` raised at the call
        site where the router could not see it, and every already-scored window
        was thrown away with the job.
        """
        say = on_progress or (lambda *_: None)
        payload = [{"id": b.id, "speaker": b.speaker,
                    "seconds": round(b.duration_ms / 1000, 1),
                    # Which answer this came out of, and whether it is the
                    # whole thing. Without it the model cannot tell that a
                    # 45-second block and a 12-second line are the same
                    # material at two lengths — it sees two unrelated
                    # candidates and scores them on merit alone, which is how
                    # the long one wins. `parent` is what makes them
                    # competitors rather than options.
                    "parent": b.parent_id, "whole": b.kind == "beat",
                    "text": b.text, "flags": b.flags} for b in window]
        completion = self.router.complete(
            "score", system=self.SYSTEM, max_tokens=self.MAX_TOKENS,
            user=(f"Brief:\n{brief_json}\n\nBeats:\n"
                  f"{json.dumps(payload, ensure_ascii=False, indent=1)}\n\n"
                  "Candidates sharing a `parent` are the same answer at "
                  "different lengths and compete for the same seconds; "
                  "`whole` marks the untrimmed original.\n\n"
                  'Return ONLY a JSON array: [{"id":"...","score":0-100,'
                  '"depends_on":[],"rationale":"one line"}]\n'
                  # A limit rather than a wish. "one line" alone produced
                  # rationales three times the estimated length, which is what
                  # exhausted the budget in the first place.
                  "Keep each rationale under 15 words. Return every beat."))
        try:
            rows = completion.json()
        except Truncated:
            self.router.mark_unparsed(completion)
            if len(window) <= self.MIN_CHUNK:
                # Not a budget problem any more. Let it raise: the router's
                # retry and failover are better placed to judge this than a
                # loop that can only make the question smaller.
                raise
            half = max(self.MIN_CHUNK, len(window) // 2)
            say(f"answer truncated — retrying in windows of {half}")
            for i in range(0, len(window), half):
                self._score_window(window[i:i + half], brief_json, out, say)
            return
        except LLMError:
            self.router.mark_unparsed(completion)
            raise

        for row in rows:
            out[row["id"]] = float(row["score"])
            for b in window:
                if b.id == row["id"]:
                    b.rationale = row.get("rationale", "")
                    b.depends_on = row.get("depends_on", []) or []

    def score(self, beats: list[Beat], brief, on_progress=None) -> dict[str, float]:
        out: dict[str, float] = {}
        brief_json = json.dumps(
            {k: v for k, v in brief.to_dict().items() if k != "notes_raw"},
            indent=2)

        windows = [beats[i:i + self.chunk]
                   for i in range(0, len(beats), self.chunk)]
        if len(windows) > 1:
            # Each window scores a different set of candidates and writes
            # different keys, so they are independent in the same way the
            # per-beat span calls are. Five windows at ~33s each was 163s of
            # sequential waiting.
            from concurrent.futures import ThreadPoolExecutor

            from ...config import get_settings

            try:
                workers = max(1, int(get_settings().llm_concurrency))
            except Exception:  # noqa: BLE001
                workers = 8
            # `out` is a plain dict written from several threads. Each window
            # owns a disjoint set of ids, and CPython dict assignment is atomic,
            # so there is nothing to guard — but the disjointness is the reason,
            # not luck, and it stops being true if windows ever overlap.
            with ThreadPoolExecutor(max_workers=min(workers, len(windows))) as pool:
                list(pool.map(
                    lambda w: self._score_window(w, brief_json, out, on_progress),
                    windows))
        else:
            for window in windows:
                self._score_window(window, brief_json, out, on_progress)

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
