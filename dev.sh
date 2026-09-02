#!/usr/bin/env bash
# Bring the whole thing up on one machine, in the right order.
#
#   ./dev.sh            # infra, schema, buckets, then API + web + the runner
#   ./dev.sh restart    # stop whatever is already running, clear the web
#                       # build cache, then the above — one command, from any
#                       # state, including a broken one
#   ./dev.sh setup      # everything except the three processes
#   ./dev.sh api        # just one of them, in its own terminal
#   ./dev.sh web
#   ./dev.sh worker     # probes completed uploads, runs queued jobs; reloads
#   ./dev.sh proxy      # builds the preview renditions, alongside the above
#                       # on a source change, like the API does
#
# Why this file exists: end to end, this is eight steps across three terminals
# — compose, migrate, the app login role, the buckets, their CORS and
# lifecycle, uvicorn with USE_MOCKS=false, next, and a job runner. Every one of
# them is documented somewhere and the order is not, so the first person to try
# it gets a browser that loads, an upload that fails with what looks like a
# CORS error, an asset that never leaves `probing`, and a job that never leaves
# `queued`.
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

# The app's origin is named in three places that must agree: the API's CORS
# allowlist, the session cookie's scope, and the buckets' CORS rules. Next
# quietly starts on 3001 when 3000 is busy, which breaks all three at once and
# surfaces as "No 'Access-Control-Allow-Origin' header is present" in a browser
# console — an error about a missing header that says nothing about ports.
#
# So the port is pinned and a busy one stops the script with the name of
# whatever is holding it.
WEB_PORT=3000
API_PORT=8000
# The back-office. Its own ports, because it is its own application on its own
# origin with its own cookie — see apps/admin/next.config.ts. Not started by
# `all`: it is not part of building the product, and a process that can change
# every customer's balance should be one you chose to start.
ADMIN_WEB_PORT=3001
ADMIN_API_PORT=8001
# Who you sign into the back-office as locally. Override in the environment if
# you would rather it were your own address.
ADMIN_EMAIL=${ADMIN_EMAIL:-ops@localhost}

port_is_free() {
  ! lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

require_port() {
  local port="$1" what="$2"
  if port_is_free "$port"; then return 0; fi
  echo "${Y}port $port is busy, and $what has to be on it.${X}"
  echo "${D}   holding it:${X}"
  lsof -nP -iTCP:"$port" -sTCP:LISTEN | sed 's/^/     /'
  echo "${D}   stop it, or set APP_ORIGIN and the app's port together — the API's"
  echo "   CORS allowlist, the session cookie and the bucket rules all name one"
  echo "   origin and it has to be the one the browser is actually on.${X}"
  return 1
}

# ── stopping what is already running ───────────────────────────────────────
#
# `restart` exists because of one failure that is common, confusing and not
# self-healing: `npm run build` writes a production build into `apps/web/.next`
# while `next dev` is serving out of the same directory, the build prunes chunks
# the dev server still has in memory, and from then on every request dies with
# `Cannot find module './383.js'`. The dev server never recovers, and the fix is
# three commands nobody remembers in that moment.
#
# It stops only what `all` starts. The back-office on 3001/8001 is a process you
# chose to start and this does not take it away from you, and Postgres and MinIO
# stay up because they hold state and `setup` is idempotent against them.
stop_port() {
  local port="$1" what="$2" pids
  pids=$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)
  [ -n "$pids" ] || return 0
  echo "   ${D}stopping $what on $port$([ -n "$pids" ] && echo " — pid $(echo $pids | tr '\n' ' ')")${X}"
  kill $pids 2>/dev/null || true
  local waited=0
  while [ "$waited" -lt 10 ]; do
    port_is_free "$port" && return 0
    sleep 0.5
    waited=$((waited + 1))
  done
  # Five seconds in and still holding the socket: it is not going to let go.
  echo "   ${D}  it did not stop on asking — forcing it${X}"
  kill -9 $pids 2>/dev/null || true
  sleep 0.5
}

stop_all() {
  step "stopping what is already running"
  # The wrappers as well as the port holder. `next dev` sits under `npm run
  # dev`, and killing only the listening child leaves npm to print a lifecycle
  # error into the terminal you are about to reuse. Matched on the port so the
  # back-office's own `next dev` is left alone.
  pkill -f "next dev --port $WEB_PORT" 2>/dev/null || true
  pkill -f "mishne.orchestration.devrunner" 2>/dev/null || true
  # And any earlier `./dev.sh all`, which is a shell sitting in `wait` on the
  # three children we are stopping. Left alone it holds a terminal and prints
  # nothing ever again. Never this shell, and never the one that launched it.
  for pid in $(pgrep -f "dev\.sh" 2>/dev/null || true); do
    [ "$pid" = "$$" ] && continue
    [ "$pid" = "${PPID:-0}" ] && continue
    kill "$pid" 2>/dev/null || true
  done
  stop_port "$WEB_PORT" "the app"
  stop_port "$API_PORT" "the API"
}

# The web build cache, which is the thing that was poisoned. Cheap to lose: a
# dev server rebuilds it in a second or two, and a stale one is a class of bug
# that looks like a code error and is not.
clear_web_cache() {
  step "clearing the web build cache"
  rm -rf "$WEB/.next"
  echo "   ${D}apps/web/.next removed${X}"
}

api()    { cd "$API" && exec "$PY" -m uvicorn mishne.main:app --reload --port "$API_PORT"; }
# 127.0.0.1 explicitly. `mishne.admin.main` refuses to start on anything else
# without ADMIN_ALLOW_PUBLIC_BIND, and this is the command people copy.
admin_api() {
  cd "$API" && exec "$PY" -m uvicorn mishne.admin.main:app --reload \
    --host 127.0.0.1 --port "$ADMIN_API_PORT"
}
admin_web() { cd "$ROOT/apps/admin" && exec npm run dev -- --port "$ADMIN_WEB_PORT"; }
web()    { cd "$WEB" && exec npm run dev -- --port "$WEB_PORT"; }
# Both halves of what the cloud does with events: an S3 notification calling
# `mishne.probe`, and Step Functions running a job. MinIO sends no notification
# and there is no state machine here, so one poll stands in for both.
#
# ## Under a file watcher, for the same reason the API is
#
# The API runs with `--reload` and the runner did not, which made a whole class
# of change invisible: edit a stage, a worker path or the ledger, watch the API
# pick it up, and the runner goes on executing the modules it imported hours
# ago. The failure that cost the most was exactly this — a fixed bug reappearing
# identically on the next job, because the process that ran it predated the fix.
# Nothing says so; a long-lived Python process has no way to.
#
# `watchfiles` comes with `uvicorn[standard]`, so it is the same reloader the
# API is already using, driving a command instead of an ASGI app.
#
# ## Why the SIGINT timeout is minutes and not seconds
#
# The runner is not stateless. On SIGINT it finishes the job in its hands and
# then exits — `devrunner.stop` — which is what makes a restart safe: the new
# process picks up the next job, and no job is interrupted part-way through a
# stage. Killing it mid-job would leave a job row in a running status with its
# hold held and no process coming back for it, which is the same shape as the
# bug this watcher exists to stop reappearing.
#
# So the watcher has to be willing to wait for a job. `watchfiles` sends SIGINT
# and then SIGKILL after `--sigint-timeout`; that timeout is therefore the
# longest job we are prepared not to break, not a liveness check. Fifteen
# minutes is longer than any local job on a laptop's worth of rushes. An idle
# runner exits in about a poll interval, so in practice the restart is instant.
WORKER_RELOAD_TIMEOUT=900
worker() {
  cd "$API"
  # Present via uvicorn[standard]; a venv without it still gets a job runner.
  if ! "$PY" -c "import watchfiles" 2>/dev/null; then
    echo "   ${Y}watchfiles is not installed — the runner will not pick up code"
    echo "   changes. Restart it by hand after editing, or re-run setup.sh.${X}"
    exec "$PY" -m mishne.orchestration.devrunner
  fi
  exec "$PY" -m watchfiles \
    --target-type command \
    --filter python \
    --sigint-timeout "$WORKER_RELOAD_TIMEOUT" \
    "$PY -m mishne.orchestration.devrunner" \
    src/mishne
}

# The preview builder. Its own process rather than a branch inside the job
# runner, because that loop probes and runs one job at a time — folding a
# ten-minute transcode into it would make previews wait for jobs and jobs wait
# for previews, which is the opposite of what this is for.
#
# On a laptop it is a process; in production it is a different machine, because
# ffmpeg at 100% of the API box is not a thing that may happen. Same code either
# way — only how it finds work differs. See orchestration/proxyrunner.py,
# ADR-0020 and ADR-0021.
#
# A shorter SIGINT timeout than the job runner's: the unit of work here is one
# ffmpeg over one asset, and the worst case of killing it mid-encode is that the
# row goes back in the queue. Nobody's credits are held.
PROXY_RELOAD_TIMEOUT=120
proxy() {
  cd "$API"
  if ! "$PY" -c "import watchfiles" 2>/dev/null; then
    exec "$PY" -m mishne.orchestration.proxyrunner
  fi
  exec "$PY" -m watchfiles \
    --target-type command \
    --filter python \
    --sigint-timeout "$PROXY_RELOAD_TIMEOUT" \
    "$PY -m mishne.orchestration.proxyrunner" \
    src/mishne
}

case "${1:-all}" in
  setup)  setup ;;
  api)    require_port "$API_PORT" "the API" || exit 1; api ;;
  web)    require_port "$WEB_PORT" "the app" || exit 1; web ;;
  worker) worker ;;
  proxy)  proxy ;;
  admin)
    require_port "$ADMIN_API_PORT" "the back-office API" || exit 1
    require_port "$ADMIN_WEB_PORT" "the back-office" || exit 1
    step "back-office"
    # Idempotent, so the back-office has a login without anyone remembering to
    # make one. `--ensure` creates the administrator only if there is not one
    # and takes the password from ADMIN_BOOTSTRAP_PASSWORD, which lives in
    # apps/api/.env and therefore outlives a reboot. Local only — the flag
    # refuses to run anywhere else.
    if [ -n "${ADMIN_BOOTSTRAP_PASSWORD:-}" ] || grep -q '^ADMIN_BOOTSTRAP_PASSWORD=.' "$API/.env" 2>/dev/null; then
      (cd "$API" && "$PY" -m mishne.admin.bootstrap \
        --email "$ADMIN_EMAIL" --name "Local" --ensure) | sed 's/^/   /'
      echo "   ${D}sign in as $ADMIN_EMAIL${X}"
    else
      echo "   ${Y}no ADMIN_BOOTSTRAP_PASSWORD in apps/api/.env.${X}"
      echo "   ${D}Add one and this script keeps the back-office login for you."
      echo "   Or make it by hand, once:"
      echo "     cd apps/api && .venv/bin/python -m mishne.admin.bootstrap --email you@example.com"
      echo "   Forgot the password? Same command with --reset-password.${X}"
    fi
    pids=()
    ( admin_api ) & pids+=($!)
    ( admin_web ) & pids+=($!)
    trap 'kill "${pids[@]}" 2>/dev/null || true' INT TERM
    echo
    echo "   back-office  http://localhost:$ADMIN_WEB_PORT"
    wait
    ;;
  restart|fresh)
    stop_all
    clear_web_cache
    # Falls through into the same start as `all`, deliberately: two ways to
    # start the stack is how they drift apart.
    set -- all
    exec "$0" all
    ;;
  all)
    require_port "$WEB_PORT" "the app" || exit 1
    require_port "$API_PORT" "the API" || exit 1
    setup
    step "starting api, web, the job runner and the preview builder"
    # One terminal, four processes, and a trap so ctrl-c takes all of them
    # down together rather than leaving a job runner holding a job.
    pids=()
    ( api ) & pids+=($!)
    ( web ) & pids+=($!)
    ( worker ) & pids+=($!)
    ( proxy ) & pids+=($!)
    trap 'echo; echo "stopping…"; kill "${pids[@]}" 2>/dev/null || true' INT TERM
    echo
    echo "   api    http://localhost:$API_PORT/docs"
    echo "   web    http://localhost:$WEB_PORT"
    echo "   minio  http://localhost:9001  ${D}(minioadmin / minioadmin)${X}"
    echo
    wait
    ;;
  *)
    echo "usage: ./dev.sh [all|restart|setup|api|web|worker|proxy|admin]"
    echo "  one word at a time — 'api|web|worker' is a shell pipeline, not a choice"
    exit 2 ;;
esac
