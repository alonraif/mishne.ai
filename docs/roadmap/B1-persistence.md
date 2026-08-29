# B1 — Postgres, the real schema, RLS from day one

> Thread starter. Read [../HANDOVER.md](../HANDOVER.md) first; you should not
> need any other file.

## Goal

Replace the in-memory fixtures with Postgres, with row-level security and
`org_id` on every table from the first migration.

## What already exists

- **The schema is designed**, in
  [../architecture/03-platform-and-data.md](../architecture/03-platform-and-data.md).
  Use it as written — it has already absorbed the multi-asset rework (`job_assets`
  join table, `beats.asset_id`, `speaker_links`).
- `apps/api/src/mishne/mock.py` — the fixtures every endpoint currently returns.
  This is your specification for what the API must serve.
- `apps/api/src/mishne/routers/` — projects, assets, jobs, billing endpoints,
  shaped and returning mocks.
- `apps/api/src/mishne/schemas.py` — Pydantic request/response models.
- `apps/api/src/mishne/config.py` — `database_url` and a `use_mocks` flag.
- **`infra/docker-compose.yml`** — local Postgres 16 with pgvector, healthcheck,
  credentials `mishne/mishne`. Already matches the default `database_url`.
- **The dependencies are already declared** in `apps/api/pyproject.toml`:
  `sqlalchemy>=2.0`, `alembic>=1.14`, `psycopg[binary]>=3.2`. Nothing to add.

## What to build

1. Migrations (Alembic). One initial migration containing the whole schema from
   the architecture doc; do not dribble it out table by table.
2. SQLAlchemy models and a session dependency.
3. Replace `mock.py` behind each router, keeping `use_mocks` working so the web
   app can still run against fixtures.
4. **RLS policies on every table**, keyed on `org_id`, enabled in the same
   migration that creates the table.
5. Repository/query layer for the read paths the web app needs — project list,
   project detail with assets, job detail with steps, transcript with beats.
6. Seed script that loads the current `mock.py` fixtures into a dev database, so
   the mockups keep working against real infrastructure.

## Decisions already made

- **`org_id` on every table, including where it is derivable by join.** It makes
  RLS policies uniform and removes any path where a missing join condition leaks
  across tenants.
- **`job_assets` is a join table.** A job draws on many assets and an asset feeds
  many jobs — both directions are real (ADR-0008).
- **`transcripts` are keyed on the asset, not the job.** Transcription is the
  expensive step and belongs to the upload; a job next month reuses it for free.
  This is the economics of separated uploads.
- **`beats.asset_id` is denormalised on purpose.** A beat's timing is local to
  its own file and meaningless without knowing which file.
- **`model_versions` on jobs is the reproducibility contract.** It now records
  every model per task including failover (ADR-0011); the shape is
  `{task: ["provider/model", ...]}`.
- The credit ledger is **append-only**, never updated in place (ADR-0006).

## Decisions still open

- Whether `words` is a table or stays in S3 with only beats in Postgres. The
  design says a table (~40k rows per three-hour transcript) and notes it can be
  partitioned later. Revisit if ingest write time becomes a problem.
- pgvector for `beats.embedding` is designed but nothing populates it yet.
- Connection pooling shape once workers exist (B3).

## Traps

- **Enable RLS in the creating migration.** Adding it later means auditing every
  query already written against the table, and the audit is the expensive part.
- A `NULL` `org_id` bypasses most naive RLS policies. Make the column `NOT NULL`.
- Timecodes are **rational** (`24000/1001`), never floats. Store numerator,
  denominator and a drop-frame flag as separate columns — never a single `fps`
  float. See `apps/api/src/mishne/timecode.py`.
- Store durations as **frames**, plus the rate. Storing seconds loses the frame
  boundary and it cannot be recovered.
- The `ingest.json` cache written by `pipeline/project.py` has a `CACHE_VERSION`.
  If persistence takes over caching, keep an equivalent — a stale cache serves
  beats built by code that no longer exists, and the only symptom is a subtly
  wrong cut.

## Definition of done

- `alembic upgrade head` on an empty database produces the full schema with RLS
  enabled everywhere.
- Every endpoint in `routers/` serves from Postgres with `use_mocks=False`, and
  still serves fixtures with `use_mocks=True`.
- A test proves that a query issued as org A cannot see org B's rows, at the
  database level rather than in application code.
- The seed script reproduces every mockup screen against a real database.
