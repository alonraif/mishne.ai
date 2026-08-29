"""Stage 5 — compile the edit brief.

Free-text director's notes into a structured `EditBrief`.

Real notes are underspecified. "Make it punchy, about ten minutes" is a typical
brief and contains one usable number. The compiler's job is therefore not
extraction so much as **applying documented defaults and saying which ones it
applied** — every assumption lands in `clarifications`, which the operator sees
before the job runs. An assumption the user can correct is fine; a silent one is
not.

Two implementations behind one call, same pattern as the scorer: a deterministic
compiler that works offline, and an LLM compiler for the nuance. The
deterministic one is not a toy — target duration, must-include and must-exclude
are the fields that actually change the output, and they are stated plainly in
most briefs.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field

NARRATIVE_SHAPES = ("chronological", "thematic", "inverted_pyramid", "q_and_a")

TONE_WORDS = {
    "punchy", "tight", "urgent", "warm", "reflective", "conversational",
    "authoritative", "sombre", "somber", "upbeat", "intimate", "energetic",
}

# "10 minutes", "10m", "1:30", "90 seconds"
DURATION_PATTERNS = [
    (re.compile(r"\b(\d+)\s*(?:minutes?|mins?|m)\b", re.I), 60),
    (re.compile(r"\b(\d+)\s*(?:seconds?|secs?|s)\b", re.I), 1),
]
CLOCK = re.compile(r"\b(\d{1,2}):(\d{2})\b")

# Number words. People writing a brief type "ten minutes" at least as often as
# "10 minutes" — it is a note to a colleague, not a form field. Missing these
# silently defaults the whole job to ten minutes, which is the single most
# consequential value in the brief.
NUMBER_WORDS = {
    "half": 0.5, "one": 1, "a": 1, "an": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "ninety": 90,
}
WORD_DURATION = re.compile(
    r"\b(" + "|".join(NUMBER_WORDS) + r")(?:[\s-]+(five|one|two|three|four|"
    r"six|seven|eight|nine))?\s*(minutes?|mins?|seconds?|secs?)\b", re.I)


@dataclass
class EditBrief:
    target_duration_s: int
    duration_tolerance_s: int = 30
    tone: list[str] = field(default_factory=list)
    narrative_shape: str = "chronological"
    must_include: list[str] = field(default_factory=list)
    must_exclude: list[str] = field(default_factory=list)
    speaker_priority: list[str] = field(default_factory=list)
    pacing: str = "tight"
    keep_filler: bool = False
    handle_frames: int = 6
    language: str = "en"
    clarifications: list[str] = field(default_factory=list)
    notes_raw: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def parse_duration(text: str) -> int | None:
    """Seconds from '10 minutes', '90 seconds', '1:30' or 'ten minutes'."""
    m = CLOCK.search(text)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))

    for pattern, multiplier in DURATION_PATTERNS:
        hit = pattern.search(text)
        if hit:
            return int(hit.group(1)) * multiplier

    hit = WORD_DURATION.search(text)
    if hit:
        value = NUMBER_WORDS[hit.group(1).lower()]
        if hit.group(2):                       # "twenty-five minutes"
            value += NUMBER_WORDS[hit.group(2).lower()]
        unit = 60 if hit.group(3).lower().startswith(("min", "m")) else 1
        return int(value * unit)
    return None


def compile_deterministic(notes: str, target_duration_s: int | None = None,
                          **overrides) -> EditBrief:
    """Compile without a model. Every default applied is recorded."""
    clarifications: list[str] = []

    duration = target_duration_s or parse_duration(notes)
    if duration is None:
        duration = 600
        clarifications.append(
            "No target length given — assumed 10 minutes. Pass --target to set it."
        )
    elif target_duration_s is None:
        clarifications.append(
            f"Read a target of {duration // 60}m {duration % 60}s from the notes."
        )

    lower = notes.lower()
    tone = sorted({w for w in TONE_WORDS if re.search(rf"\b{w}\b", lower)})

    pacing = "tight" if re.search(r"\b(tight|punchy|fast|snappy)\b", lower) else \
             "breathing" if re.search(r"\b(breath|slow|gentle|reflective)\b", lower) \
             else "tight"

    shape = "chronological"
    for candidate, pattern in (
        ("inverted_pyramid", r"\b(lead with|strongest first|inverted|top.?line)\b"),
        ("q_and_a", r"\b(q\s*(&|and)\s*a|question and answer|interview format)\b"),
        ("thematic", r"\b(thematic|by theme|group(ed)? by topic)\b"),
    ):
        if re.search(pattern, lower):
            shape = candidate
            break
    if shape == "chronological" and "narrative_shape" not in overrides:
        clarifications.append(
            "No structure specified — keeping source order (chronological)."
        )

    tolerance = max(10, int(duration * 0.05))
    if pacing == "tight":
        clarifications.append(
            f'Notes read as "tight" — using a ±{tolerance}s tolerance.'
        )

    brief = EditBrief(
        target_duration_s=duration,
        duration_tolerance_s=tolerance,
        tone=tone,
        narrative_shape=shape,
        pacing=pacing,
        clarifications=clarifications,
        notes_raw=notes,
    )
    for k, v in overrides.items():
        if v is not None and hasattr(brief, k):
            setattr(brief, k, v)
    return brief


SYSTEM = (
    "You turn a video editor's production notes into a structured brief for an "
    "automated rough-cut system.\n\n"
    "Notes are usually underspecified. Do not invent intent. Where the notes are "
    "silent, apply a sensible default AND record it in `clarifications` in plain "
    "language, addressed to the editor — they will read these before the job "
    "runs and correct you.\n\n"
    "`must_include` and `must_exclude` are short topic phrases as the editor "
    "would say them, not quotes from the transcript. Only fill them when the "
    "notes actually state a requirement."
)


def compile_with_llm(notes: str, target_duration_s: int | None = None,
                     model: str = "claude-sonnet-4-5", **overrides) -> EditBrief:
    """Compile with Claude. Falls back to the deterministic compiler on failure.

    The fallback is not defensive padding: a brief is cheap to compile badly and
    expensive to fail on, and a job that refuses to start because a vendor was
    briefly unavailable is worse than one that runs with documented defaults.
    """
    import json

    import anthropic

    base = compile_deterministic(notes, target_duration_s, **overrides)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        base.clarifications.append(
            "Compiled without a language model (no ANTHROPIC_API_KEY) — "
            "structure and tone come from keyword matching only."
        )
        return base

    try:
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=model, max_tokens=1500, system=SYSTEM,
            messages=[{"role": "user", "content": (
                f"Production notes:\n\"\"\"\n{notes}\n\"\"\"\n\n"
                + (f"Target length: {target_duration_s} seconds.\n\n"
                   if target_duration_s else "")
                + "Return ONLY JSON with keys: target_duration_s, "
                  "duration_tolerance_s, tone (array), narrative_shape (one of "
                  f"{list(NARRATIVE_SHAPES)}), must_include (array), "
                  "must_exclude (array), speaker_priority (array), pacing "
                  "('tight'|'breathing'), keep_filler (bool), clarifications "
                  "(array of strings)."
            )}],
        )
        text = re.sub(r"^```(?:json)?|```$", "", msg.content[0].text.strip(),
                      flags=re.M).strip()
        data = json.loads(text)
    except Exception as exc:  # noqa: BLE001
        base.clarifications.append(
            f"Language-model brief compilation failed ({type(exc).__name__}); "
            "fell back to keyword matching."
        )
        return base

    for key in ("target_duration_s", "duration_tolerance_s", "tone",
                "narrative_shape", "must_include", "must_exclude",
                "speaker_priority", "pacing", "keep_filler", "clarifications"):
        if key in data and data[key] is not None:
            setattr(base, key, data[key])
    if target_duration_s:
        base.target_duration_s = target_duration_s
    for k, v in overrides.items():
        if v is not None and hasattr(base, k):
            setattr(base, k, v)
    return base


def compile_brief(notes: str, target_duration_s: int | None = None,
                  use_llm: bool = True, **overrides) -> EditBrief:
    if use_llm:
        return compile_with_llm(notes, target_duration_s, **overrides)
    return compile_deterministic(notes, target_duration_s, **overrides)
