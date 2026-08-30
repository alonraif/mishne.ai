# C4 — The customer's material, and what happens to it

> Thread starter. Read [../HANDOVER.md](../HANDOVER.md) first; you should not
> need any other file.

## Goal

A customer can ask what we hold, ask for it to be deleted, and get an answer
that is true.

## What already exists

- [../architecture/04-security.md](../architecture/04-security.md) — the design.
- **Tenancy is enforced by the database** (B1, B4). `org_id` on every table, RLS
  enabled and FORCEd, the org taken from the session and set per transaction.
  `tests/test_rls_isolation.py` and `tests/test_pool_isolation.py` are the
  proofs, including that a pooled connection cannot carry one request's org into
  the next.
- **The audit log is written** (B4): sign-in, failed sign-in, sign-out, org
  creation, membership changes, every upload. Append-only by trigger. Owners can
  read it at `GET /v1/org/audit`.
- **Bucket lifecycle and CORS are code** (B2): `infra/s3_lifecycle.py` expires
  raw by retention, derived quickly, artifacts at a year, and aborts incomplete
  multipart uploads after seven days. `infra/s3_cors.py` names the origins.
- `orgs.retention_days` exists and defaults to 30. **Nothing reads it.**
- Presigned URLs at 900 seconds, scoped to one object;
  `storage.delete_prefix(bucket, org_prefix(org))` is the deletion mechanism and
  is why keys lead with the tenant.
- SSE-KMS per environment, refused at startup outside `local` without a key.

## What to build

1. **Deletion that actually deletes.** A customer asking to be forgotten needs
   objects gone from three buckets, rows gone from twenty tables, and an answer
   about the two tables that refuse to be deleted from (below).
2. **Retention enforced per org**, not just by the bucket's blanket rule.
   `orgs.retention_days` is the customer's contract and the lifecycle rule is
   the backstop; today only the backstop exists.
3. **A "what do you hold about me" export**: projects, assets, jobs, transcripts,
   artifacts, audit entries.
4. **Per-org KMS keys** where the plan allows, which the security doc promises
   and the code does not do — it uses one key per environment.
5. **The vendor register**: who receives what, with zero-retention terms and SOC
   2 on file. A broadcaster's security review asks for exactly this and having
   it ready shortens procurement by weeks.

## Decisions already made

- **Isolation lives in the database.** Application filtering is defence in
  depth, not the mechanism.
- **Keys lead with the tenant** (`orgs/{org}/...`), so retention, deletion and
  per-org IAM are all prefix operations rather than tag scans.
- **Media never transits the API**, and probing runs on a worker for the same
  reason.
- **No customer content in logs**, enforced by a filter and tested.

## Decisions still open — and one of them blocks the work

- **What deletion means for the two append-only tables.** `credit_ledger` and
  `audit_log` refuse `DELETE` by trigger, deliberately: a financial record that
  can be erased is not a record, and a security log you can delete is not
  evidence. So "delete everything about this customer" cannot mean what it says.
  The options are anonymise in place (which is an `UPDATE`, which the trigger
  also refuses), retain under a stated policy and say so in the contract, or a
  separate archival path with its own controls. **Decide this first** — it
  determines the shape of everything else in this workstream, and C1 needs the
  same answer for the ledger.
- Whether deletion is soft for a grace period. An editor who deletes a project
  the day before the client asks for it again is a support conversation, and an
  irreversible delete is a worse one.
- Data residency, and whether EU customers get their own buckets and keys.
- BYOK, which large customers eventually ask for.

## Traps

- **Deleting a tenant is an ordered operation, not one statement.** Nothing
  cascades from `orgs` — `org_id` is a plain column by design — and
  `job_assets.asset_id` is `ON DELETE RESTRICT`, so jobs go before assets before
  projects. `tests/conftest.purge_org` is the working order and the comment
  explaining why.
- **Two correct rules can forbid something between them.** B3 found that a
  project which had ever been billed for could not be deleted at all: the
  ledger's foreign key made the delete an `UPDATE`, and the append-only trigger
  refused. Migration 0004 fixed it. Expect more of this shape here, and look for
  it before promising a customer a deletion date.
- **An object with no row is unattributable and permanent.** The upload path
  writes the row first for this reason, and any deletion path has to reckon with
  objects whose row is already gone.
- **Presigned URLs bypass the API entirely.** They are credentials; 900 seconds,
  one object, never logged.
- Derived audio is reproducible and expires quickly — but it *is* customer
  content while it exists, and a retention answer that only covers `raw` is
  wrong.
- `use_mocks=True` must never reach an environment with real data. `Settings`
  refuses to construct; keep it that way.

## Definition of done

- A deletion request removes every object and row it can, and the policy for
  what remains is written down, agreed, and reflected in the contract.
- Retention is enforced per org and verified on a test bucket, with the
  lifecycle rule as the backstop rather than the mechanism.
- An export answers "what do you hold about me" completely.
- The vendor register exists and is disclosable.
- The isolation tests still pass, and a new table cannot ship without a policy.
