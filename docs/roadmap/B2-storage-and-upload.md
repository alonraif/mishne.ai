# B2 — Object storage and the upload path

> Thread starter. Read [../HANDOVER.md](../HANDOVER.md) first; you should not
> need any other file.

## Goal

Customers upload multi-gigabyte media directly to S3, and the pipeline reads it
from there instead of from a local path.

## What already exists

- `config.py` names three buckets: `s3_bucket_raw`, `s3_bucket_derived`,
  `s3_bucket_artifacts`, plus `presign_ttl_seconds` (900).
- `routers/assets.py` — the endpoint shape, currently returning a mock presign.
- `apps/web/src/components/new-job-flow.tsx` — the upload UI, against fixtures.
- The pipeline reads local paths everywhere: `pipeline/steps/prepare.py`,
  `audio.py`, `aaf_ingest.py`, `pipeline/project.py`.

## What to build

1. Presigned **multipart** upload: initiate, per-part URLs, complete, abort. A
   three-hour ProRes file is far past the single-PUT limit.
2. Resumable client upload in the web app, with per-part retry.
3. Probe-on-arrival: an S3 event triggers stage 0 so the asset's rate, timecode
   and duration are known before a job is ever created — the job estimate needs
   them.
4. A working-directory abstraction so stages can read and write S3 or local
   paths. Today `project.ingest()` takes a `work_dir: Path`.
5. Lifecycle rules per bucket: raw media by retention policy, derived audio short
   (it is reproducible), artifacts long.

## Decisions already made

- Three buckets by lifecycle, not one. Raw media is the customer's IP and
  expensive; derived audio is disposable; artifacts are the deliverable.
- Uploads go **direct to S3**, never through the API. A 60 GB file must not
  traverse an application server.
- The asset id is `filename + size` today (`project.asset_id_for`). Once storage
  is real, move to a content hash — the code says so and explains why it was
  deferred.

## Decisions still open

- Whether to accept an AAF with **linked** rather than embedded media, which
  means the customer must also upload the media the AAF references and the system
  must resolve them. `aaf_ingest.py` already reports unresolved clips.
- Whether to transcode on ingest to a mezzanine format or always work from the
  original.
- Multi-region, if customers require data residency.

## Traps

- **The pipeline reads files with ffmpeg and pyaaf2, which need real file
  paths**, not streams. Either stage to local disk per worker or use a FUSE
  mount; decide deliberately and document it, because it drives worker sizing.
- An AAF with embedded essence is self-contained and can be enormous. Do not
  assume you can hold one in memory.
- **`ffprobe` cannot read an AAF at all** — probing must branch on file type, and
  `aaf_ingest.parse()` is what handles that branch.
- Presigned URLs leak if logged. They are credentials.
- The `work_dir` cache is what makes re-running a job cheap. Do not make it
  ephemeral per worker without measuring what re-transcription costs.

## Definition of done

- A 10 GB file uploads from the browser, resumes after a deliberate network drop,
  and completes.
- Probe runs on arrival and the asset row has rate, start timecode, duration and
  audio track count before any job exists.
- `run.py` — or its worker equivalent — processes an asset from S3 end to end
  with no local path in the request.
- Lifecycle rules applied and verified on a test bucket.
