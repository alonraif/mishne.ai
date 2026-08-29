"""Stage 4 — words into beats.

Deterministic. No model runs here, and that is the point: this stage is cheap,
testable, and it hands stage 6 clean structured units instead of a wall of
words. Anything an LLM would have to figure out from scratch that punctuation
and pause length already answer is work not worth paying for.

A **beat** is the smallest unit that can stand alone in a cut — typically one to
four sentences from one speaker.

## Where beats come from depends on what the material is

Pauses are the natural seam in **raw rushes**: a subject finishes an answer and
stops. That assumption was the whole segmentation model, and it fails silently
on material where nobody pauses. A presenter reading to camera over b-roll has a
p90 word gap of 60 ms and one gap over 1200 ms in four minutes; the entire reel
came back as six beats of forty seconds, and a "forty second cut" was then the
first forty seconds verbatim. Every stage downstream was working correctly.

So there are two sources of seam, and which ones exist depends on provenance:

* **Rushes** — a single continuous recording, nobody has cut it yet. Pauses and
  speaker changes are the real boundaries, and they are honoured first, because
  keeping a whole answer intact is what stops the solver selecting a payoff
  without its setup.
* **A sequence** — an AAF or EDL that has already been through an edit. Somebody
  has already made every cut decision in it, and those seams are free ground
  truth. Not all of them are about speech: on a finished segment most cuts are
  b-roll changes with the voiceover running straight through, so a seam only
  becomes a beat boundary when it lands near a speech boundary. On the reference
  Hebrew material 15 of 21 seams qualified; the other 6 were picture cuts.

Under both, sentences are the fallback seam, so a stretch with no pause and no
cut still divides into thoughts rather than arriving as one block.

## The limit of all of this

Every seam above is something the *material* provides, and some material does
not provide enough. A 26-minute English interview, asked for a two-minute cut,
has 55 silences over 600 ms and 17 over a second in its entire length. Sweeping
the pause threshold from 1200 ms down to 600 moves the median beat from 29 s to
21 s and still leaves half the running time in beats over 30 s. There is no
threshold that fixes it, because the seams are not there to find.

Whichever beat the solver then picks, it is picking a 30-second block, and a
two-minute cut is four of them. Cutting *inside* a sentence — which is what an
editor does constantly — is not a tuning problem and cannot be reached from
here: the beat is the atomic unit of selection, so nothing downstream is ever
offered a boundary that structure.py did not produce. Doing it properly means
proposing spans rather than scoring fixed beats, and validating each proposed
boundary against the silence that has to be there for the cut to be audible.

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
#
# Lowered from 1200 when the signal changed. The old value was calibrated
# against *word gaps*, which on Whisper output are almost always zero and
# therefore measured nothing; it is now real silence from the VAD, where a full
# second is unambiguously a beat boundary. The English reference interview has a
# 1124 ms silence after "...symbolise in our culture" — a clean end of answer —
# which the old threshold missed by 76 ms and glued to the next question.
#
# Do not expect much from tuning this. Sweeping 1200 down to 600 on that
# interview moves the median beat from 29 s to 21 s and still leaves half the
# material in beats over 30 s: a speaker who does not pause cannot be segmented
# by pauses at any threshold. See the module docstring.
BEAT_PAUSE_MS = 1000
# Beats longer than this get split at the best internal pause, whatever the
# material. A safety ceiling, not a target.
MAX_BEAT_MS = 45_000
# Soft ceiling when the material carries human seams: past this, close the beat
# at the next sentence. Chosen from the reference material, where it yields
# beats of about nine seconds — one coherent thought each.
SEQUENCE_SOFT_BEAT_MS = 12_000
# How near a speech boundary a human cut must land before it counts as an
# editorial seam rather than a picture change.
SEAM_SPEECH_TOLERANCE_MS = 1_200
# Slack when matching a word boundary against a VAD silence. ASR word times and
# VAD edges are independent estimates and rarely agree to the millisecond.
TOUCH_MS = 200
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
    # Hebrew retake requests are usually a request verb followed by "again"
    # with words in between, not a fixed phrase. Matching only the fixed forms
    # missed "?אפשר להגיד את זה שוב" — the commonest phrasing there is — and let
    # an announced retake into the cut. Still first-pass, written without native
    # review; worth checking against real material.
    "he": re.compile(
        r"((אפשר|יכול|יכולה|בוא|בואי|תן לי|תני לי|אני רוצה|נסיון|ננסה)"
        r"[^.?!]{0,25}(שוב|עוד פעם|מחדש|מהתחלה)"
        r"|עוד פעם אחת|שוב פעם|מהתחלה|נתחיל מחדש|בוא נעשה שוב"
        r"|סליחה[,\s]+(רגע|שוב))", re.I),
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
    # Which asset this beat came from. A project accumulates uploads over weeks
    # and one cut draws on several of them, so a beat is only meaningful
    # alongside the asset it belongs to. Defaulted, and therefore last: every
    # single-asset caller predates it and must keep working untouched.
    asset_id: str = ""
    # --- span provenance ----------------------------------------------------
    # A Beat is also the shape of a *candidate span*: stage 6 proposes narrower
    # versions of a beat that start or end inside a sentence, and they flow
    # through scoring, selection and assembly as ordinary beats. See
    # steps/propose.py.
    #
    # `parent_id` is the beat this was carved from — itself, for an untouched
    # one. `kind` says what was done: "beat" | "trim" | "split".
    parent_id: str = ""
    kind: str = "beat"
    rationale: str = ""
    depends_on: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.parent_id:
            self.parent_id = self.id

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    def overlaps(self, other: "Beat") -> bool:
        """Same source material, in time. Two such spans cannot both be cut."""
        return (self.asset_id == other.asset_id
                and self.start_ms < other.end_ms
                and other.start_ms < self.end_ms)


def _norm_tokens(text: str) -> list[str]:
    return re.findall(r"[\w']+", text.lower())


def _silence_finder(speech: SpeechMap | None):
    """Ask "was there real silence between these two words?".

    **Word gaps are not a silence signal.** Whisper's word timestamps are
    contiguous by construction — each word's end is the next word's start — so
    on a 26-minute English interview the gaps gave 10 pauses over 600 ms while
    the VAD found 55, and 147 over 300 ms. Segmentation built on word gaps is
    therefore reading an artefact of the ASR, not the recording, and on material
    where Whisper also stops emitting punctuation it has nothing left to work
    with: that interview came back as 62 "sentences", one of them 185 seconds.

    So silence comes from the VAD where there is one, and falls back to word
    gaps only when there is not.
    """
    if speech is None or not speech.speech:
        return lambda a, b: max(0, b.start_ms - a.end_ms)

    gaps = speech.silence

    def between(a: Word, b: Word) -> int:
        """How long the silence at this word boundary actually is.

        The **length of the silence**, not its overlap with the boundary. An
        earlier version returned the overlap with a 300 ms window around the
        boundary, which capped every answer at 300 ms and meant no VAD split
        could ever clear the 600 ms threshold — the VAD was wired in and had no
        effect, which is the quietest kind of wrong.
        """
        lo, hi = a.end_ms - TOUCH_MS, b.start_ms + TOUCH_MS
        best = 0
        for s0, s1 in gaps:
            if s0 >= hi:
                break
            if min(hi, s1) > max(lo, s0):
                best = max(best, s1 - s0)
        return best

    return between


def group_sentences(words: list[Word],
                    speech: SpeechMap | None = None) -> list[list[Word]]:
    """Words into sentences, on punctuation or a real pause.

    Pause wins over punctuation. ASR punctuation is a guess; silence in the
    waveform is evidence — see `_silence_finder` for why that has to come from
    the VAD rather than from the word timestamps.
    """
    pause = _silence_finder(speech)
    out: list[list[Word]] = []
    current: list[Word] = []

    for i, w in enumerate(words):
        current.append(w)
        gap = pause(w, words[i + 1]) if i + 1 < len(words) else 0
        if SENTENCE_END.search(w.text) or gap >= SENTENCE_PAUSE_MS:
            out.append(current)
            current = []
    if current:
        out.append(current)
    return out


def editorial_seams(seams: list[int], sentences: list[list[Word]],
                    tolerance_ms: int = SEAM_SPEECH_TOLERANCE_MS) -> list[int]:
    """Which of a sequence's cuts were made about the speech.

    A finished segment cuts picture far more often than it cuts audio: the
    voiceover runs continuously while the pictures change under it. Splitting a
    beat at every one of those would shred the narration into fragments that
    each end mid-thought.

    A cut counts as editorial when it lands within `tolerance_ms` of a sentence
    boundary — that is, where the previous editor was cutting *because* a
    thought ended. On the reference material this keeps 15 of 21 and discards
    six b-roll changes, which is the split you get by ear.

    Returns the qualifying seams, snapped to the sentence boundary they match,
    so a beat never ends a few frames inside the next word.
    """
    if not seams or not sentences:
        return []
    bounds = sorted({s[0].start_ms for s in sentences}
                    | {s[-1].end_ms for s in sentences})
    kept = set()
    for seam in seams:
        nearest = min(bounds, key=lambda b: abs(b - seam))
        if abs(nearest - seam) <= tolerance_ms:
            kept.add(nearest)
    return sorted(kept)


def group_beats(sentences: list[list[Word]], seams: list[int] | None = None,
                soft_ms: int | None = None,
                speech: SpeechMap | None = None) -> list[list[Word]]:
    """Sentences into beats.

    Hard boundaries — a speaker change, a real pause, or a cut a person already
    made — always end a beat. `soft_ms` then caps how long a beat may run
    without one: past it, the beat closes at the next sentence rather than
    growing until the safety ceiling. Left unset for rushes, where a long answer
    should stay whole.
    """
    seam_set = set(seams or ())
    out: list[list[Word]] = []
    current: list[Word] = []

    for sent in sentences:
        if not sent:
            continue
        if current:
            gap = sent[0].start_ms - current[-1].end_ms
            speaker_changed = sent[0].speaker != current[-1].speaker
            span = sent[-1].end_ms - current[0].start_ms
            # A seam between the two sentences — matched on either side, since
            # it was snapped to whichever boundary it was nearest.
            at_seam = bool(seam_set & {current[-1].end_ms, sent[0].start_ms})
            over_soft = soft_ms is not None and span > soft_ms
            if (gap >= BEAT_PAUSE_MS or speaker_changed or at_seam
                    or over_soft or span > MAX_BEAT_MS):
                out.append(current)
                current = []
        current.extend(sent)

    if current:
        out.append(current)
    return [g for group in out for g in _split_overlong(group, speech)]


def _split_overlong(group: list[Word],
                    speech: SpeechMap | None = None) -> list[list[Word]]:
    """Break a beat past the ceiling at the best real pause inside it.

    Two ways this went wrong before, both found on a 26-minute English
    interview, and both worth stating because they look like the same bug and
    are not:

    1. **Splitting on word gaps.** Whisper's timestamps are contiguous, so every
       gap inside a long sentence was exactly zero. See `_silence_finder`.
    2. **Breaking ties by position.** With every candidate scoring zero, picking
       the maximum of `(gap, index)` selected the *last* viable index — shaving
       a second off the end and recursing on the rest. A 185-second sentence
       came out as 118 pieces, 114 of them under two seconds.

    So: score candidates by the real silence at that point, break ties toward
    the middle, and when the speech genuinely has no seam, split down the middle
    rather than at an arbitrary word. A balanced split of gapless speech is
    admittedly artificial — but it is a boundary an editor can move, where a
    one-second fragment is nothing at all.
    """
    if len(group) < 2 or group[-1].end_ms - group[0].start_ms <= MAX_BEAT_MS:
        return [group]

    pause = _silence_finder(speech)
    mid = (group[0].start_ms + group[-1].end_ms) / 2
    viable = [(pause(a, b), i + 1)
              for i, (a, b) in enumerate(zip(group, group[1:]))
              if a.end_ms - group[0].start_ms >= MIN_BEAT_MS
              and group[-1].end_ms - b.start_ms >= MIN_BEAT_MS]
    if not viable:
        return [group]

    # Biggest pause wins; among equals, the one nearest the middle.
    _, at = max(viable, key=lambda gi: (gi[0], -abs(group[gi[1]].start_ms - mid)))
    return (_split_overlong(group[:at], speech)
            + _split_overlong(group[at:], speech))


def detect_flags(beats: list[Beat], language: str = "en",
                 loudness_lufs: float | None = None) -> list[str]:
    """Attach flags in place.

    Retake detection is the highest-value item here and the least obvious. In
    raw interview and presenter material the same line is commonly delivered
    three or four times; automatically preferring the last clean take is one of
    the most useful things the system does, and it is entirely deterministic.

    Similarity uses token-sequence matching rather than embeddings. It catches
    near-verbatim redelivery, which is the actual case, and costs nothing. A
    paraphrased second attempt will slip through — that is stage 6's redundancy
    clustering to catch, not this.

    Returns track-level warnings — conditions that describe the recording rather
    than any individual beat.
    """
    warnings: list[str] = []
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

        b.flags[:] = sorted(set(b.flags))

    # Off-mic is a property of a *beat* — one speaker turned away, or on the
    # wrong microphone — and absolute track loudness cannot detect it. A quiet
    # track is a gain problem, and flagging every beat off-mic because of it
    # disqualified the entire transcript and produced an empty cut. That is
    # what this used to do.
    #
    # Detecting it properly needs per-beat levels relative to the track's own
    # speech median, which is the same normalisation the speaker attribution
    # does. Until that is wired through, say something true about the track
    # instead of something false about every beat.
    if loudness_lufs is not None and loudness_lufs < -40:
        warnings.append(
            f"Audio is very quiet ({loudness_lufs:.1f} LUFS). Check the gain — "
            f"transcription and cut-point detection both degrade below about "
            f"-35 LUFS."
        )
    return warnings


def build(words: list[Word], speech: SpeechMap | None = None,
          language: str = "en", speaker_default: str = "SPK",
          loudness_lufs: float | None = None,
          asset_id: str = "", seams: list[int] | None = None) -> list[Beat]:
    """Words in, beats out.

    `seams` are cuts a person already made in this material, in milliseconds —
    empty or omitted for raw rushes. Supplying them switches on the tighter
    segmentation described at the top of this module: the ones that fall on a
    speech boundary become beat boundaries, and beats are capped at a soft
    ceiling instead of running to the safety limit.
    """
    words = [w for w in words if w.text.strip()]
    if not words:
        return []

    for w in words:
        if not w.speaker:
            w.speaker = speaker_default

    sentences = group_sentences(words, speech)
    kept_seams = editorial_seams(list(seams or ()), sentences)
    # The soft cap belongs with the seams. Material somebody has already cut is
    # being re-cut, and the editorial unit is small; raw rushes keep whole
    # answers, and splitting one into halves the solver can pick separately is
    # how a payoff arrives without its setup.
    soft = SEQUENCE_SOFT_BEAT_MS if seams else None

    beats: list[Beat] = []
    for idx, group in enumerate(group_beats(sentences, kept_seams, soft, speech)):
        text = " ".join(w.text for w in group).strip()
        text = re.sub(r"\s+([,.!?;:])", r"\1", text)
        confs = [w.confidence for w in group if w.confidence is not None]
        beats.append(Beat(
            id=f"{asset_id + '_' if asset_id else ''}beat_{idx:04d}",
            idx=idx,
            asset_id=asset_id,
            speaker=group[0].speaker,
            start_ms=group[0].start_ms,
            end_ms=group[-1].end_ms,
            text=text,
            words=group,
            mean_confidence=sum(confs) / len(confs) if confs else 1.0,
        ))

    warnings = detect_flags(beats, language=language,
                            loudness_lufs=loudness_lufs)
    if seams:
        dropped = len(seams) - len(kept_seams)
        warnings.append(
            f"already-cut material — {len(kept_seams)} of {len(seams)} existing "
            f"cuts used as beat boundaries"
            + (f" ({dropped} were picture-only)" if dropped else ""))
    build.warnings = warnings   # read by the caller for reporting
    build.seams_used = kept_seams
    return beats
