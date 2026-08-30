"""Structured logging, and the one place customer content is stopped.

Hard rule: no customer content in logs. No transcript text, no filenames, no
brief text. IDs, durations, counts and status only. See
docs/architecture/04-security.md.

`scrub` is the enforcement point. Do not route around it — and note that it is
now used by `telemetry.py` for span attributes as well, because a trace is a log
that a vendor holds, and the rule does not get weaker because the transport
changed.

## Why blocking by key is not enough on its own, and what was done about it

The original list was ten exact key names. That works for the keys somebody
thought of and fails silently for every one added afterwards: a new
`speaker_name` field carrying what an editor typed passes straight through, and
nothing tells you. The failure is invisible by construction, which is the worst
property a safeguard can have.

So there are two rules now, and the second is the one that holds:

* **Exact keys**, as before — the specific fields known to carry content.
* **Suffixes.** A key ending in `_text`, `_path`, `_filename`, `_url`,
  `_prompt` or `_content` is redacted whatever its prefix. New fields tend to
  be named after what they hold, so this catches the ones nobody added to the
  list.

Suffixes are deliberately narrow. `_name` is not among them: `step_name` and
`provider_name` are ours and redacting them would turn the telemetry into rows
of `<redacted>`, which is how a safeguard gets switched off by the person it
inconveniences.

Nesting matters too. `scrub` used to look only at the top level, so
`log.info("step.failed", context={"filename": ...})` was not redacted at all.
It now walks dicts and lists.
"""

import structlog

#: Exact key names known to carry customer content.
BLOCKED_KEYS = {
    "filename",
    "text",
    "transcript",
    "notes",
    "notes_raw",
    "brief",
    "rationale",
    "s3_key",
    "path",
    "email",
    # The prompt and the answer. A model call's inputs are the transcript and
    # the editor's brief, and its output quotes both back.
    "prompt",
    "completion",
    "system",
    "user",
    "content",
    "body",
    "message",
    "response",
    # The transcript page's heading is the customer's own filename — the one
    # place a filename legitimately appears in *output*, and never in telemetry.
    "title",
    # What an editor named a speaker. Usually a real person.
    "speaker",
    "stem",
    "media",
    "url",
}

#: A key ending in one of these is redacted whatever its prefix. This is the
#: half that covers fields nobody has written yet.
BLOCKED_SUFFIXES = (
    "_text",
    "_path",
    "_filename",
    "_url",
    "_prompt",
    "_content",
)

REDACTED = "<redacted>"


def blocked(key: str) -> bool:
    """Whether a field name may not carry a value into telemetry."""
    lowered = key.lower()
    return lowered in BLOCKED_KEYS or lowered.endswith(BLOCKED_SUFFIXES)


def clean(value):
    """A value with every blocked field inside it redacted, at any depth."""
    if isinstance(value, dict):
        return {
            k: REDACTED if blocked(str(k)) else clean(v) for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


def scrub(_logger, _method, event_dict: dict) -> dict:
    return clean(event_dict)


def configure() -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            scrub,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )


def get_logger(name: str):
    return structlog.get_logger(name)
