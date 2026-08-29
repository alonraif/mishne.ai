# Phase B — session prompt (starts with B1)

Phase B is four workstreams with real dependencies, so they do not go in one
session:

```
B1 persistence  ──┬── B2 storage ── B3 orchestration
                  └── B4 auth
```

**B1 comes first and blocks the other three.** The prompt below is scoped to
B1 alone. When it lands, generate the B2 and B4 prompts the same way from
their briefs — those two can then run in parallel.

Paste the block below into a fresh session, from the repo root.

---

```
We are working on mishne.ai. This session has one job: workstream B1 —
replacing the in-memory fixtures with Postgres, with row-level security and
org_id on every table from the first migration.

Read these three files first, in this order, and nothing else until you have:
  docs/HANDOVER.md                — what exists, how to run it, the traps
  docs/roadmap/B1-persistence.md  — this workstream's brief
  docs/adr/0012-two-environments-and-expand-contract-migrations.md
                                  — the constraint that shapes migration #1

Do not re-derive the architecture or read the whole codebase. The handover is
accurate and current; trust it. The schema is already designed in
docs/architecture/03-platform-and-data.md — implement it as written rather
than redesigning it. It has already absorbed the multi-asset rework.

## Scope

B1 only. Phase B is four workstreams and B1 blocks the other three; object
storage, orchestration and auth are separate sessions and you should not start
them. If you find yourself needing S3 or a login to finish B1, you have gone
out of scope — stop and tell me.

Note: Phase A (the selection corpus, and Avid acceptance testing) is deferred
by decision, not forgotten. Nothing in B1 depends on it.

## What already exists — do not rebuild it

  infra/docker-compose.yml       Postgres 16 with pgvector, healthcheck,
                                 credentials mishne/mishne, already matching
                                 the default database_url in config.py
  apps/api/pyproject.toml        sqlalchemy, alembic, psycopg already declared
  src/mishne/mock.py             the fixtures every endpoint returns today.
                                 This is your specification for what the API
                                 must serve — read it before writing models.
  src/mishne/schemas.py          Pydantic request/response models, done
  src/mishne/routers/            projects, assets, jobs, billing — shaped,
                                 returning mocks
  src/mishne/config.py           database_url, use_mocks

## What to build

1. One Alembic migration containing the whole schema from
   docs/architecture/03-platform-and-data.md. Do not dribble it out table by
   table — it is a greenfield database and the design is settled.
   Establish the expand/contract conventions here, and write them down where
   the next person writing a migration will see them.
2. RLS policies on every table, keyed on org_id, enabled in the same migration
   that creates the table. Key them on a session variable the request sets;
   B4 will wire real identity into it later.
3. SQLAlchemy models and a session dependency.
4. Replace mock.py behind each router, keeping use_mocks working so the web
   app can still run against fixtures.
5. A query layer for the read paths the web app needs: project list, project
   detail with assets, job detail with steps, transcript with beats.
6. A seed script that loads the current mock.py fixtures into a dev database,
   so every mockup screen still renders against real infrastructure.

## Decisions I have already made — do not relitigate

- org_id on every table, including where a join could derive it. Uniform RLS
  policies, and no path where a forgotten join condition leaks across tenants.
- job_assets is a join table. A job draws on many assets and an asset feeds
  many jobs; both directions are real. (ADR-0008)
- transcripts are keyed on the ASSET, not the job. Transcription is the
  expensive step and belongs to the upload — a job next month re-uses it free.
  That is the economics of the whole multi-upload feature.
- beats.asset_id is denormalised on purpose. A beat's timing is local to its
  own file and meaningless without knowing which file.
- The credit ledger is append-only. No row is ever updated. (ADR-0006)
- Two deployed environments, staging and production. Development happens
  against staging; schema iteration happens locally against docker-compose.
  Promotion is the same immutable artifact deployed to a second stack.
- IN-FLIGHT JOBS SURVIVE A DEPLOY. Old and new code run side by side, so every
  migration is backward-compatible — expand, dual-write, backfill, read-new,
  contract, across separate releases. This constrains migration #1. Read
  ADR-0012 before writing it. (ADR-0012)
- model_versions on jobs is the reproducibility contract, shaped
  {task: ["provider/model", ...]}, and records failover. (ADR-0011)

## Decisions still open — raise them, do not quietly pick one

- Whether `words` is a Postgres table or stays in S3 with only beats in the
  database. The design says a table, roughly 40k rows per three-hour
  transcript, partitionable later. If ingest write time looks bad, say so.
- Whether pgvector on beats.embedding is created now, given nothing populates
  it yet.
- Connection pooling shape, which really belongs to B3.

## Traps

- Every migration must be safe with the PREVIOUS release still running. No new
  column is NOT NULL without a default, because old code does not set it. No
  renames — add, dual-write, backfill, drop, across releases. Indexes created
  CONCURRENTLY, which in Alembic needs an autocommit block because it cannot
  run inside the migration's transaction.
- alembic downgrade must genuinely work for every migration. Without it a
  seamless deploy has no rollback, which is most of its value. Test it.
- Enable RLS in the CREATING migration. Adding it later means auditing every
  query already written, and the audit is the expensive part.
- A NULL org_id bypasses most naive RLS policies. Make the column NOT NULL.
- Timecodes are RATIONAL, never floats. Store numerator, denominator and a
  drop-frame flag as separate columns — never a single fps float. See
  src/mishne/timecode.py, and read its module docstring before modelling any
  time column.
- Store durations as FRAMES plus the rate. Seconds loses the frame boundary
  and it cannot be recovered.
- pipeline/project.py writes an ingest cache with a CACHE_VERSION, because a
  cache written by older code serves beats built by code that no longer exists
  and the only symptom is a subtly wrong cut. If persistence takes over any of
  that caching, keep an equivalent.
- Do not let use_mocks=True be reachable in an environment with real data.

## Definition of done

- `alembic upgrade head` on an empty database produces the full schema with
  RLS enabled everywhere, and `alembic downgrade base` cleanly reverses it.
- The expand/contract conventions are written down where the next person
  writing a migration will actually see them.
- Every router serves from Postgres with use_mocks=False, and still serves
  fixtures with use_mocks=True.
- A test proves a query issued as org A cannot see org B's rows AT THE
  DATABASE LEVEL, not in application code. This is the test that matters:
  customer media is unreleased IP and a cross-tenant leak is a broadcast
  incident, not a privacy incident.
- The seed script reproduces every mockup screen against a real database.
- The existing 90 tests still pass.

## Environment

  docker compose -f infra/docker-compose.yml up -d      local Postgres
  cd apps/api && ./setup.sh                             venv, checks
  .venv/bin/python -m pytest tests -q                   expect 90 passed

Python must be 3.11-3.13. A .venv is not portable between machines — never
copy one, rerun setup.sh.

Start by reading mock.py and the schema section of
docs/architecture/03-platform-and-data.md side by side, and tell me any place
the fixtures and the designed schema disagree before you write the migration.
That disagreement is the most likely source of rework.
```
