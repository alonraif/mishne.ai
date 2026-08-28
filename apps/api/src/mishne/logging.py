"""Structured logging.

Hard rule: no customer content in logs. No transcript text, no filenames, no
brief text. IDs, durations, counts and status only. See
docs/architecture/04-security.md.

`scrub` is the enforcement point. Do not route around it.
"""

import structlog

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
}


def scrub(_logger, _method, event_dict: dict) -> dict:
    for key in list(event_dict):
        if key.lower() in BLOCKED_KEYS:
            event_dict[key] = "<redacted>"
    return event_dict


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
