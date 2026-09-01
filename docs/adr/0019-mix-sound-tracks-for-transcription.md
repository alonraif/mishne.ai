# ADR-0019 — A sequence's sound tracks are mixed for transcription and kept separate for the cut

**Status:** Accepted · **Date:** 2026-09-01

## Context

A podcast AAF does not have one sound track. It has one per microphone: the
export in `samples/peppercreative_law_podcast…` has four, each a 265 MB mono WAV
of the whole 46 minutes; the other real export in `samples/` has seven across
775 files.

`aaf_ingest.parse` read the first one and stopped — `chosen = (audio or video or
tracks)[:1]`, with a comment explaining that the audio track is what gets
transcribed. That was written for a sequence with one dialogue track and a
picture track, where it is correct. On a multi-microphone recording it fails
twice over, and quietly:

* **The customer is asked for a quarter of the media.** Requirements are folded
  from the clips `parse` returns (ADR-0014), so a four-mic sequence asked for
  one file, went `ready` when it arrived, and looked complete.
* **Three people are never heard.** Stage 1 extracts audio from the flattened
  file, so whatever `parse` skipped is not in the transcript, is not a beat, and
  cannot be selected. Nothing reports this: the run looks ordinary and the
  transcript looks short.

The failure was invisible because every downstream stage was working correctly
on the material it was given. That is the shape of bug this decision exists to
close.

Meanwhile `01-edit-engine.md` says stage 1 extracts audio "per source clip,
never on a flattened mix", and that has to be reconciled rather than ignored.

## Decision

**Read every sound track.** `parse` returns clips from all of them, each
carrying `track_index` and `track_name`. Requirement discovery, which folds
those clips, therefore asks for every referenced file without knowing anything
about tracks.

**Mix them into the one file that gets transcribed.** `flatten_audio` renders
each track full-length by the loop it always used, then mixes:
`amix=inputs=N:duration=longest:normalize=0` with a `1/sqrt(N)` trim. A sequence
with one sound track takes the path it always took and produces the same bytes —
there is no mix filter where there is nothing to mix, which is what keeps
`test_reference_run` a real regression test rather than a re-baselined one.

**Sum, do not average.** `normalize=1` divides by the input count, so four
tracks where one person is talking make that person a quarter as loud, and a
quiet microphone is the case transcription is already worst at. The `1/sqrt(N)`
trim is the incoherent-sum compensation: it keeps four mics off the clipping
ceiling without flattening one. Measured on the real export: peak 9783 of
32767, and the mixed RMS above every individual track's.

**Keep the per-track renders, and attribute speakers from them.** They are one
microphone each, at the sequence's length, on the same timeline as the mix —
which is exactly what `speakers.attribute_from_files` takes. So a multi-track
sequence gets its speakers the way multi-track material always has: the loudest
microphone is whoever is talking, by arithmetic, with no model and no ASR cost.
Mixing must not cost that, and without this it would.

**Express the cut against one track.** `AAFSource.primary_clips` is the first
sound track, and it is what `map_to_source`, the seam detection and the
assembler use. `clips` is what to ask for and what to transcribe; `primary_clips`
is what the output document can say.

### Why not transcribe each track separately

It is the better transcript, and by some distance: the track name *is* the
speaker, so there is no attribution to infer, and crosstalk is separable rather
than merely flagged. It is also N times the ASR cost per job, it changes the
asset-to-track cardinality ADR-0008 rests on, and it touches stages 1, 2, 4 and
speaker attribution. That is a larger decision than this one and it is not
foreclosed: the per-track renders this decision keeps on disk are exactly what
it would need.

### Why this does not contradict stage 1's rule

"Per source clip, never on a flattened mix" is about not accepting a programme
mix in place of the isolated tracks — a stereo mixdown of a finished show, where
the individual microphones no longer exist and nothing can recover them. Here
the isolated tracks are what we have, they are rendered and kept, and the mix is
made from them for one consumer: the transcriber, which takes one mono file.
Attribution still reads the isolated tracks. Nothing downstream is handed a mix
in place of something better.

## Rationale

- **One transcript of the whole room beats four transcripts of a quarter of
  it.** The product cuts conversation. A transcript missing three of four
  speakers is not a degraded cut, it is the wrong cut.
- **The mix is where the tracks stop mattering.** Boundaries are gated on
  silence (ADR-0010) and silence in a conversation is silence on every
  microphone, so the mix is the right signal for VAD as well as for ASR.
- **Attribution by microphone is arithmetic and always right**, where
  diarization is a model that can be wrong and says so. Choosing the mix for
  transcription while keeping the mics for attribution takes the better answer
  in both places.
- **A single-track sequence must not move.** Most AAFs are single-track, the
  reference run is one, and a change that re-baselines a byte comparison has
  destroyed the test that would have caught it.

## Consequences

- **The emitted cut references the primary sound track only.** Four microphones
  in, one track of clips out. The timeline positions are right, so an editor can
  extend the cut across their other tracks in Media Composer — but we do not
  emit them, and for a four-mic podcast that is real work left to the customer.
  Doing it properly means N parallel audio tracks in the canonical OTIO document
  (ADR-0001), an N-way `map_to_source`, and an EDL that cannot express any of
  it. It is the obvious next decision and it is not this one.
- **Attribution is only as good as the track layout.** The real export turns out
  to be two stereo pairs rather than four mics: tracks 1 and 2 are identical, as
  are 3 and 4. Attribution correctly finds two voices and correctly reports
  every word as having two microphones at similar levels, which reads as high
  crosstalk and marks the labels unreliable. That is honest and it is not
  useful; collapsing identical L/R pairs into one microphone would fix it and is
  not done.
- **Worker disk grows by the track count.** `tracks * flat_audio` at 16 kHz
  mono, on top of the companions — see the amended formula in ADR-0013. The
  renders are in `workspace.NOT_CACHEABLE` so they are never mirrored: four
  full-length WAVs per asset is 350 MB of reproducible bytes on a 46-minute
  sequence.
- **`duration_frames` is the longest track, not the sum.** Tracks are parallel.
  This is obvious and it is exactly the kind of arithmetic that silently
  quadruples a sequence's length if the loop is written once and reused.
- **A sequence with several sound tracks is still "rushes", not "sequence".**
  Provenance comes from the seams on the primary track, so four microphones
  running continuously are one clip each and no cut decisions — which is what
  they are. Four tracks is not four sets of edits.
