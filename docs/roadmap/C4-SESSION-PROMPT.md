# C4 — session prompt

Requires B1 (persistence), B2 (storage) and B4 (auth). One decision blocks the
rest of the workstream; take it first.

```
We are working on mishne.ai. This session has one job: workstream C4 — the
customer's material: retention, deletion, and being able to answer what we hold.

Read these three files first, in this order, and nothing else until you have:
  docs/HANDOVER.md                          — what exists, how to run it, traps
  docs/roadmap/C4-security-and-retention.md — this workstream's brief
  docs/architecture/04-security.md          — the design

Do not re-derive the architecture or read the whole codebase. The handover is
accurate and current; trust it.

## Scope

C4 only. Do not start billing (C1) or the web screens (C2).

Phase A (selection corpus, Avid acceptance) is deferred by decision.

## What already exists — do not rebuild it

  RLS everywhere              org_id on every table, enabled and FORCEd, the org
                              taken from the session and set per transaction
  test_rls_isolation.py       the proof, including that the connecting role can
  test_pool_isolation.py      neither bypass RLS nor carry one request's org
                              into the next on a pooled connection
  audit.py, /v1/org/audit     the audit log, append-only, owner-readable
  infra/s3_lifecycle.py       expiry per bucket, and the 7-day multipart abort
  infra/s3_cors.py            named origins, and the ETag header
  storage.delete_prefix       deletion by tenant prefix — why keys lead with org
  orgs.retention_days         exists, defaults to 30, and NOTHING READS IT

## The decision that blocks everything else

`credit_ledger` and `audit_log` refuse DELETE by trigger, deliberately: a
financial record that can be erased is not a record, and a security log you can
delete is not evidence. So "delete everything about this customer" cannot mean
what it says. Anonymise in place is an UPDATE, which the trigger also refuses.

Decide this before writing any deletion code, and tell me the answer. C1 needs
the same answer for the ledger, and it has to be the same one.

## What to build

1. Deletion that deletes: objects from three buckets, rows from twenty tables,
   and a written policy for what remains.
2. Retention enforced per org from orgs.retention_days, with the bucket
   lifecycle rule as the backstop rather than the mechanism.
3. A "what do you hold about me" export.
4. Per-org KMS keys where the plan allows — the security doc promises this and
   the code uses one key per environment.
5. The vendor register: who receives what, zero-retention terms, SOC 2 on file.

## Decisions I have already made — do not relitigate

- Isolation lives in the database. Application filtering is defence in depth.
- Keys lead with the tenant, so retention, deletion and per-org IAM are prefix
  operations.
- Media never transits the API.
- No customer content in logs.

## Decisions still open — raise them, do not quietly pick one

- Whether deletion is soft for a grace period.
- Data residency, and whether EU customers get their own buckets and keys.
- BYOK.

## Traps

- DELETING A TENANT IS AN ORDERED OPERATION, not one statement. Nothing cascades
  from orgs — org_id is a plain column by design — and job_assets.asset_id is ON
  DELETE RESTRICT, so jobs go before assets before projects.
  tests/conftest.purge_org is the working order.
- TWO CORRECT RULES CAN FORBID SOMETHING BETWEEN THEM. B3 found that a project
  which had ever been billed for could not be deleted at all: the ledger's
  foreign key made the delete an UPDATE and the append-only trigger refused.
  Migration 0004 fixed that one. Expect more of this shape here, and look before
  promising a customer a deletion date.
- An object with no row is unattributable and permanent. The upload path writes
  the row first for that reason.
- Presigned URLs bypass the API entirely. They are credentials: 900 seconds, one
  object, never logged.
- Derived audio is reproducible and short-lived, but it IS customer content
  while it exists. A retention answer covering only `raw` is wrong.
- use_mocks=True must never reach an environment with real data. Settings
  refuses to construct; keep it that way.

## Definition of done

- A deletion request removes every object and row it can, and the policy for
  what remains is written down and reflected in the contract.
- Retention is enforced per org and verified on a test bucket.
- An export answers "what do you hold about me" completely.
- The vendor register exists and is disclosable.
- The isolation tests still pass, and a new table cannot ship without a policy.

## Environment

  docker compose -f infra/docker-compose.yml up -d
  cd apps/api && ./setup.sh && .venv/bin/alembic upgrade head
  .venv/bin/python -m pytest -q tests/test_rls_isolation.py tests/test_pool_isolation.py

Start with the append-only question. Everything else in this workstream depends
on the answer, and it is a policy decision before it is an engineering one.
```
