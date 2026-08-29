# ADR-0012 — Two environments, and every migration is backward-compatible

**Status:** Accepted · **Date:** 2026-08-29

## Context

The platform docs assumed three environments (`dev`, `staging`, `prod`) and said
nothing about how a release reaches production or what happens to work already
running when it does. Both gaps had to be closed before the first migration was
written, because one of the answers constrains every schema change the project
will ever make.

Jobs here are long. Transcription of a three-hour asset runs for a long time,
and a job's whole workflow longer still. A deploy that interrupts one costs a
customer their job and their credits.

## Decision

**Two deployed environments: `staging` and `production`.** Day-to-day
development is done against staging. Schema iteration happens locally against
`infra/docker-compose.yml`; staging is the only deployed lower environment.

**Promotion is the same immutable artifact deployed to a second stack.** One
build is deployed to staging, verified, and then that identical artifact is
deployed to production. Separate stacks, separate databases, separate buckets,
separate KMS keys. Migrations are run against each environment independently.

**In-flight jobs survive a deploy.** Old and new code run side by side until
work started under the previous version finishes.

**Therefore every migration is backward-compatible**, and schema change follows
expand/contract across separate releases:

1. **Expand** — add the new column, nullable or defaulted. Never drop, never
   rename, never tighten a constraint.
2. **Dual-write** — deploy code that writes both old and new.
3. **Backfill** — in its own migration or an offline job, never blocking a
   deploy.
4. **Read-new** — deploy code that reads only the new shape.
5. **Contract** — a later release drops the old column.

## Rationale

- **Three environments was one more than the team.** A deployed dev environment
  that nobody guards drifts, and its drift is indistinguishable from a bug. A
  local Postgres is a better dev database than a shared remote one because it
  can be destroyed.
- **The same artifact, not a rebuild.** If production runs a different build
  from the one staging verified, staging verified nothing.
- **Surviving in-flight jobs is what "no downtime" means for a job system.**
  API availability alone is not the promise; a customer whose three-hour job
  died because we shipped does not care that the login page stayed up.
- **Expand/contract is cheap to start and expensive to retrofit.** Adopting it
  at migration #1 costs a rule. Adopting it at migration #40 means auditing
  every table, every query and every deploy procedure already written.

## Consequences

- **A new column is never `NOT NULL` without a default**, because the old code
  still running does not set it.
- **Renames are forbidden.** Add, dual-write, backfill, drop — across releases.
- **Indexes are created `CONCURRENTLY`** so a deploy never takes a table lock.
  In Alembic this needs an autocommit block; it cannot run inside the
  migration's transaction.
- **`alembic downgrade` must genuinely work for every migration.** Without it
  "seamless" has no rollback, which is most of its value.
- **RLS policies are part of the schema and follow the same rules.** A policy
  changed in the expand step has to be valid for both code versions at once.
- **Step payloads carry a version.** An in-flight job's `job_steps` rows were
  written by the previous release and the new one has to read them
  (see [0002](0002-workflow-engine-not-agent-framework.md) and the step
  contract in [../architecture/00-overview.md](../architecture/00-overview.md)).
- **Staging holds synthetic media only**, as it always did — and this matters
  more now that development happens there. Customer footage never enters a lower
  environment, however much easier it would make reproducing a bug.
- Deploys are slower to design and boring to execute, which is the trade.

## Alternatives considered

**Drain before deploying.** Simpler migrations, and one long job blocks a
release for hours. Rejected: it makes shipping hostage to the longest job.

**Blue/green inside production.** True instant rollback, and the two stacks
share a database — so backward-compatible migrations become mandatory anyway,
for more infrastructure. The discipline adopted here gets most of the benefit;
blue/green can be added later without changing the migration rules.

**Kill and resume in-flight jobs.** Genuinely cheap here, because the per-asset
ingest cache means transcription is never paid for twice (ADR-0008). Rejected
as the default because a job visibly stalling mid-deploy is a worse experience
than a slower deploy — but it remains the correct fallback for a stuck job.
