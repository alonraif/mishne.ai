"""A scoring window that comes back cut off.

This is the failure that killed the first real 26-minute run, and it is worth
recording exactly what it looked like, because three separate things disguised
it and each one is now a test below.

The model was asked to score 40 candidates in one call with a 4096-token
budget. It wrote perfectly good JSON until the budget ran out, mid-string, at
the 33rd row. What the operator saw was:

    LLMError: anthropic/claude-sonnet-5 did not return valid JSON:
              Unterminated string starting at: line 33 column 65

— which reads as a model that cannot follow instructions, and is the wrong
conclusion about a model that was doing exactly what it was told until we cut
it off. The right conclusion is that we asked for more than the budget held.

Three disguises:

1. **No `stop_reason`.** The vendor says "max_tokens"; the provider dropped it,
   so truncation was indistinguishable from malformed output.
2. **The failure happened where the router could not see it.** `.json()` is
   called by the stage, after `complete()` returned, so there was no retry, no
   failover, and every already-scored window was thrown away with the job.
3. **The ledger said the call succeeded.** `ok=True` and the cost were recorded
   before anything tried to parse it, so the summary read `1/1 ok` for a call
   that produced nothing. Parse compliance is one of the few things ADR-0011
   can measure without a corpus, and it was the one thing not recorded.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mishne.llm.base import Completion, LLMError, Truncated  # noqa: E402
from mishne.llm.router import Router  # noqa: E402
from mishne.pipeline.steps.score import ModelScorer  # noqa: E402
from mishne.pipeline.steps.structure import Beat  # noqa: E402


def _beats(n: int) -> list[Beat]:
    return [
        Beat(id=f"beat_{i:04d}", idx=i, speaker="T1", start_ms=i * 1000,
             end_ms=(i + 1) * 1000, text=f"line {i}", asset_id="ast_1")
        for i in range(n)
    ]


class _Brief:
    def to_dict(self):
        return {"target_duration_s": 120, "notes_raw": "unused"}


class FakeRouter:
    """Truncates any window larger than `holds`, like a real token budget."""

    def __init__(self, holds: int):
        self.holds = holds
        self.windows: list[int] = []
        self.unparsed = 0

    def complete(self, task, *, system, user, max_tokens=4096, **kw):
        ids = [line.split('"')[3] for line in user.splitlines()
               if '"id"' in line and "beat_" in line]
        self.windows.append(len(ids))
        if len(ids) > self.holds:
            # What a real truncation looks like: valid JSON, cut mid-string.
            partial = json.dumps(
                [{"id": i, "score": 50, "depends_on": [], "rationale": "x"}
                 for i in ids[: self.holds]]
            )[:-8]
            return Completion(text=partial, model="fake-1", provider="fake",
                              output_tokens=max_tokens, stop_reason="max_tokens")
        return Completion(
            text=json.dumps([{"id": i, "score": 50, "depends_on": [],
                              "rationale": "fine"} for i in ids]),
            model="fake-1", provider="fake", stop_reason="end_turn")

    def mark_unparsed(self, completion):
        self.unparsed += 1


# ── the fix ───────────────────────────────────────────────────────────────


def test_a_truncated_window_is_retried_smaller_and_every_beat_is_scored():
    """The job survives. Before this, one overrun killed a 26-minute run and
    discarded every window already paid for."""
    router = FakeRouter(holds=8)
    beats = _beats(50)

    scores = ModelScorer(router, chunk=25).score(beats, _Brief())

    assert len(scores) == 50
    assert all(v == 50 for v in scores.values())
    # It halved rather than giving up: 25 -> 12 -> 6, and 6 <= 8 holds.
    assert 25 in router.windows and min(router.windows) <= 8


def test_the_ledger_is_told_the_answer_was_unusable():
    """`ok` records that the call returned; `parsed` records whether the money
    bought anything. A truncated answer is a successful call that produced
    nothing, and the summary used to report it as `1/1 ok`."""
    router = FakeRouter(holds=8)
    ModelScorer(router, chunk=25).score(_beats(20), _Brief())
    assert router.unparsed > 0


def test_a_window_at_the_floor_stops_halving():
    """A five-beat window that still truncates is not a budget problem, and a
    loop whose only move is to ask for less cannot fix it. It raises, where the
    router's retry and failover can judge it."""
    router = FakeRouter(holds=0)  # truncates everything
    with pytest.raises(Truncated):
        ModelScorer(router, chunk=25).score(_beats(10), _Brief())
    assert min(router.windows) <= ModelScorer.MIN_CHUNK


def test_an_untruncated_window_makes_exactly_one_call():
    """The retry path must not cost anything when nothing is wrong."""
    router = FakeRouter(holds=1000)
    ModelScorer(router, chunk=25).score(_beats(20), _Brief())
    assert router.windows == [20]


# ── the disguises ─────────────────────────────────────────────────────────


def test_truncation_is_told_apart_from_bad_json():
    """The distinction the operator did not have. Same unparseable text, two
    completely different diagnoses and two different responses."""
    cut_off = Completion(text='[{"id":"a","rationale":"unter',
                         model="m", provider="p", stop_reason="max_tokens")
    nonsense = Completion(text="I'm afraid I can't do that",
                          model="m", provider="p", stop_reason="end_turn")

    with pytest.raises(Truncated):
        cut_off.json()
    with pytest.raises(LLMError) as bad:
        nonsense.json()
    assert not isinstance(bad.value, Truncated)


def test_the_truncation_message_says_what_to_do_about_it():
    """"did not return valid JSON" sent the last reader looking at the model.
    The budget is what changed."""
    cut = Completion(text="[{", model="m", provider="p",
                     output_tokens=4096, stop_reason="max_tokens")
    with pytest.raises(Truncated) as exc:
        cut.json()
    assert "token budget" in str(exc.value)
    assert "less at a time" in str(exc.value)


def test_the_openai_spelling_of_truncation_counts_too():
    """Anthropic says "max_tokens", every OpenAI-compatible vendor says
    "length" — and three of the four providers are on that path."""
    assert Completion(text="", model="m", provider="p",
                      stop_reason="length").truncated
    assert not Completion(text="", model="m", provider="p",
                          stop_reason="stop").truncated


def test_a_provider_that_reports_nothing_is_not_assumed_complete():
    """Absence of a stop reason is absence of evidence. It must not read as
    "finished cleanly" — that would restore the original bug for any vendor
    that omits the field."""
    silent = Completion(text='[{"id":"a","score":1}]', model="m", provider="p")
    assert silent.stop_reason == ""
    assert not silent.truncated
    # It parses, because the text is valid — the point is that nothing here
    # claims completion on the strength of a missing field.
    assert silent.json() == [{"id": "a", "score": 1}]


def test_the_router_records_parse_failure_against_the_right_call():
    router = Router()
    from mishne.llm.base import CallRecord

    router.ledger.add(CallRecord(task="score", provider="p", model="other", ok=True))
    router.ledger.add(CallRecord(task="score", provider="p", model="m", ok=True))
    router.mark_unparsed(
        Completion(text="", model="m", provider="p", stop_reason="max_tokens")
    )

    assert router.ledger.calls[-1].parsed is False
    assert router.ledger.calls[-1].stop_reason == "max_tokens"
    assert router.ledger.calls[0].parsed is True
