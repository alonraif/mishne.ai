"""Span proposal runs its calls together, and still gets the same answer.

38 calls at ~26 seconds each was 980s of a 1,158s job — 85% of the wall clock,
almost all of it waiting. The calls are one per beat and share nothing, so they
run concurrently now. Two things had to be true first, and both are asserted
here because neither fails loudly.

**Order.** The finished cut's order comes from beat order. Appending results as
they arrive would shuffle the timeline by network latency, and the artifacts
would still validate — the cut would just be wrong in a way that looks like an
editorial choice.

**Attribution.** A stage reports what it learned about an answer after
`complete` has returned: whether it parsed, and how many proposals the silence
gate refused. That used to find its record by scanning backwards for the last
matching one, which with two calls in flight lands one beat's result on
another's. Compliance is the one quality signal available without a corpus
(ADR-0011), so a race that scrambles it quietly poisons the only measurement
there is.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mishne.llm.base import CallRecord, Completion, Ledger  # noqa: E402
from mishne.pipeline.steps import propose  # noqa: E402
from mishne.pipeline.steps.structure import Beat  # noqa: E402
from mishne.pipeline.steps.vad import SpeechMap  # noqa: E402


def _beats(n: int) -> list[Beat]:
    from mishne.asr.base import Word

    out = []
    for b in range(n):
        words = [Word(text=f"b{b}w{i}", start_ms=i * 1000,
                      end_ms=(i + 1) * 1000 - 500) for i in range(40)]
        out.append(Beat(id=f"beat_{b:04d}", idx=b, speaker="T1", start_ms=0,
                        end_ms=40_000, text=" ".join(w.text for w in words),
                        words=words, asset_id="ast_1"))
    return out


SPEECH = SpeechMap(speech=[(i * 1000, (i + 1) * 1000 - 500) for i in range(40)],
                   duration_ms=40_000)


class SlowProposer:
    """Carves one span per beat, slowly, and records concurrency."""

    def __init__(self, delay: float = 0.05):
        self.delay = delay
        self.live = 0
        self.peak = 0
        self._lock = threading.Lock()

    def propose(self, beat, speech, brief):
        with self._lock:
            self.live += 1
            self.peak = max(self.peak, self.live)
        try:
            time.sleep(self.delay)
            carved = propose.span(beat, 0, 8, "trim", "r")
            return [beat] + ([carved] if carved else [])
        finally:
            with self._lock:
                self.live -= 1


class _Brief:
    target_duration_s = 120
    tone: list[str] = []


def test_the_calls_actually_overlap():
    proposer = SlowProposer()
    propose.build(_beats(8), lambda _a: SPEECH, _Brief(), proposer)
    assert proposer.peak > 1, "proposal is still running one beat at a time"


def test_order_survives_concurrency():
    """Results are written into a slot per beat, not appended on arrival."""
    beats = _beats(12)
    out = propose.build(beats, lambda _a: SPEECH, _Brief(), SlowProposer())

    parents = [b.parent_id for b in out]
    first_seen = []
    for p in parents:
        if p not in first_seen:
            first_seen.append(p)
    assert first_seen == [b.id for b in beats]


def test_a_failing_beat_still_yields_its_whole_self():
    class Boom(SlowProposer):
        def propose(self, beat, speech, brief):
            if beat.idx == 3:
                raise RuntimeError("provider had a moment")
            return super().propose(beat, speech, brief)

    beats = _beats(6)
    out = propose.build(beats, lambda _a: SPEECH, _Brief(), Boom())

    assert {b.id for b in beats} <= {b.parent_id for b in out}
    assert propose.build.failed == ["RuntimeError"]


# ── attribution ───────────────────────────────────────────────────────────


def test_a_result_lands_on_its_own_call_not_the_most_recent():
    """The race the backwards scan created, made deterministic.

    Two calls are recorded, then the FIRST one's constraint result is
    reported. Under the old scan that landed on the second — the most recent
    matching record — and the two calls' obedience figures were swapped.
    """
    from mishne.llm.router import Router

    router = Router()
    first = router.ledger.add(
        CallRecord(task="spans", provider="p", model="m", ok=True))
    second = router.ledger.add(
        CallRecord(task="spans", provider="p", model="m", ok=True))

    completion = Completion(text="", model="m", provider="p",
                            record_seq=first.seq)
    router.note_violations("spans", 3, 9, completion)

    assert (first.violations, first.proposals) == (3, 9)
    assert (second.violations, second.proposals) == (0, 0)


def test_the_ledger_numbers_records_under_concurrent_writes():
    ledger = Ledger()

    def add_many():
        for _ in range(50):
            ledger.add(CallRecord(task="spans", provider="p", model="m", ok=True))

    threads = [threading.Thread(target=add_many) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    seqs = [c.seq for c in ledger.calls]
    assert len(ledger.calls) == 200
    assert len(set(seqs)) == 200, "two records were given the same seq"
