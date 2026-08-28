# ADR-0003 — Managed ASR behind a provider interface

**Status:** Accepted · **Date:** 2026-08-28

## Context

Transcription is the input to everything. Requirements: word-level timestamps, tight
timestamp boundary accuracy, speaker diarization, multilingual including Hebrew, and
preserved disfluencies.

Self-hosting Whisper or similar would require a GPU fleet. The current constraint is
no GPU.

## Decision

Use a **managed ASR API** behind a narrow provider interface. No GPU fleet.

```python
class ASRProvider(Protocol):
    def transcribe(
        self, audio_uri: str, *, language: str | None,
        diarize: bool, preserve_disfluencies: bool,
    ) -> Transcript: ...
```

`Transcript` is a provider-neutral structure of words with start, end, confidence and
speaker. The raw vendor response is persisted verbatim to object storage regardless.

## Rationale

- No GPU fleet to provision, scale, or keep busy. Batch ASR at roughly $0.10–0.25 per
  hour is cheaper than idle GPUs at any plausible early volume.
- Batch, not streaming — accuracy is better and cost is roughly half. Nothing here is
  real-time.
- Leading providers cluster within a few points of word error rate on clean English;
  differentiation is in hard audio, languages, and boundary precision.

**Selection criteria, in priority order:**

1. **Timestamp boundary precision** — more important than word error rate. A provider
   at 3% WER with sloppy word boundaries produces worse cuts than one at 5% with tight
   ones. No vendor publishes this; it must be measured directly.
2. Disfluency preservation. Most APIs default to "smart formatting" that silently
   drops filler words. This must be disableable — removing filler is mishne.ai's job,
   and it cannot do it without knowing where they are.
3. Diarization quality.
4. Hebrew and other target-language quality, measured rather than assumed.
5. Contractual zero retention and no training on customer data.
6. Data residency options.

## Consequences

**Positive** — no GPU operations; low fixed cost; provider swappable as the market
moves.

**Negative — and it is significant** — customer audio leaves the platform. This is
exactly the content the security model promises to protect, so vendor selection is a
security decision as much as a quality one. Contractual zero-retention terms are a
hard prerequisite; see [04 — Security](../architecture/04-security.md).

**Escape hatch** — some broadcasters will refuse third-party ASR. The interface makes
self-hosted Whisper on GPU a configuration change and a deployment, not a rewrite.
That scenario is the reason the interface exists.
