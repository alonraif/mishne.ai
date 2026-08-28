"""Stage 4 — words into beats.

Deterministic. No model runs here, and that is the point: this stage is cheap,
testable, and it hands stage 6 clean structured units instead of a wall of
words. Anything an LLM would have to figure out from scratch that punctuation
and pause length already answer is work not worth paying for.

A **beat** is the smallest unit that can stand alone in a cut — typically one to
four sentences from one speaker.

Flags are attached, never applied. Nothing is deleted here. Removing filler is a
selection decision made later with the brief in hand; a beat flagged `filler`
may still be the only place a fact is stated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from ...asr import Word
from .vad import SpeechMap

# Pause long enough to end a sentence even without punctuation.
SENTENCE_PAUSE_MS = 600
# Pause long enough to start a new beat.
BEAT_PAUSE_MS = 1200
# Beats longer than this get split at the best internal pause.
MAX_BEAT_MS = 45_000
MIN_BEAT_MS = 800

FILLER_LEXICON = {
    "en": {"um", "uh", "erm", "ah", "hmm", "mm", "like", "y'know", "you know",
           "i mean", "sort of", "kind of", "basically", "right", "so"},
    # Hebrew fillers. Untested against real material — see the ASR benchmark.
    "he": {"אה", "אמ", "כאילו", "יעני", "זהו", "בעצם"},
}

SENTENCE_END = re.compile(r"[.!?…]+[\"')\]]*$")

# Phrases where the speaker announces a retake. These are coherent sentences, so
# the repeated-prefix rule never catches them, and they are extremely common in
# raw interview and presenter footage. Flagged as `retake_signal` — the beat
# itself is almost never wanted, and its presence is strong evidence that the
# beat before it was a discarded take.
RETAKE_SIGNAL = {
    "en": re.compile(
        r"\b("
        r"(can|could|shall|let) (i|me|us|we) (say|do|try|start|go)( that| it| again)"
        r"|start(ing)? (that |it )?(over|again)"
        r"|(one|once) more time"
        r"|(take|try) (that|it|this) again"
        r"|from the top"
        r"|scratch that"
        r"|sorry,? again"
        r")\b", re.I),
    "he": re.compile(r"(עוד פעם|שוב פעם|מהתחלה|בוא נעשה שוב)", re.I),
}


@dataclass
class Beat:
    id: str
    idx: int
    speaker: str
    start_ms: int
    end_ms: int
    text: str
    words: list[Word] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    mean_confidence: float = 1.0

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


def _norm_tokens(text: str) -> list[str]:
    return re.findall(r"[\w']+", text.lower())


def group_sentences(words: list[Word]) -> list[list[Word]]:
    """Words into sentences, on punctuation or a long enough pause.

    Pause wins over punctuation. ASR punctuation is a guess; a 600 ms gap is
    evidence.
    """
    out: list[list[Word]] = []
    current: list[Word] = []

    for i, w in enumerate(words):
        current.append(w)
        gap = (words[i + 1].start_ms - w.end_ms) if i + 1 < len(words) else 0
        if SENTENCE_END.search(w.text) or gap >= SENTENCE_PAUSE_MS:
            out.append(current)
            current = []
    if current:
        out.append(current)
    return out


def group_beats(sentences: list[list[Word]]) -> list[list[Word]]:
    """Sentences into beats, splitting on speaker change or a long pause."""
    out: list[list[Word]] = []
    current: list[Word] = []

    for i, sent in enumerate(sentences):
        if not sent:
            continue
        if current:
            gap = sent[0].start_ms - current[-1].end_ms
            speaker_changed = sent[0].speaker != current[-1].speaker
            too_long = (sent[-1].end_ms - current[0].start_ms) > MAX_BEAT_MS
            if gap >= BEAT_PAUSE_MS or speaker_changed or too_long:
                out.append(current)
                current = []
        current.extend(sent)

    if current:
        out.append(current)
    return out


def detect_flags(beats: list[Beat], language: str = "en",
                 loudness_lufs: float | None = None) -> None:
    """Attach flags in place.

    Retake detection is the highest-value item here and the least obvious. In
    raw interview and presenter material the same line is commonly delivered
    three or four times; automatically preferring the last clean take is one of
    the most useful things the system does, and it is entirely deterministic.

    Similarity uses token-sequence matching rather than embeddings. It catches
    near-verbatim redelivery, which is the actual case, and costs nothing. A
    paraphrased second attempt will slip through — that is stage 6's redundancy
    clustering to catch, not this.
    """
    filler_words = FILLER_LEXICON.get(language, FILLER_LEXICON["en"])

    for i, b in enumerate(beats):
        tokens = _norm_tokens(b.text)
        if not tokens:
            b.flags.append("empty")
            continue

        # Filler: a beat that is mostly filler, or a very short pure-filler beat.
        filler_hits = sum(1 for t in tokens if t in filler_words)
        if tokens and (filler_hits / len(tokens)) > 0.4:
            b.flags.append("filler")
        elif len(tokens) <= 3 and filler_hits:
            b.flags.append("filler")

        # False start: the beat repeats its own opening, or is a stub.
        if len(tokens) >= 4:
            for n in (3, 4, 5):
                if len(tokens) >= 2 * n and tokens[:n] == tokens[n:2 * n]:
                    b.flags.append("false_start")
                    break
        if len(tokens) <= 2 and b.duration_ms < MIN_BEAT_MS:
            b.flags.append("false_start")

        signal = RETAKE_SIGNAL.get(language, RETAKE_SIGNAL["en"])
        if signal.search(b.text):
            b.flags.append("retake_signal")
            # The beat before an announced retake is the take being abandoned.
            if i > 0 and beats[i - 1].speaker == b.speaker:
                if "superseded" not in beats[i - 1].flags:
                    beats[i - 1].flags.append("superseded")

        if b.mean_confidence < 0.55:
            b.flags.append("low_confidence")

        # Retake: near-verbatim repeat of a nearby beat by the same speaker.
        for prev in beats[max(0, i - 4):i]:
            if prev.speaker != b.speaker:
                continue
            prev_tokens = _norm_tokens(prev.text)
            if len(prev_tokens) < 4 or len(tokens) < 4:
                continue
            ratio = SequenceMatcher(None, prev_tokens, tokens).ratio()
            if ratio > 0.75:
                b.flags.append("retake")
                # The earlier delivery is the superseded one.
                if "superseded" not in prev.flags:
                    prev.flags.append("superseded")
                break

        if loudness_lufs is not None and loudness_lufs < -40:
            b.flags.append("off_mic")

        b.flags[:] = sorted(set(b.flags))


def build(words: list[Word], speech: SpeechMap | None = None,
          language: str = "en", speaker_default: str = "SPK",
          loudness_lufs: float | None = None) -> list[Beat]:
    """Words in, beats out."""
    words = [w for w in words if w.text.strip()]
    if not words:
        return []

    for w in words:
        if not w.speaker:
            w.speaker = speaker_default

    beats: list[Beat] = []
    for idx, group in enumerate(group_beats(group_sentences(words))):
        text = " ".join(w.text for w in group).strip()
        text = re.sub(r"\s+([,.!?;:])", r"\1", text)
        confs = [w.confidence for w in group if w.confidence is not None]
        beats.append(Beat(
            id=f"beat_{idx:04d}",
            idx=idx,
            speaker=group[0].speaker,
            start_ms=group[0].start_ms,
            end_ms=group[-1].end_ms,
            text=text,
            words=group,
            mean_confidence=sum(confs) / len(confs) if confs else 1.0,
        ))

    detect_flags(beats, language=language, loudness_lufs=loudness_lufs)
    return beats
