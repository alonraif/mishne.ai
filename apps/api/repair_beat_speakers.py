#!/usr/bin/env python
"""Put the right speaker, and an honest talk time, back on what a broken run wrote.

## What went wrong

The orchestrator ran `structure` before `speakers`. Attribution rewrites
`Word.speaker` in place and `structure.build` snapshots the first word's
speaker onto the beat, so every beat the worker cached kept the label the ASR
vendor returned — `c0:spk:0` from a chunked Gemini call — while the speaker
legend offered `T0`/`T1` from the microphones. Two id spaces: a raw vendor id on
every line, one colour for everybody, and a speaker filter that matched nothing.

## Why nothing has to be re-run

A beat holds *references* to its words (`structure.Beat.words`), and attribution
mutated those same objects a moment later. So the cached `ingest.json` already
carries the correct attribution on the words — only the beat's own `speaker`
column is stale, and it can be read straight back off the first word:

    beat.speaker        c0:spk:1          ← stale, what the UI showed
    beat.words[0].spk   T1                ← correct, never used

No transcription, no attribution, no audio, no model call. The repair is a
column update from a file that is already in the derived bucket.

## Talk time, from the same file

`speakers.speech_ms` was summed from word durations at ingest time, and a
managed engine had returned one word whose span was hours long — which is how a
46-minute recording came to report 49m 51s of talking on one microphone, and
24.6 hours on another. The words are in the same cached ingest, so the totals are
recomputed here under the invariant `asr/timings.py` now enforces at the ASR
boundary. Exact, and again with nothing re-run.

## And where the beats are

A beat's frames are its own words' first start and last end, so the same word
made one beat 23 minutes long. Those are recomputed here too — recomputed, not
regrouped: *which* words went into which beat is `structure`'s decision, and it
was not what broke. The beat holding the impossible word has three words in it,
the same as its neighbours, because the repair keeps that word in its place in
the sequence rather than sorting it elsewhere (`asr/timings.py`).

An empty first-word speaker is left empty on purpose: those words really are
unattributed — below the silence floor on every microphone (`steps/speakers`) —
and "unattributed" is what the UI should say about them.

## Running it

    .venv/bin/python repair_beat_speakers.py            # report, change nothing
    .venv/bin/python repair_beat_speakers.py --apply

One-off. Jobs ingested after the ordering fix are already correct, and
`CACHE_VERSION = 4` means a re-ingest rebuilds rather than serving one of these
caches — so this exists for the rows that are already in the database, and is
deletable once they are gone.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).parent / "src"))

from mishne.asr.base import ASRResult, Word  # noqa: E402
from mishne.asr.timings import sanitise  # noqa: E402
from mishne.config import get_settings, load_env_file  # noqa: E402
from mishne.db import models as m  # noqa: E402
from mishne.storage import bucket_for, derived_key, get_storage  # noqa: E402
from mishne.timecode import Rate, ms_to_frames  # noqa: E402


def content_id(checksum: str) -> str:
    """The pipeline's id for these bytes — `project.asset_id_for`, in reverse."""
    return f"a_{checksum.lower()[:24]}"


@dataclass
class Repaired:
    """One asset's cached ingest, with the timings put right.

    Keyed on the beat id *suffix* rather than the whole id because the two id
    spaces differ in exactly the prefix (`db/ids.py`): the cache names a beat
    after the content, the database after the asset row.
    """

    #: `beat_0007` -> the speaker of its first word.
    speaker: dict[str, str]
    #: `beat_0007` -> (start_ms, end_ms), from its own repaired words.
    span: dict[str, tuple[int, int]]
    #: speaker -> (words, speech_ms).
    talk: dict[str, tuple[int, int]]


def read_cache(store, bucket: str, key: str, audio_seconds: float) -> Repaired | None:
    """Everything this script needs, from one cached ingest.

    The beats are in order and their word lists concatenate to the transcript,
    which is what `sanitise` needs: the invariant is about the sequence, and a
    word can only be bounded by the one after it. So the words are gathered
    once, repaired once, and then read back per beat — a beat repaired on its
    own would have no next word at its right edge.
    """
    try:
        body = store.client.get_object(Bucket=bucket, Key=key)["Body"].read()
    except Exception:  # noqa: BLE001 - a missing cache is an answer, not an error
        return None
    ingest = json.loads(body)

    beats = sorted(ingest.get("beats", []), key=lambda x: x["idx"])
    words: list[Word] = []
    owner: list[str] = []          # the beat each word belongs to, parallel
    for beat in beats:
        _, _, tail = beat["id"].rpartition("_beat_")
        for w in beat.get("words") or []:
            words.append(Word(w["t"], w["s"], w["e"], w.get("c", 1.0),
                              w.get("spk", "")))
            owner.append(f"beat_{tail}")
    if not words:
        return None

    result = ASRResult(words=words, language=ingest.get("language", ""),
                       provider="cache", model="", audio_seconds=audio_seconds)
    sanitise(result)

    speaker: dict[str, str] = {}
    span: dict[str, tuple[int, int]] = {}
    talk: dict[str, tuple[int, int]] = {}
    for w, beat_id in zip(result.words, owner):
        if beat_id not in speaker:
            speaker[beat_id] = w.speaker
            span[beat_id] = (w.start_ms, w.end_ms)
        else:
            start, _ = span[beat_id]
            span[beat_id] = (start, w.end_ms)
        if w.speaker:
            count, ms = talk.get(w.speaker, (0, 0))
            talk[w.speaker] = (count + 1, ms + w.duration_ms)
    return Repaired(speaker=speaker, span=span, talk=talk)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="write the updates; without it nothing is changed")
    args = ap.parse_args()

    # An entry point, so it loads `.env` itself: pydantic-settings populates
    # `Settings` and leaves `os.environ` alone, and boto3 reads the environment.
    # Without this the S3 client falls back to whatever real AWS credentials this
    # machine happens to have and every cache read fails as "no cached ingest" —
    # which reads exactly like a cache that is not there.
    load_env_file(Path(__file__).parent / ".env")

    settings = get_settings()
    store = get_storage()
    bucket = bucket_for("derived", settings)
    engine = sa.create_engine(str(settings.database_url).replace(
        "postgresql://", "postgresql+psycopg://"))

    a, b, sp = m.Asset.__table__, m.Beat.__table__, m.Speaker.__table__
    changed = 0

    with engine.begin() as conn:
        assets = conn.execute(
            sa.select(a.c.id, a.c.org_id, a.c.project_id, a.c.filename, a.c.checksum,
                      a.c.duration_frames, a.c.edit_rate_num, a.c.edit_rate_den,
                      a.c.drop_frame, a.c.start_tc_frames)
            .where(a.c.id.in_(sa.select(b.c.asset_id).distinct()))
            .order_by(a.c.filename)
        ).all()

        for asset in assets:
            roster = {
                r.speaker_id for r in conn.execute(
                    sa.select(sp.c.speaker_id).where(sp.c.asset_id == asset.id))
            }
            beats = conn.execute(
                sa.select(b.c.id, b.c.speaker, b.c.start_frames, b.c.end_frames)
                .where(b.c.asset_id == asset.id)
            ).all()
            # A beat whose speaker is not one of the asset's own speakers is the
            # symptom this script is named after.
            stale = [r for r in beats if r.speaker and r.speaker not in roster]

            if not roster:
                # Single-track material with no diarizer: `steps/speakers`
                # returns no speakers at all and says so, deliberately, rather
                # than inventing one voice for a room with three people in it.
                # The vendor's own diarization label survives on the word — it is
                # what nothing chose to trust — and there is no correct speaker
                # to write here. The UI renders these as unattributed, which is
                # what the attribution note already says about them.
                if stale:
                    print(f"  {asset.id}  {len(stale):4d} beats unattributed — "
                          f"voices were never separated (single track, no "
                          f"diarizer)")
                continue
            if not asset.checksum:
                print(f"  {asset.id}  seeded fixture, no checksum — skipped")
                continue

            fps = asset.edit_rate_num / (asset.edit_rate_den or 1)
            rate = Rate(asset.edit_rate_num, asset.edit_rate_den, asset.drop_frame)
            key = derived_key(asset.org_id, asset.project_id,
                              content_id(asset.checksum), "ingest.json")
            cache = read_cache(store, bucket, key,
                               asset.duration_frames / fps if fps else 0.0)
            if cache is None:
                print(f"  {asset.id}  no cached ingest — needs a re-ingest "
                      f"(transcription is cached, so it is free)")
                continue

            print(f"  {asset.id}  {asset.filename}")

            # ── the speaker on each beat ───────────────────────────────────
            fixed: Counter[str] = Counter()
            skipped = 0
            for row in stale:
                speaker = cache.speaker.get(row.id[len(asset.id) + 1:])
                # Only ever write a speaker this asset actually has. Anything
                # else would trade one wrong id space for another.
                if speaker is None or (speaker and speaker not in roster):
                    skipped += 1
                    continue
                fixed[speaker or "(unattributed)"] += 1
                if args.apply:
                    conn.execute(sa.update(b).where(b.c.id == row.id)
                                 .values(speaker=speaker))
            if fixed:
                counts = ", ".join(f"{k}×{v}" for k, v in sorted(fixed.items()))
                print(f"      {sum(fixed.values())} beat speakers → {counts}"
                      + (f"  ({skipped} not in the cache)" if skipped else ""))
                changed += sum(fixed.values())

            # ── where each beat is ─────────────────────────────────────────
            # A beat's frames are its own words' first start and last end, so the
            # impossible word made one beat 23 minutes long. Recomputed, not
            # regrouped: *which* words went into which beat is `structure`'s
            # decision and it was not what broke — the beat holding that word has
            # three words in it, the same as its neighbours.
            moved = 0
            longest_before = longest_after = 0
            for row in beats:
                repaired = cache.span.get(row.id[len(asset.id) + 1:])
                longest_before = max(longest_before,
                                     row.end_frames - row.start_frames)
                if repaired is None:
                    longest_after = max(longest_after,
                                        row.end_frames - row.start_frames)
                    continue
                start = asset.start_tc_frames + ms_to_frames(repaired[0], rate)
                end = asset.start_tc_frames + ms_to_frames(repaired[1], rate)
                end = max(end, start + 1)   # ck_beats_positive_duration
                longest_after = max(longest_after, end - start)
                if (start, end) == (row.start_frames, row.end_frames):
                    continue
                moved += 1
                if args.apply:
                    conn.execute(sa.update(b).where(b.c.id == row.id)
                                 .values(start_frames=start, end_frames=end))
            if moved:
                print(f"      {moved} beat spans moved; longest beat "
                      f"{longest_before / fps:.0f}s → {longest_after / fps:.0f}s")
                changed += moved

            # ── and the talk time on the legend ────────────────────────────
            for speaker_id in sorted(roster):
                count, ms = cache.talk.get(speaker_id, (0, 0))
                if not count:
                    continue
                was = conn.execute(
                    sa.select(sp.c.speech_ms).where(sp.c.asset_id == asset.id,
                                                    sp.c.speaker_id == speaker_id)
                ).scalar_one()
                if was == ms:
                    continue
                print(f"      {speaker_id} talk time {was / 60000:7.1f}m → "
                      f"{ms / 60000:5.1f}m  (clip is "
                      f"{asset.duration_frames / fps / 60:.1f}m)")
                changed += 1
                if args.apply:
                    conn.execute(
                        sa.update(sp)
                        .where(sp.c.asset_id == asset.id,
                               sp.c.speaker_id == speaker_id)
                        .values(word_count=count, speech_ms=ms))

        if not args.apply:
            # Nothing was written, but be explicit rather than relying on the
            # transaction happening to be empty.
            conn.rollback()

    print(f"\n{changed} rows {'repaired' if args.apply else 'would be repaired'}.")
    if not args.apply and changed:
        print("Re-run with --apply to write them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
