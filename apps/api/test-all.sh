#!/usr/bin/env bash
# Run the whole suite against a scratch database, with the samples wired in.
#
# The problem this solves: most of the suite's guards are correct and
# self-defeating. `test_back_office` refuses to run when the database holds a
# platform administrator it did not create, because clearing the platform tables
# would delete your back-office login; the API-parity tests refuse to re-seed a
# database holding organisations they did not create, because `seed.reset()` is
# TRUNCATE over every table. Both are right — on a machine you have actually used
# the product with, they protect real work. Both also mean that on that same
# machine, roughly forty tests never execute, and "622 passed" quietly means
# "the back-office was not tested".
#
# So: a database created for this run and dropped after it, where truncating
# everything costs nothing and no guard has anything to protect.
#
#   ./test-all.sh              # scratch DB, all samples, whole suite
#   ./test-all.sh -k billing   # any pytest arguments are passed through
#
# Requires the docker-compose Postgres to be up. It does NOT touch the `mishne`
# database, and it does not read your .env — every setting is passed explicitly,
# because the point is to be independent of the machine's own configuration.

set -euo pipefail

cd "$(dirname "$0")"
REPO_ROOT="$(cd ../.. && pwd)"

PGHOST="${PGHOST:-localhost}"
PGPORT="${PGPORT:-5432}"
PGSUPERUSER="${PGSUPERUSER:-mishne}"
PGPASSWORD_="${PGPASSWORD:-mishne}"

# A name per run, so two of these in parallel — or one that died before its
# teardown — cannot collide.
SCRATCH="mishne_test_$$_$(date +%s)"
ADMIN_URL="postgresql+psycopg://${PGSUPERUSER}:${PGPASSWORD_}@${PGHOST}:${PGPORT}/postgres"
OWNER_URL="postgresql+psycopg://${PGSUPERUSER}:${PGPASSWORD_}@${PGHOST}:${PGPORT}/${SCRATCH}"
APP_URL="postgresql+psycopg://mishne_local:mishne_local@${PGHOST}:${PGPORT}/${SCRATCH}"

PY=.venv/bin/python

if [[ ! -x "$PY" ]]; then
    echo "no venv — run ./setup.sh first" >&2
    exit 1
fi

_sql() {
    # CREATE/DROP DATABASE cannot run inside a transaction, hence AUTOCOMMIT.
    "$PY" - "$1" <<'PYEOF'
import sys
import sqlalchemy as sa
import os
engine = sa.create_engine(os.environ["_ADMIN_URL"])
with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
    conn.exec_driver_sql(sys.argv[1])
engine.dispose()
PYEOF
}

cleanup() {
    local status=$?
    echo "==> dropping ${SCRATCH}"
    # FORCE: pytest may have left a connection open, and a dropped-but-not-really
    # database is a confusing thing to find on your next run.
    _ADMIN_URL="$ADMIN_URL" _sql "DROP DATABASE IF EXISTS ${SCRATCH} WITH (FORCE)" || true
    exit $status
}
trap cleanup EXIT

echo "==> creating ${SCRATCH}"
_ADMIN_URL="$ADMIN_URL" _sql "CREATE DATABASE ${SCRATCH}"

# Everything below runs against the scratch database. ENVIRONMENT=local is what
# lets `bootstrap` create the well-known local credential; it refuses anywhere
# else, deliberately.
export ENVIRONMENT=local
export DATABASE_URL="$OWNER_URL"
export APP_DATABASE_URL="$APP_URL"
export USE_MOCKS=false

echo "==> migrating"
.venv/bin/alembic upgrade head >/dev/null

echo "==> creating the application login role"
# `mishne_local` is cluster-wide, so it usually already exists; the SQL is
# idempotent. The GRANT, however, is per-database and is the part that matters
# here — without it the app role cannot see the scratch schema at all.
"$PY" -m mishne.db.bootstrap >/dev/null

# The samples the guarded tests ask for. Absent ones simply leave those tests
# skipping, which is the existing behaviour and not an error — `samples/` is
# deliberately not committed.
SAMPLE_AAF="${REPO_ROOT}/samples/SyncDaniel.aaf"
SAMPLE_REPLAY="${REPO_ROOT}/samples/SyncDaniel_roughcut/work/SyncDaniel_flat_a0.asr.json"
SAMPLE_LINKED="${REPO_ROOT}/samples/peppercreative_habatim_sync_4test-aaf_2026-08-29_2014/Habatim_Sync_4Test.aaf"

[[ -f "$SAMPLE_AAF" ]]    && export MISHNE_SAMPLE_AAF="$SAMPLE_AAF"
[[ -f "$SAMPLE_REPLAY" ]] && export MISHNE_SAMPLE_REPLAY="$SAMPLE_REPLAY"
[[ -f "$SAMPLE_LINKED" ]] && export MISHNE_SAMPLE_LINKED_AAF="$SAMPLE_LINKED"

for var in MISHNE_SAMPLE_AAF MISHNE_SAMPLE_REPLAY MISHNE_SAMPLE_LINKED_AAF; do
    [[ -n "${!var:-}" ]] || echo "    note: ${var} not found — its tests will skip"
done

echo "==> pytest"
"$PY" -m pytest "$@"
