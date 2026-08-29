"""Speaker attribution.

Two problems get confused constantly, so state them separately:

**Diarization** — who spoke when. Solvable, not free. For 2-3 person close-mic
interviews expect 8-15% DER; overlapping speech pushes it to 20-35%, and
interviews have a lot of crosstalk.

**Identification** — attaching a *name*. Diarization does not do this at all. It
returns `Speaker_00`, `Speaker_01`. No model looks at rushes and knows it is
Margret. Names come from a person, always.

## The multi-track shortcut

Professional shoots put each subject on their own lavalier, recorded to its own
track. Stage 1 already extracts audio per track rather than from a mix, so for
that material attribution collapses into arithmetic: whoever is loudest on
track 2 is the person wearing mic 2.

That is not a worse approximation of diarization — it is *better than any model*,
because it uses physical information no model has. No DER, no crosstalk
confusion, no GPU. The hard case only remains for single-track material.

## Why normalisation is the whole trick

Mic gains differ, often by a lot. Compare raw energy and the hottest track wins
every word, silently and confidently. Each track is normalised by its own speech
reference level first, so the question becomes "which mic is closest to whoever
is talking" rather than "which mic is loudest".

## Why a margin is required

Every mic hears every voice; the others are just quieter. A word is attributed
only when the winning track leads the runner-up by a clear margin. Inside that
margin the honest answer is "two people at once", flagged rather than guessed —
a confidently wrong speaker label produces a confidently wrong cut, and in a
broadcast piece a misattributed quote is a serious error.

## Known limitation

A track that never carries its owner's voice — somebody mic'd who never speaks,
or a channel picking up only bleed — has no real speech to set a reference from.
Its bleed becomes its own reference, normalises to 1.0, and ties with whoever is
actually talking. There is no principled fix without an absolute reference:
"quiet speaker" and "bleed-only channel" look identical.

The margin rule means this surfaces as flagged crosstalk rather than a silently
wrong label, which is the right failure mode. Revisit with real multi-track
material; guarding against it now would be tuning against a case nobody has
measured. See tests/test_speakers.py.
"""

from __future__ import annotations

import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ...asr import Word

HOP_MS = 20
# Winning track must exceed the runner-up by this factor (~3.5 dB).
MARGIN = 1.5
# Below this fraction of a track's own reference level, nobody is on that mic.
SILENCE_FLOOR = 0.08


@dataclass
class Speaker:
    """A distinct voice, before anyone has given it a name."""

    id: str
    source: str            # "track" | "diarization"
    default_label: str     # shown until a human renames it
    track_index: int | None = None
    word_count: int = 0
    speech_ms: int = 0
    confirmed: bool = False
    label: str = ""

    @property
    def display(self) -> str:
        return self.label or self.default_label

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "defaultLabel": self.default_label,
            "label": self.label,
            "confirmed": self.confirmed,
            "trackIndex": self.track_index,
            "wordCount": self.word_count,
            "speechMs": self.speech_ms,
        }


@dataclass
class Attribution:
    speakers: list[Speaker]
    crosstalk_words: int = 0
    unattributed_words: int = 0
    reliable: bool = True
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "speakers": [s.to_dict() for s in self.speakers],
            "crosstalkWords": self.crosstalk_words,
            "unattributedWords": self.unattributed_words,
            "reliable": self.reliable,
            "notes": self.notes,
        }


def track_envelope(path: Path, hop_ms: int = HOP_MS) -> np.ndarray:
    """RMS energy per hop. I/O only — attribution itself is pure and testable."""
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        raw = w.readframes(w.getnframes())
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    hop = max(1, int(sr * hop_ms / 1000))
    n = len(samples) // hop
    if n == 0:
        return np.zeros(1, dtype=np.float32)
    return np.sqrt((samples[: n * hop].reshape(n, hop) ** 2).mean(axis=1) + 1e-12)


def reference_level(env: np.ndarray) -> float:
    """A track's own speech level.

    The 85th percentile of active frames, not the mean or the peak: the mean is
    dragged down by silence, which is most of any track, and the peak is set by
    whatever the loudest cough was.
    """
    if env.max() <= 0:
        return 1.0
    active = env[env > env.max() * 0.05]
    return float(np.percentile(active, 85)) if len(active) else 1.0


def attribute(words: list[Word], envelopes: dict[int, np.ndarray],
              hop_ms: int = HOP_MS, margin: float = MARGIN) -> Attribution:
    """Assign a speaker to every word from per-track energy. Mutates `words`."""
    if not envelopes:
        return Attribution(speakers=[], reliable=False, notes=["no audio tracks"])

    refs = {i: reference_level(e) for i, e in envelopes.items()}
    speakers = {
        idx: Speaker(id=f"T{idx}", source="track",
                     default_label=f"Mic {n + 1}", track_index=idx)
        for n, idx in enumerate(sorted(envelopes))
    }
    result = Attribution(speakers=list(speakers.values()))

    if len(envelopes) == 1:
        # Energy cannot separate anyone on one track. Saying so beats inventing
        # speakers that do not exist.
        only = next(iter(speakers.values()))
        only.default_label = "Speaker 1"
        for w in words:
            w.speaker = only.id
            only.word_count += 1
            only.speech_ms += w.duration_ms
        result.notes.append(
            "Single audio track — every word attributed to one speaker. "
            "Multi-speaker material needs diarization."
        )
        return result

    for w in words:
        lo = max(0, int(w.start_ms / hop_ms))
        hi = max(lo + 1, int(w.end_ms / hop_ms))

        scores = {}
        for idx, env in envelopes.items():
            window = env[lo:hi]
            scores[idx] = (float(window.mean()) / (refs[idx] or 1.0)
                           if len(window) else 0.0)

        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        best_idx, best = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else 0.0

        if best < SILENCE_FLOOR:
            w.speaker = ""
            result.unattributed_words += 1
            continue

        if second > 0 and (best / second) < margin:
            result.crosstalk_words += 1

        w.speaker = speakers[best_idx].id
        speakers[best_idx].word_count += 1
        speakers[best_idx].speech_ms += w.duration_ms

    # A track nobody ever won is a room mic, a spare, or a dead channel. Drop it
    # rather than offering the user an empty speaker to name.
    result.speakers = [s for s in speakers.values() if s.word_count > 0]

    if result.crosstalk_words:
        pct = 100 * result.crosstalk_words / max(1, len(words))
        result.notes.append(
            f"{result.crosstalk_words} words ({pct:.0f}%) had two mics at "
            f"similar levels — attributed to the louder one."
        )
        if pct > 25:
            result.reliable = False
            result.notes.append(
                "High crosstalk. Speaker attribution on this material is "
                "unreliable; check the labels before trusting the cut."
            )
    return result


def attribute_from_files(words: list[Word], track_paths: dict[int, Path],
                         hop_ms: int = HOP_MS) -> Attribution:
    envelopes = {i: track_envelope(p, hop_ms) for i, p in track_paths.items()}
    return attribute(words, envelopes, hop_ms=hop_ms)


def attribute_from_diarization(words: list[Word], result) -> Attribution:
    """Attribution for single-track material, from a diarization result.

    The multi-track path above is arithmetic and always right: each person has a
    microphone, and the loudest one is whoever is talking. One track has none of
    that, and until now the answer was a single speaker marked reliable — which
    on a three-way conversation put every line in one mouth and said so
    confidently. This is the honest replacement.

    What comes back is still *separation*, never identity: "three distinct
    voices", not who they are. Naming stays a thing a person does, and
    `confirmed` stays False until they do it.
    """
    speakers: dict[str, Speaker] = {}
    unassigned = 0

    for w in words:
        sid = result.speaker_at(w.start_ms, w.end_ms)
        if not sid:
            unassigned += 1
            w.speaker = w.speaker or "SPK"
            continue
        sp = speakers.get(sid)
        if sp is None:
            sp = speakers[sid] = Speaker(
                id=sid, source="diarization",
                default_label=f"Speaker {len(speakers) + 1}")
        w.speaker = sid
        sp.word_count += 1
        sp.speech_ms += w.duration_ms

    att = Attribution(
        speakers=sorted(speakers.values(), key=lambda s: -s.speech_ms),
        unattributed_words=unassigned,
        reliable=result.reliable,
        notes=list(result.notes),
    )
    if unassigned:
        pct = 100 * unassigned / max(1, len(words))
        att.notes.append(
            f"{unassigned} words ({pct:.0f}%) fell outside every detected turn "
            f"and carry no speaker.")
        if pct > 20:
            att.reliable = False
    return att


def single_track_unseparated(words: list[Word]) -> Attribution:
    """One track and no diarizer: say so, rather than inventing a speaker.

    The previous behaviour reported one speaker with `reliable=True`. On
    material with three people in it that is not a simplification, it is a false
    statement the UI then renders as fact next to every line.
    """
    for w in words:
        w.speaker = w.speaker or "SPK"
    return Attribution(
        speakers=[],
        reliable=False,
        notes=["Single audio track and no diarization — voices were never "
               "separated. Every line is unattributed; the speaker legend has "
               "nothing to show."],
    )
