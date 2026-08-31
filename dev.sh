#!/usr/bin/env bash
# Bring the whole thing up on one machine, in the right order.
#
#   ./dev.sh            # infra, schema, buckets, then API + web + job runner
#   ./dev.sh setup      # everything except the three processes
#   ./dev.sh api        # just one of them, in its own terminal
#   ./dev.sh web
#   ./dev.sh worker
#
# Why this file exists: end to end, this is eight steps across three terminals
# — compose, migrate, the app login role, the buckets, their CORS and
# lifecycle, uvicorn with USE_MOCKS=false, next, and a job runner. Every one of
# them is documented somewhere and the order is not, so the first person to try
# it gets a browser that loads, an upload that fails with what looks like a
# CORS error, and a job that never leaves `queued`.
#
# Local only. It sets ENVIRONMENT=local and nothing here is a deployment.
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$PWD"
API="$ROOT/apps/api"
WEB="$ROOT/apps/web"
PY="$API/.venv/bin/python"

B=$'\033[1m'; D=$'\033[2m'; Y=$'\033[33m'; X=$'\033[0m'
step() { echo "${B}==>${X} $*"; }

[ -x "$PY" ] || { echo "${Y}no venv — run apps/api/setup.sh first${X}"; exit 1; }

# ── the settings this script needs, whatever .env says ─────────────────────
#
# Exported rather than only written to the file, because an environment
# variable beats .env in pydantic-settings and every process below inherits
# this shell. The first version of this script only wrote them when it created
# .env from the example — so anyone who already had a .env (everyone who had
# ever set an API key) got a bucket script talking to real AWS and an API
# serving fixtures out of a database it had just migrated.
export ENVIRONMENT=local
export USE_MOCKS=false
export S3_ENDPOINT_URL=http://localhost:9000
export AWS_ACCESS_KEY_ID=minioadmin
export AWS_SECRET_ACCESS_KEY=minioadmin

if [ ! -f "$API/.env" ]; then
  step "creating apps/api/.env from the example"
  cp "$API/.env.example" "$API/.env"
  echo "   ${D}add XAI_API_KEY and GEMINI_API_KEY to it before running a job${X}"
fi

# And make the file agree, so `uvicorn` started by hand behaves the same. Only
# missing keys are added; nothing already in the file is overwritten, except
# USE_MOCKS — a stale `true` there is a booby trap that serves fixtures from a
# real database, and it is the default in .env.example, so almost every .env
# has it. Changing it says so out loud rather than doing it quietly.
"$PY" - "$API/.env" <<'PYEOF'
import sys
from pathlib import Path

env = Path(sys.argv[1])
text = env.read_text()
lines = text.splitlines()
present = {line.split("=", 1)[0].strip() for line in lines if "=" in line
           and not line.strip().startswith("#")}

wanted = {
    "S3_ENDPOINT_URL": "http://localhost:9000",
    "AWS_ACCESS_KEY_ID": "minioadmin",
    "AWS_SECRET_ACCESS_KEY": "minioadmin",
}
missing = {k: v for k, v in wanted.items() if k not in present}
if missing:
    text = text.rstrip("\n") + "\n\n# Added by dev.sh — MinIO from infra/docker-compose.yml.\n"
    text += "".join(f"{k}={v}\n" for k, v in missing.items())
    print("   added to apps/api/.env: " + ", ".join(missing))

out = []
for line in text.splitlines():
    if line.strip().startswith("USE_MOCKS=") and line.strip() != "USE_MOCKS=false":
        print("   apps/api/.env said USE_MOCKS=true — set to false, or the API "
              "serves fixtures from a real database")
        line = "USE_MOCKS=false"
    out.append(line)
env.write_text("\n".join(out) + "\n")
PYEOF

setup() {
  step "postgres and minio"
  docker compose -f infra/docker-compose.yml up -d
  # Compose returns when the containers are started, not when Postgres is
  # ready to answer. Migrating a second too early fails with a connection
  # error that reads like a configuration problem.
  step "waiting for postgres"
  for _ in $(seq 1 30); do
    docker compose -f infra/docker-compose.yml exec -T postgres \
      pg_isready -U mishne >/dev/null 2>&1 && break
    sleep 1
  done

  step "schema"
  (cd "$API" && "$PY" -m alembic upgrade head)
  # The app connects as a role RLS actually applies to, not as the superuser
  # compose created — a superuser bypasses every policy silently.
  step "application login role"
  (cd "$API" && "$PY" -m mishne.db.bootstrap)

  step "buckets, cors and lifecycle"
  "$PY" infra/s3_buckets.py
  "$PY" infra/s3_cors.py --apply --origin http://localhost:3000
  "$PY" infra/s3_lifecycle.py --apply

  echo
  echo "   ${D}ready. seed demo data with: cd apps/api && .venv/bin/python -m mishne.db.seed --reset${X}"
  echo "   ${D}first owner: PUBLIC_SIGNUP=true ./dev.sh api, sign up once, then turn it off${X}"
  echo "   ${D}invitations print to the API's terminal — MAIL_PROVIDER=console${X}"
}

api()    { cd "$API" && exec "$PY" -m uvicorn mishne.main:app --reload --port 8000; }
web()    { cd "$WEB" && exec npm run dev; }
worker() { cd "$API" && exec "$PY" -m mishne.orchestration.devrunner; }

case "${1:-all}" in
  setup)  setup ;;
  api)    api ;;
  web)    web ;;
  worker) worker ;;
  all)
    setup
    step "starting api, web and the job runner"
    # One terminal, three processes, and a trap so ctrl-c takes all of them
    # down together rather than leaving a job runner holding a job.
    pids=()
    ( api ) & pids+=($!)
    ( web ) & pids+=($!)
    ( worker ) & pids+=($!)
    trap 'echo; echo "stopping…"; kill "${pids[@]}" 2>/dev/null || true' INT TERM
    echo
    echo "   api    http://localhost:8000/docs"
    echo "   web    http://localhost:3000"
    echo "   minio  http://localhost:9001  ${D}(minioadmin / minioadmin)${X}"
    echo
    wait
    ;;
  *)
    echo "usage: ./dev.sh [all|setup|api|web|worker]"
    echo "  one word at a time — 'api|web|worker' is a shell pipeline, not a choice"
    exit 2 ;;
esac
