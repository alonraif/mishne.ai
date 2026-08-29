# Migrations — read this before you write one

Every migration in this project runs against a database that a **previous
release is still talking to**. Jobs here are long: a three-hour transcription
outlives a deploy, and old and new code run side by side until the work started
under the previous version finishes. That is the whole reason these rules exist.

See [ADR-0012](../../../docs/adr/0012-two-environments-and-expand-contract-migrations.md).

---

## The five-step dance

A schema change is never one migration. It is a sequence, spread across
**separate releases**:

| Step | What ships | Migration? |
|---|---|---|
| 1. **Expand** | Add the new column, nullable or defaulted. Add the new table. Add the new index. | yes |
| 2. **Dual-write** | Code that writes both the old and the new shape. | no |
| 3. **Backfill** | Fill the new shape for existing rows. Batched, restartable, never blocking a deploy. | yes, its own |
| 4. **Read-new** | Code that reads only the new shape. Still writes both. | no |
| 5. **Contract** | Drop the old column. Only once no running code reads it. | yes |

You cannot compress this. Steps 1 and 5 in one release is the bug this document
exists to prevent.

---

## Hard rules

**No `NOT NULL` without a default on an existing table.** The old release does
not set your new column, and its inserts must keep working.

*Exception, and it is the only one:* a column on a table created **in the same
migration**. There is no previous release writing to a table that did not exist,
so `NOT NULL` is correct there — and for `org_id` it is mandatory (see below).

**No renames. Ever.** `ALTER TABLE ... RENAME COLUMN` is instantly fatal to the
old release. Add the new name, dual-write, backfill, drop the old one — four
releases, not one statement. The same goes for renaming a table or changing a
column's type in place.

**No tightening a constraint in the expand step.** Adding a `CHECK` or a
`NOT NULL` to an existing column rejects writes from code that predates it. Add
the constraint `NOT VALID`, backfill, then `VALIDATE CONSTRAINT` in a later
release.

**Indexes are created `CONCURRENTLY`.** A plain `CREATE INDEX` takes an
`ACCESS EXCLUSIVE`-adjacent lock that blocks writes for the duration, which on a
large table is an outage. `CONCURRENTLY` cannot run inside a transaction, so in
Alembic it needs an autocommit block:

```python
with op.get_context().autocommit_block():
    op.create_index("ix_beats_transcript", "beats", ["transcript_id"],
                    postgresql_concurrently=True, if_not_exists=True)
```

Use the `concurrent_index` / `drop_concurrent_index` helpers in
[`conventions.py`](conventions.py) rather than writing this out each time.

A `CONCURRENTLY` build that fails leaves an **`INVALID`** index behind. It is not
used by the planner and it is not automatically retried — drop it and rebuild.
Check for them after any failed migration:

```sql
select indexrelid::regclass from pg_index where not indisvalid;
```

**`downgrade` must genuinely work.** Not a `pass`, not a `raise
NotImplementedError`. Without a working downgrade a seamless deploy has no
rollback, which is most of its value. `alembic downgrade base` on a fresh
database is part of CI; if your downgrade is a lie, that is where it is caught.

**No native `ENUM` types.** A value cannot be removed from a Postgres enum, so
any migration that adds one has no honest downgrade. Use `text` with a `CHECK`
constraint, which can be dropped and re-added freely. This is why
`jobs.status` is `text`.

**The credit ledger is append-only.** No migration may `UPDATE` a
`credit_ledger` row, and no code may either — the table carries a trigger that
refuses updates and deletes (ADR-0006). Correct a mistake with a compensating
`adjustment` entry.

---

## Row-level security

**Every table carries `org_id text NOT NULL`, and RLS is enabled in the same
migration that creates the table.** Both halves matter.

`org_id` is on every table *including where a join could derive it*. That is
deliberate: it makes the policy identical everywhere, and removes any path where
a forgotten join condition leaks across tenants. A `NULL` `org_id` slips past a
naive policy, which is why the column is `NOT NULL` and why there is no default.

Adding RLS to a table *later* means auditing every query already written against
it, and the audit is the expensive part. So:

```python
create_org_table("beats", sa.Column(...), ...)   # table + org_id + RLS + policy
```

`create_org_table` in [`conventions.py`](conventions.py) does all of it. If you
find yourself calling `op.create_table` directly, you are about to ship a table
with no policy on it.

### How the policy is keyed

Policies compare `org_id` against the `app.org_id` session variable:

```sql
CREATE POLICY org_isolation ON beats
  USING      (org_id = nullif(current_setting('app.org_id', true), ''))
  WITH CHECK (org_id = nullif(current_setting('app.org_id', true), ''));
```

When the variable is unset, `current_setting(..., true)` is `NULL`, the
comparison is `NULL`, and **no rows are visible**. Failing closed is the point:
a request that forgets to set the org sees an empty database, not someone
else's.

The variable is set with `set_config('app.org_id', :org, true)` — the third
argument is `is_local`, so it is scoped to the **transaction** and cannot leak
onto the next request that borrows the same pooled connection. `SET LOCAL` does
not accept bind parameters; `set_config` does, which is also what keeps it free
of injection.

B4 will put a real authenticated identity into that variable. Until then the
session dependency sets it from a request header, and that is the only thing
about this design that is temporary.

### Two roles, and why RLS looks broken if you ignore this

**A superuser bypasses RLS. So does the table owner, unless the table is
`FORCE`d.** Both are easy to trip over:

- Migrations run as the **owner** (`mishne` locally). The owner is exempt from
  its own policies by default, so every table is created with
  `ALTER TABLE ... FORCE ROW LEVEL SECURITY`.
- The application connects as **`mishne_app`**, a `NOLOGIN` group role created
  by migration 0001 that holds `SELECT/INSERT/UPDATE/DELETE` and nothing else.
  It is not a superuser and does not have `BYPASSRLS`, so its queries are
  filtered.

If you test isolation while connected as `mishne` (a superuser in
docker-compose), **every policy will appear to do nothing**. That is not a bug
in the policy. Connect as a role that is a member of `mishne_app`; see
`infra/local-app-user.sql` and `tests/test_rls_isolation.py`.

---

## Writing one

```bash
cd apps/api
.venv/bin/alembic revision -m "add beats.embedding index"
.venv/bin/alembic upgrade head
.venv/bin/alembic downgrade -1      # always test this
```

Before you open the PR, on a scratch database:

```bash
.venv/bin/alembic upgrade head && .venv/bin/alembic downgrade base
```

If that does not come back to an empty schema, the migration is not finished.
