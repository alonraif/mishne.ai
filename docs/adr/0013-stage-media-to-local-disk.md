# ADR-0013 — Stage media to local disk; do not mount it

**Status:** Accepted · **Date:** 2026-08-29

## Context

B2 moves customer media into S3, and the pipeline stops reading local paths that
somebody typed on a command line. Two of its dependencies cannot be handed a
stream, and no amount of interface design changes that:

- **ffmpeg and ffprobe are separate processes.** They take `argv`, and `argv`
  holds a path.
- **pyaaf2 opens OLE structured storage and seeks around inside it.** An AAF is
  not read front to back; it is a small filesystem, and reading it means random
  access.

So "read the object from S3" is not available at the point of use. Something has
to put a real file on a real disk first, and the only question is what.

## Decision

**A worker downloads the object to its own scratch disk before any stage
touches it.** `mishne.workspace` is that boundary: `LocalWorkspace` is a
directory and nothing else — the concierge CLI on a laptop is unchanged — and
`S3Workspace` materialises an asset, mirrors the cacheable derived files back to
the derived bucket, and publishes artifacts.

**No FUSE mount.** Not mountpoint-s3, not s3fs, not a future equivalent, without
re-opening this ADR.

**What is mirrored between runs is deliberately narrow:** `ingest.json`, the
extracted audio, the raw ASR response. Not the intermediate `_seg_*.wav` files
and not extracted AAF essence, which is reproducible, enormous, and would cost
more to store than to rebuild.

## Rationale

- **A mount is very appealing right up to the first large AAF.** pyaaf2's access
  pattern over structured storage is thousands of small seeks, each becoming a
  ranged GET with a network round trip. Read amplification on a 30 GB AAF is
  pathological rather than merely slow.
- **When a mount does fail it fails badly.** `EIO` from inside a C library,
  several frames below any code that knows what an asset is, is not a
  diagnosable failure at three in the morning.
- **Downloading is dumber and better.** One sequential read at full bandwidth,
  trivially observable, failures are ordinary Python exceptions, and the file
  behaves like a file for every subsequent stage.
- **The ingest cache is the economics, and it has to outlive the worker.**
  Stages 0-4 are cached per asset and transcription is the expensive one. On one
  machine that cache was a directory; across workers, a cache that dies with the
  container means every retry re-transcribes and the unit economics invert.

## Consequences

- **Worker disk is sized against the largest asset it may be handed**, plus that
  asset's extracted audio, plus — for an AAF with embedded essence — the essence
  written out beside it:

  ```
  disk >= largest_asset_bytes * 2 + headroom
  ```

  This is an input to B3's worker sizing, not an afterthought.

- **A linked AAF is not one asset on disk, it is a folder.** Amended 1 Sep 2026,
  after ADR-0014. The sequence is a few hundred kilobytes; the companions it
  references all land beside it, and the several sound tracks a multi-track
  sequence is mixed from are rendered at full length before the mix (ADR-0019).
  So for a linked sequence the figure is:

  ```
  disk >= aaf_bytes + sum(companion_bytes) + mixed_audio + (tracks * flat_audio)
  ```

  A real export in `samples/` is 1.0 GB of companions across four tracks;
  another is 2.1 GB across 775 files. Neither is close to `largest_asset_bytes
  * 2` for any of the individual objects, and both are the number that has to
  fit. The per-track renders are 16 kHz mono, so `tracks * flat_audio` is small
  beside the companions — but it is not nothing on a feature-length sequence,
  and it is why `_track_` is in `workspace.NOT_CACHEABLE`.

- **`Settings.max_upload_bytes` is our ceiling, and it is a product decision.**
  `storage.MAX_OBJECT_BYTES` is S3's 5 TiB; ours is whatever a worker class can
  actually hold. Accepting an upload no worker can process is a job that fails
  after the customer has already waited for the upload.

  It is a **per-object** ceiling, and a folder of linked media is many objects
  none of which approaches it while their sum is what a worker must hold. There
  is no ceiling on that sum today, and that is a gap rather than a decision:
  the requirement rows say how many files a sequence wants before any of them
  is uploaded, so it is a check that can be made early, and is not made yet.

- **Time-to-first-stage includes a full download.** For a large asset that is
  minutes before anything appears to happen, so the job's step timeline has to
  show staging as work rather than as silence.

- **Scratch is per worker and disposable.** Anything that must survive the
  worker is published — derived files to the derived bucket, artifacts to the
  artifacts bucket — and anything not published is by definition rebuildable.

- **If a future stage genuinely needs random access to something enormous** —
  proxy generation over a whole master, say — the answer is a ranged read
  written deliberately for that stage, not a mount that silently changes the
  failure mode of every other stage.
