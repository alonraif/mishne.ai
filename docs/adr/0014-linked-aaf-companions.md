# ADR-0014 — Accept a linked AAF, and ask for the media it references

**Status:** Accepted · **Date:** 2026-08-29

## Context

An AAF is a sequence, not a container. An **embedded** one carries its essence
inside itself and is self-sufficient. A **linked** one is a few hundred
kilobytes of pointers at media sitting on an editor's SAN, and it is what
Media Composer exports by default.

B2 had to decide whether to accept one. Refusing is defensible — "export with
embedded media" is one checkbox — but it is a checkbox in someone else's
application, discovered after they have already uploaded, and for a long
sequence the embedded export can be tens of gigabytes larger than the media the
customer already has on disk.

`aaf_ingest.parse` already reported which clips it could not resolve, so the
detection half existed. What did not exist was any way to say *which files* were
wanted, or to hold a job until they arrived.

## Decision

**Accept it.** A linked AAF probes normally and lands in a new asset status,
`awaiting_media`, with one row in `asset_media_requirements` per referenced file
it does not contain. Uploading those files satisfies the requirements; when none
is outstanding the sequence becomes `ready` on its own.

**Match on basename, normalised for case.** The absolute path inside the AAF
describes a filesystem we will never see. `aaf_ingest._url_to_path` already
falls back to a same-directory basename match, so materialising the companions
beside the AAF at ingest is the whole of the resolution — the parser does the
rest, unchanged.

**`awaiting_media` is not an error.** Nothing has gone wrong: the upload worked
and the sequence is intact. It is also not `ready`, because a job started
against it would transcribe silence.

**A folder is N ordinary assets, and only the referenced ones.** *Added 1 Sep
2026.* The customer drops the folder their editor exported — or the whole export
folder, AAF included — and each file in it that a requirement asks for is
uploaded as its own asset, by the same presigned multipart path as any other
upload. Files nothing references are skipped and counted, not uploaded: a folder
can hold a year of rushes and a cut needs four of them. The frame rate every
audio companion has to declare (ADR-0005) is taken from the sequence, because
the sequence knows and asking 775 times does not.

**A job may run with an acknowledged gap.** *Added 1 Sep 2026, and it reverses
the last consequence below.* Submission refuses an `awaiting_media` sequence by
default and names the files it is waiting for. The submitter can say to go
ahead anyway; the clips that cannot be resolved are silent in the transcript and
still reference the right source, and what was missing is recorded on the job in
`jobs.media_gaps` — never re-derived, because uploading the file next week
satisfies the requirement row and would erase the reason the transcript has
silence in it. The floor is that a sequence which resolved *none* of its media
is still refused, whatever the flag says: that job transcribes silence, and the
customer cannot know that is what they are buying.

**Rows, not a `jsonb` column on `assets`.** The question the feature exists to
answer is *given a file the customer just uploaded, which sequences were waiting
for it?* — a lookup across every awaiting asset in the org, on every completed
upload. As rows that is one index; as a `jsonb` array it is a scan with a
containment operator that gets slower exactly as a customer's project gets big
enough to matter.

## Rationale

- **The customer already has the media.** Asking them to re-export a
  self-contained AAF is asking them to spend an hour and 40 GB of disk to give
  us something they could have given us as files.
- **The list is actionable.** "3 of 22 clips could not be resolved" is a
  complaint; "upload A001.mxf (which 14 clips need), B002.mxf and C003.mxf" is
  an instruction.
- **A basename is what survives the trip**, and it is what the parser already
  matches on. Keying on anything else would mean two resolution mechanisms that
  can disagree.

## Consequences

- **A basename is not unique in principle.** Two source files really can both
  be called `A001.mxf`, and the AAF gives us nothing better to key on. `mob_id`
  is recorded on every requirement so a stricter check can be added later
  without a second migration.
- **A re-probe replaces the requirement set** rather than merging it. A customer
  who re-exports with embedded essence must not be left blocked by a
  requirement that no longer exists; anything already satisfied keeps its
  satisfaction, because the file it refers to is still uploaded.
- **The companions are ordinary assets.** They have their own rows, their own
  probe, their own storage and their own retention. Nothing special-cases them,
  and a customer can use the same file in another job without uploading it
  again.
- **Ingest must materialise the companions beside the AAF**, under their
  original names. `workspace.materialise` takes them for exactly this reason.
- **A job must refuse to start against an `awaiting_media` asset.** ~~That check
  belongs with job submission (B3/C1) and is not yet written; until then the
  status is advisory rather than enforced.~~ *Superseded 1 Sep 2026.* The check
  was written (`routers/jobs.py`) and then softened, because a flat refusal is
  wrong for the ordinary export: `samples/peppercreative_habatim…` references
  776 files and ships 775 — the absent one is a video reference — and under a
  flat refusal it can never be submitted at all. It is now a question with a
  floor under it, as the Decision above says.

- **A basename asked for is the whole of what the customer is told.** The
  requirement rows are also the upload filter, so what the panel lists and what
  a dropped folder uploads are the same set by construction. A file the parse
  could not name — a clip with no locator at all — is invisible to both, and
  that is deliberate: a clip *name* is whatever the editor typed and sending
  somebody to look for it is worse than silence about it.

- **Requirements are discovered across every sound track.** *Added 1 Sep 2026.*
  They always came from `aaf_ingest.parse`, and `parse` read only the first
  audio track — so a four-microphone podcast asked for one file of four and
  transcribed one microphone of four, with nothing anywhere reporting a problem.
  Reading every track is what makes the list the customer is shown complete.
  See [ADR-0019](0019-mix-sound-tracks-for-transcription.md) for what is then done with those tracks.
