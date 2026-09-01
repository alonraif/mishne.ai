#!/usr/bin/env bash
# Grant credits through the back-office, from a terminal.
#
# The same call the org page at :3001 makes, for when you would rather not
# leave the shell. It goes through the platform API and therefore through the
# ledger and the action log — a mandatory reason, an append-only entry, the
# balance as a projection. Nothing here writes to `credit_ledger` directly, and
# nothing should: see ADR-0006.
#
# The password is read from a prompt, never taken as an argument, matching
# `admin.bootstrap` — an argument is in the process table and in your history.
#
#   ./tools/grant_credits.sh org_0d8e6c85 50 "topping up local testing"
#
set -euo pipefail

ORG=${1:?usage: grant_credits.sh <org_id> <credits> <reason>}
CREDITS=${2:?usage: grant_credits.sh <org_id> <credits> <reason>}
REASON=${3:?usage: grant_credits.sh <org_id> <credits> <reason>}
ADMIN_API=${ADMIN_API:-http://127.0.0.1:8001}
ADMIN_EMAIL=${ADMIN_EMAIL:-ops@mishne.test}

JAR=$(mktemp -t mishne-admin)
trap 'rm -f "$JAR"' EXIT

read -r -s -p "password for $ADMIN_EMAIL: " ADMIN_PASSWORD
echo

python3 - "$ADMIN_EMAIL" "$ADMIN_PASSWORD" >"$JAR.login" <<'PY'
import json, sys
print(json.dumps({"email": sys.argv[1], "password": sys.argv[2]}))
PY

curl -sS -c "$JAR" -X POST "$ADMIN_API/auth/login" \
    -H 'content-type: application/json' \
    --data-binary "@$JAR.login" >/dev/null
rm -f "$JAR.login"

python3 - "$CREDITS" "$REASON" >"$JAR.grant" <<'PY'
import json, sys
print(json.dumps({"credits": float(sys.argv[1]), "reason": sys.argv[2]}))
PY

curl -sS -b "$JAR" -X POST "$ADMIN_API/orgs/$ORG/credits" \
    -H 'content-type: application/json' \
    --data-binary "@$JAR.grant"
echo
rm -f "$JAR.grant"

curl -sS -b "$JAR" -X POST "$ADMIN_API/auth/logout" >/dev/null
