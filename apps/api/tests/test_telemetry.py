"""The content rule, tested rather than assumed — and the traces it governs.

The definition of done for C3 says "no customer content appears anywhere in the
telemetry, tested rather than assumed". This is that test, and it is the reason
the module has a `blocked()` predicate at all: a rule that only exists inside a
`for` loop over a set literal cannot be asserted about a key nobody has written
yet.

`scrub` blocks by key. The failure mode it was always going to have is that a
field added later — `speaker_name`, `source_path`, `brief_text` — passes
straight through and nothing tells you, because the safeguard's failure is
silent by construction. So there are two rules now: exact keys, and suffixes
that catch the shape of a name rather than its spelling.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mishne import telemetry  # noqa: E402
from mishne.logging import REDACTED, blocked, scrub  # noqa: E402

SECRET = "she describes the settlement in the third answer"


# ── the rule ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "key",
    [
        "filename", "text", "transcript", "notes", "notes_raw", "brief",
        "rationale", "s3_key", "path", "email", "prompt", "completion",
        "title", "speaker", "media", "url",
    ],
)
def test_the_known_content_keys_are_blocked(key):
    assert scrub(None, None, {key: SECRET})[key] == REDACTED


@pytest.mark.parametrize(
    "key",
    ["speaker_text", "source_path", "original_filename", "download_url",
     "system_prompt", "message_content"],
)
def test_a_key_nobody_added_to_the_list_is_still_blocked(key):
    """The half of the rule that covers the future.

    Every one of these is a plausible field name that did not exist when the
    list was written, and under the old exact-match rule every one of them
    would have carried its value into a log.
    """
    assert blocked(key)
    assert scrub(None, None, {key: SECRET})[key] == REDACTED


def test_the_fields_telemetry_is_made_of_are_not_blocked():
    """A safeguard that redacts the useful fields gets switched off.

    `_name` is deliberately not a blocked suffix for exactly this reason: it
    would take `step_name` and `provider_name` with it and leave a trace of
    `<redacted>` rows, which is how somebody ends up removing the processor.
    """
    event = {
        "job_id": "job_1", "org_id": "org_1", "step": "transcribe",
        "step_name": "transcribe", "provider_name": "acme", "asset_id": "ast_1",
        "seconds": 41.5, "attempt": 2, "from_cache": True, "status": "done",
        "detail": "412 beats · 6 of 9 windows", "reason": "TimeoutError",
    }
    assert scrub(None, None, dict(event)) == event


def test_content_nested_inside_a_field_is_redacted():
    """`scrub` used to look only at the top level, so a payload passed as one
    argument was not examined at all."""
    scrubbed = scrub(None, None, {
        "job_id": "job_1",
        "context": {"asset": {"filename": SECRET}, "counts": {"beats": 12}},
        "errors": [{"path": SECRET}],
    })
    blob = str(scrubbed)
    assert SECRET not in blob
    assert "settlement" not in blob
    # And the counts survive, which is the point of redacting by key rather
    # than dropping the structure.
    assert scrubbed["context"]["counts"]["beats"] == 12


# ── the spans ─────────────────────────────────────────────────────────────


def test_tracing_is_off_until_something_turns_it_on():
    """`run.py` on a laptop, and the whole test suite, run with no exporter and
    must not need the SDK installed."""
    telemetry.reset()
    assert not telemetry.configured()
    with telemetry.span("step.transcribe", job_id="job_1") as trace:
        trace.set(seconds=1.0)
        trace.failed(RuntimeError("nothing should happen"))


def test_a_span_never_carries_a_blocked_attribute():
    """Span attributes bypassed `scrub` entirely before this — a trace is a log
    a vendor holds, and the rule does not weaken because the transport did."""
    recorded: dict = {}

    class FakeSpan:
        def set_attribute(self, key, value):
            recorded[key] = value

    telemetry._Span(FakeSpan()).set(
        job_id="job_1", filename=SECRET, source_path=SECRET, seconds=2.0
    )

    assert recorded == {"job_id": "job_1", "seconds": 2.0}
    # Not even as "<redacted>": an attribute carrying no information is an
    # invitation to relax the rule to make it useful.
    assert SECRET not in str(recorded)


def test_a_failed_span_records_the_type_and_not_the_message():
    """`record_exception` attaches the message and the stack trace, and a
    step's exception can quote a filename. It is never called."""
    marks: dict = {}

    class FakeSpan:
        def set_attribute(self, key, value):
            marks[key] = value

        def set_status(self, status):
            marks["status"] = str(status)

        def record_exception(self, exc):  # pragma: no cover - must not be called
            raise AssertionError("record_exception carries the message")

    pytest.importorskip("opentelemetry")
    telemetry._Span(FakeSpan()).failed(RuntimeError(f"failed reading {SECRET}"))

    assert marks["error.type"] == "RuntimeError"
    assert SECRET not in str(marks)


def test_configure_is_a_no_op_when_the_exporter_is_none(monkeypatch):
    """The default. Nothing is exported by a process nobody configured."""
    telemetry.reset()

    class S:
        otel_exporter = "none"
        environment = "local"
        otel_endpoint = ""
        otel_sample_ratio = 1.0

    assert telemetry.configure(S()) is False
    assert not telemetry.configured()
