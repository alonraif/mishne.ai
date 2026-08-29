# C4 — Security, retention, and the customer's IP

> Thread starter. Read [../HANDOVER.md](../HANDOVER.md) first; you should not
> need any other file.

## Goal

Treat customer media as what it is — unreleased intellectual property, often
under embargo — and be able to say so credibly to a broadcaster's legal team.

## What already exists

- [../architecture/04-security.md](../architecture/04-security.md) — the design.
- `orgs.retention_days` in the schema design.
- RLS and `org_id` everywhere (B1, B4).
- `presign_ttl_seconds` at 900.
- Keys are environment-only, never in files — `llm/README.md` states this and
  `.gitignore` excludes `.env`.

## What to build

1. Encryption at rest for all three buckets and the database; encryption in
   transit everywhere.
2. Retention policy per org, enforced by lifecycle rules, with deletion that
   actually deletes — raw media, derived audio, artifacts, transcripts, and the
   rows that reference them.
3. A customer-facing deletion path, and evidence it completed.
4. Key management for the four vendor API keys and any ASR keys: rotation, and
   no key ever in a log line or an error message.
5. A data-processing description a customer's legal team can read: what leaves
   the system, to which vendors, and what they retain.
6. Access control on support tooling. The support view in C3 shows transcripts.

## Decisions already made

- **The AI never receives audio or video.** Only text leaves the system, to the
  model vendors. This is a genuine and unusual selling point with broadcast
  customers and it is worth stating plainly in the data-processing description.
- Three buckets by lifecycle so raw media can be deleted aggressively without
  destroying the artifacts the customer paid for.
- API keys from the environment only.

## Decisions still open

- Whether to offer zero-retention model endpoints, or on-premise/self-hosted
  models, for customers who will not accept text leaving at all. The provider
  interface in `llm/` makes a local model a provider rather than a rewrite.
- Data residency, which European broadcasters will ask for.
- Whether transcripts are retained after the artifacts are delivered.

## Traps

- **Transcripts are as sensitive as the footage.** An unreleased interview's
  transcript is the story. Anywhere a transcript is stored, logged or shipped is
  in scope — including the standalone transcript HTML page, the per-job JSON, and
  the ingest cache under `work/`.
- The ingest cache holds the full ASR output on disk indefinitely by design,
  because re-transcription is the expensive step. That is a deliberate trade and
  it must appear in the retention policy rather than being forgotten.
- Presigned URLs are credentials with a 15-minute life. Never log them.
- Model vendors have their own retention. Which ones, and for how long, belongs
  in the data-processing description — and it changes per vendor, which is now a
  routing decision.

## Definition of done

- Encryption at rest and in transit, verified.
- Deleting an org removes every artifact and every row, provably.
- A written data-processing description naming every vendor, what it receives,
  and what it retains.
- No key or presigned URL appears in any log, verified by a test.
