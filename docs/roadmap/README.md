# Roadmap to production

Each file here is a **thread starter**. It is written so a fresh session — with
no memory of how the project got here — can open one file, read
[../HANDOVER.md](../HANDOVER.md) for context, and start work. They deliberately
repeat themselves rather than cross-reference, because the point is that you
never need two of them open at once.

Every brief has the same shape: goal, what already exists, what to build,
decisions already made, decisions still open, traps, and definition of done.

## Order, and why

Sequenced by **risk retired per week**, not by architectural tidiness. The two
Phase A items can kill the product; do them first and in parallel, because
neither needs any infrastructure and both are cheap.

### Phase A — retire the two risks that can end the project

| | Workstream | Why it is first |
|---|---|---|
| [A1](A1-selection-corpus.md) | Selection corpus and the quality number | Answers "is this worth paying for", and unblocks three open questions at once. No infra needed. |
| [A2](A2-nle-acceptance.md) | NLE acceptance, Avid above all | The highest technical risk in the project, still unmeasured. One day of work with the right machine. |

**Next up: A2.** A ready-to-paste session prompt is in
[A2-SESSION-PROMPT.md](A2-SESSION-PROMPT.md).

**Do not build the platform until A2 passes.** Every deliverable is an AAF. If
Media Composer will not take it, the platform has nothing to deliver.

### Phase B — make it a service instead of a script

| | Workstream | Depends on |
|---|---|---|
| [B1](B1-persistence.md) | Postgres, the real schema, RLS from day one | — |

**Phase B prompts**, one session each — B1 first, then B2 and B4 in parallel,
then B3:

| Workstream | Prompt | Needs | State |
|---|---|---|---|
| B1 persistence | [B-SESSION-PROMPT.md](B-SESSION-PROMPT.md) | — | **done** |
| B2 storage | [B2-SESSION-PROMPT.md](B2-SESSION-PROMPT.md) | B1 | **done** |
| B4 auth | [B4-SESSION-PROMPT.md](B4-SESSION-PROMPT.md) | B1 | **done** |
| B3 orchestration | [B3-SESSION-PROMPT.md](B3-SESSION-PROMPT.md) | B1, B2 | **done** |
| [B2](B2-storage-and-upload.md) | S3, multipart upload, presigned URLs | B1 |
| [B3](B3-orchestration.md) | Step Functions, workers, the step contract | B1, B2 |
| [B4](B4-auth-and-tenancy.md) | Accounts, orgs, session, tenant isolation | B1 |

### Phase C — make it sellable

| | Workstream | Depends on |
|---|---|---|
| [C1](C1-billing-live.md) | Stripe, credit packs, hold and settle for real | B1, B4 |
| [C2](C2-web-on-real-data.md) | The ten mockup screens against the real API | B1-B4 |
| [C3](C3-observability.md) | Logs, traces, cost per job, alerting | B3 |
| [C4](C4-security-and-retention.md) | Customer media is their IP; encryption, retention, deletion | B1, B2 |

## Reality check on sequencing

Phase B and C are ordinary SaaS engineering with a known shape — the design is
already written in [../architecture/03-platform-and-data.md](../architecture/03-platform-and-data.md).
They are not where the project succeeds or fails.

Phase A is. A perfect platform delivering mediocre cuts is a dead product, and
a rough script delivering cuts an editor keeps is a business. If time is short,
A1 and A2 are the ones that matter.

## Status, honestly

| Area | State |
|---|---|
| Interchange (AAF/FCPXML/EDL/OTIO) | **Works.** Automated round-trip, 4 formats × 4 rates. Resolve confirmed by hand. |
| Avid Media Composer | **Untested.** The open risk. |
| Transcription incl. Hebrew, RTL | **Works** on real material. |
| Beat structure, provenance-aware | **Works.** Verified on rushes and on an already-cut sequence. |
| Span proposal + silence gate | **Works.** Thresholds unvalidated. |
| Speakers: multi-track | **Works**, deterministic. |
| Speakers: single-track diarization | **Works, weak on short utterances**, and says so. |
| Multi-asset projects | **Works.** |
| LLM routing, four vendors | **Works.** Compliance measured; taste not. |
| Selection quality | **Unmeasured.** No corpus. |
| Persistence (Postgres, RLS) | **Works.** Twenty tables, isolation proved at the database. |
| Storage and upload | **Works.** Resumable direct-to-S3, probe on arrival, lifecycle rules. |
| Auth and tenancy | **Works.** Sessions, roles, audit log, WorkOS behind an interface. |
| Orchestration | **Works, undeployed.** Durable runner, generated state machine, worker image. |
| Billing, the ten screens, observability | **Not built.** C1, C2, C3. |
