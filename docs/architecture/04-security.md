# 04 — Security

## What is actually at stake

mishne.ai holds unreleased footage. For a broadcaster that means embargoed material,
pre-air segments, unaired interviews, and sometimes legally sensitive raw recordings
where only a fraction was ever intended for broadcast. For a creator it means work in
progress they have not chosen to publish.

A leak here is not an inconvenience. It is a news story about the customer, caused by
a vendor. That framing should drive how much of this gets built before first
customer, which is: most of it.

The good news is that the architecture already reduces exposure. The engine needs
audio, not video, and never retains media after a job. The audio-only ingest path
(see [ADR-0005](../adr/0005-audio-only-ingest-path.md)) means for many customers
mishne.ai never holds their footage at all.

## Tenancy

Enforced at the data layer, not trusted to application code.

- `org_id` on every table, including where it is derivable by join.
- **Postgres row-level security** on every tenant table, as a backstop.
- A single data-access layer that opens every transaction with
  `SET LOCAL app.current_org_id = $1`. No query path bypasses it.
- Integration tests that attempt cross-tenant reads and assert they return zero rows.
  Run these in CI on every change.

Application-level filtering alone is one forgotten `WHERE` clause away from a
cross-tenant leak, and that clause is forgotten during a rushed fix at 2am, not
during code review.

## Identity and access

**WorkOS.** SAML SSO and SCIM directory sync are asked for in the first procurement
conversation with any broadcast buyer; building them is weeks of work with a long
tail of provider-specific quirks.

Roles, deliberately minimal for MVP:

| Role | Can |
|---|---|
| `owner` | Everything, including billing and retention policy |
| `member` | Create projects, upload, run jobs, download artifacts |
| `viewer` | Read projects and transcripts, download artifacts, no upload or job creation |

Resist adding granularity before a customer asks. Per-project ACLs are the first
thing enterprises request and the first thing that makes the permission model hard;
design the schema so `project_members` can be added later without migration pain.

## Storage

Three buckets, distinct policies:

| Bucket | Holds | Lifecycle | Key |
|---|---|---|---|
| `raw` | Customer media as uploaded | Purge N days after job completion, default 30 | Per-org CMK where plan allows, else per-env CMK |
| `derived` | Extracted audio, transcripts, VAD maps, embeddings | Purge with raw | Per-env CMK |
| `artifacts` | AAF, FCPXML, EDL, OTIO, transcript JSON | Retained per org policy, default 1 year | Per-env CMK |

- All buckets: public access blocked at the account level, SSE-KMS, versioning on,
  TLS-only bucket policy.
- Per-org key prefixes, with bucket policies and task-role policies denying access
  across prefixes.
- Presigned URLs: 15 minutes maximum, and scoped to the exact key.
- **Lifecycle rule to abort incomplete multipart uploads after 7 days.** A failed
  200 GB upload otherwise leaves orphaned parts that are invisible in the console and
  billed indefinitely.

## Vendor exposure

This is the sharpest tension in the design. The system promises to protect customer
content, and it sends that content's audio to third parties for transcription and its
transcript to a third party for scoring.

Requirements before any customer material touches a vendor:

- **Zero data retention, in writing.** Not a settings toggle — a contractual term.
  Both ASR and LLM providers.
- **No training on customer data**, contractually.
- Data residency options where the customer requires them. EU residency will be asked
  for; some ASR vendors support it, some do not, and this may drive vendor choice
  more than accuracy does.
- SOC 2 Type II from each vendor, on file.
- Vendor list, and what each receives, documented and disclosable to customers. A
  broadcaster's security review will ask. Having the answer ready shortens a
  procurement cycle by weeks.

Some customers will refuse third-party ASR entirely. That is the scenario where
self-hosted Whisper on GPU becomes a requirement despite the current no-GPU
constraint — which is precisely why ASR sits behind a provider interface. See
[ADR-0003](../adr/0003-managed-asr-behind-an-interface.md).

## Untrusted input

Uploaded media is attacker-controlled. Treat it accordingly.

- Validate content type server-side by inspecting the file, not by trusting the
  extension or the client-declared MIME type.
- Enforce size caps per plan before issuing presigned URLs.
- **AAF is structured storage and can be adversarial.** Validate declared essence
  sizes against actual before allocating; cap extraction; fail cleanly on malformed
  structures rather than exhausting disk.
- ffmpeg has a large attack surface. Run media processing in a task role with:
  no outbound network beyond S3 and vendor endpoints, read-only root filesystem
  except scratch, non-root user, dropped Linux capabilities, and a hard timeout.
- Never execute or interpret anything derived from uploaded content.

## Logging

**No customer content in logs. Ever.** No transcript text, no filenames, no
directory paths, no brief text.

Logs carry identifiers, durations, counts, status codes, and timings. This is
enforced by a log filter in the shared logging module and tested — it is far easier
to build in on day one than to retrofit after content has been sitting in CloudWatch
for six months, at which point remediation means proving what was retained and where
it was replicated.

The same rule applies to error tracking and to LLM request logging. Vendor SDK debug
modes frequently log full request bodies; verify this is off in production.

## Audit log

From the first commit. Cheap now, painful to retrofit.

Record: who, what action, which resource, when, from where. Specifically every
artifact download, every transcript view, every job creation, every permission
change, every retention-policy change.

Append-only. Retained beyond the media retention period — the record that footage was
accessed must outlive the footage.

## Retention

Default: raw media and derived audio purged 30 days after job completion; artifacts
and transcripts retained one year; audit log retained three years.

All configurable per org, within plan bounds. Hard delete on request, propagated to
S3 versions and vendor caches, with a completion record written to the audit log.

Retention is a product decision as much as a security one — it is also the dominant
storage cost lever, see [05 — Roadmap & Risks](05-roadmap-and-risks.md).

## Compliance path

Do not pursue certification pre-revenue. Do build so that certification is cheap
later:

- Access control, audit logging, and encryption as described above — these are the
  substance of a SOC 2 audit and are being built anyway.
- Infrastructure as code, so configuration is evidence.
- Documented incident response and change management once there is a team to follow
  them.

Expect SOC 2 Type II to be a gating requirement within roughly a year of the first
enterprise deal, and expect a broadcaster's own security questionnaire well before
that. The questionnaire is answerable from this document plus a vendor list.
