# B2 — session prompt

Requires B1 (persistence) to be done. Can run in parallel with B4.

```
We are working on mishne.ai. This session has one job: workstream B2 — object
storage and the upload path. Customers upload multi-gigabyte media directly to
S3, and the pipeline reads it from there instead of a local path.

Read these three files first, in this order, and nothing else until you have:
  docs/HANDOVER.md                       — what exists, how to run it, the traps
  docs/roadmap/B2-storage-and-upload.md  — this workstream's brief
  docs/adr/0012-two-environments-and-expand-contract-migrations.md
                                         — deploys never interrupt work

Do not re-derive the architecture or read the whole codebase. The handover is
accurate and current; trust it.

## Scope

B2 only. B1 (persistence) is done and you build on it. Orchestration is B3 and
auth is B4 — do not start either. If you find yourself writing a state machine
or a login, stop and tell me.

Phase A (selection corpus, Avid acceptance) is deferred by decision. Nothing
here depends on it.

## What already exists — do not rebuild it

  src/mishne/config.py         s3_bucket_raw, s3_bucket_derived,
                               s3_bucket_artifacts, aws_region,
                               presign_ttl_seconds (900)
  src/mishne/routers/assets.py the endpoint shape, returning a mock presign
  apps/web/.../new-job-flow.tsx the upload UI, against fixtures
  boto3                        already declared in pyproject.toml
  pipeline/steps/prepare.py    probe; branches on file type
  pipeline/steps/aaf_ingest.py AAF parsing, and it already reports which clips
                               failed to resolve to media
  pipeline/project.py          ingest(path, work_dir, ...) — the local-path
                               entry point everything currently goes through

## What to build

1. Presigned MULTIPART upload: initiate, per-part URLs, complete, abort. A
   three-hour ProRes file is far past the single-PUT limit.
2. Resumable client upload in the web app, with per-part retry.
3. Probe on arrival: an S3 event runs stage 0 so an asset's rate, start
   timecode, duration and audio track count are known before a job exists —
   the credit estimate needs them.
4. A working-directory abstraction so stages read and write S3 or local paths.
   project.ingest() currently takes work_dir: Path.
5. Lifecycle rules per bucket: raw by retention policy, derived audio short
   because it is reproducible, artifacts long. Plus a rule aborting incomplete
   multipart uploads after 7 days.

## Decisions I have already made — do not relitigate

- Three buckets by lifecycle, not one. Raw media is the customer's IP and
  expensive; derived audio is disposable; artifacts are the deliverable.
- Uploads go DIRECT to S3, never through the API. A 60 GB file must not
  traverse an application server.
- Move the asset id from filename+size to a content hash. project.asset_id_for
  says why it was deferred; real storage is when it stops being deferred.
- Two deployed environments, staging and production, each with its own buckets
  and KMS keys. Staging holds synthetic media only — never copy customer
  footage into it, however much easier a repro would be. (ADR-0012)

## Decisions still open — raise them, do not quietly pick one

- Whether to accept an AAF with LINKED rather than embedded media, which means
  the customer must upload the referenced media too and we must resolve them.
  aaf_ingest already reports unresolved clips, so the detection half exists.
- Whether to transcode to a mezzanine on ingest or always work from the
  original.
- Multi-region, if customers require data residency.

## Traps

- The pipeline reads files with ffmpeg and pyaaf2, which need REAL FILE PATHS,
  not streams. Either stage to local disk per worker or use a FUSE mount —
  decide deliberately and write down which, because it drives worker sizing in
  B3.
- An AAF with embedded essence is self-contained and can be enormous. Do not
  assume you can hold one in memory.
- ffprobe CANNOT READ AN AAF at all — it is structured storage, not a media
  container. Probing must branch on file type; aaf_ingest.parse() is that
  branch.
- Presigned URLs are credentials. Never log them. TTL is 900s.
- The work_dir ingest cache is what makes re-running a job cheap — stages 0-4
  are cached per asset and transcription is never repaid. Do not make it
  ephemeral per worker without measuring what re-transcription costs.
- Bucket and lifecycle changes are deployed while the previous release is still
  running. A rule that deletes something the old code still reads is an
  outage. (ADR-0012)

## Definition of done

- A 10 GB file uploads from the browser, resumes after a deliberate network
  drop, and completes.
- Probe runs on arrival; the asset row has rate, start timecode, duration and
  audio track count before any job exists.
- The pipeline processes an asset from S3 end to end with no local path in the
  request, producing the same artifacts as the local run.
- Lifecycle rules applied and verified on a test bucket, including the
  incomplete-multipart abort.
- The existing tests still pass.

## Environment

  docker compose -f infra/docker-compose.yml up -d   local Postgres
  cd apps/api && ./setup.sh                          venv, checks

Use a local S3 (MinIO or moto) for tests; do not make the suite need AWS.

Start by telling me how you intend to give ffmpeg and pyaaf2 real paths from
S3, and what that implies for worker disk. That decision constrains B3 and I
want it explicit before you write the upload code.
```
