# Handoff — picking this up in VS Code with Claude Code

*Written 31 Aug 2026, at the point the project moves off the desktop app and
into the editor. Read this, then [HANDOVER.md](HANDOVER.md) for what the system
is, then [roadmap/README.md](roadmap/README.md) for what to build next.*

Everything the project is lives in **one repository**, `~/Dev/mishne.ai`, on
branch `phase-c-cost-and-billing`, plus two directories that are deliberately
not committed (`models/`, 2.9 GB, re-downloadable; `samples/`, 443 MB, the
irreplaceable part). There is **no deployment of any kind** — no Terraform, no
AWS account, no CI, no environments. That is the next infrastructure step and it
has its own plan: [AWS-MIGRATION.md](AWS-MIGRATION.md).

---

## 1. The first thing to do, before anything else

**Run the test suite on the Mac.** The last two commits —
`8cfc87f` (project creation) and `629d778` (the platform back-office) — were
written and committed from a Linux VM that could not execute the repo's venv,
which is a macOS build. They were verified against a live Postgres 16 with 49
assertions and a full `0001 → 0009 → base → 0009` migration cycle, but the
repo's own 500-odd tests have not seen them.

```bash
cd apps/api
./setup.sh                                   # venv, interpreter and ffmpeg checks
docker compose -f ../../infra/docker-compose.yml up -d
.venv/bin/alembic upgrade head               # brings a stale database to 0009
.venv/bin/python -m mishne.db.bootstrap
.venv/bin/python -m mishne.db.seed --reset
.venv/bin/python -m pytest -q                # expect 506 + the two new suites
```

Two things about a green suite here that are worth knowing before you trust it:

- **`test_reference_run.py` skips without a sample.** It is the regression
  target for the whole orchestration workstream, and for a long time it was
  quietly skipping in most runs. Give it the paths:

  ```bash
  MISHNE_SAMPLE_AAF=../../samples/SyncDaniel.aaf \
  MISHNE_SAMPLE_REPLAY=../../samples/SyncDaniel_roughcut/work/SyncDaniel_flat_a0.asr.json \
    .venv/bin/python -m pytest tests/test_reference_run.py -q
  ```

- **The back-office tests skip on a database below 0009.** That is deliberate —
  a stale local database should skip rather than fail with a missing table — but
  it also means "all green" can mean "none of the new code ran". Check the skip
  count, not just the colour.

If anything fails, fix it before pushing. Nothing is on the remote yet:
`origin` is `github.com/alonraif/mishne.ai` and the branch is **six commits
ahead** of it.

## 2. Then click the whole thing through, once

Nothing has been through the browser-to-AAF path end to end. Every part of it
is tested; the joins between the parts are not.

```bash
npm install            # apps/admin is a new workspace
./dev.sh               # Postgres, MinIO, schema, buckets, API, web, worker
```

First owner, once: `PUBLIC_SIGNUP=true ./dev.sh api`, sign up, turn it off.
After that an owner invites from the Team screen, and with
`MAIL_PROVIDER=console` the invitation link prints to the API's terminal.

Then, in the browser at `http://localhost:3000`:

1. **Upload a short English file.** This is the first time a browser will PUT a
   part at MinIO through the CORS rules `s3_cors.py` applies. An S3 error at
   this point is reported by the browser as a CORS failure, which sends the next
   hour in entirely the wrong direction — read the API and MinIO logs before
   believing the browser.
2. **Submit an `ai` job and watch the progress panel.** The local job runner
   (`orchestration/devrunner.py`) is what moves it off `queued`. `S3Workspace`
   has only ever run against moto; this is its first time against MinIO.
3. **Submit a `manual` job**, edit the cut in the browser, resume it. The
   `awaiting_edit` round trip passes in unit tests and no real job with media
   attached has done it.
4. **Download the AAF.** Presigned, with a Content-Disposition that gives it a
   real filename, and an audit row.
5. **Open it in Media Composer.** This is A2, the highest technical risk in the
   project, still unmeasured. Every deliverable is an AAF; if Avid will not take
   it, the platform has nothing to deliver.

Then the back-office, on the other origin:

```bash
cd apps/api && .venv/bin/python -m mishne.admin.bootstrap --email you@example.com
./dev.sh admin         # admin API on 8001 (loopback), back-office on 3001
```

Grant the org some credits, check the line appears on the customer's own
billing screen, suspend the org and confirm a signed-in session is turned away
with a 403 rather than a login loop, then unsuspend.

**Write down what breaks.** That list is the real output of this pass, and it
is what the AWS plan waits on.

## 3. What Claude Code should read first, and what it must not do

`CLAUDE.md` at the repo root is the standing brief and is loaded automatically.
The rules in it are not style preferences — each one is a bug that already
happened. In particular: rational timecode only, no customer content in logs,
`org_id` on every table with RLS as the backstop, media never transits the API,
`run.py` is the specification, money moves through the ledger only.

Read `docs/architecture/` before any structural change and the relevant ADR
before changing a decision. `docs/HANDOVER.md` has a section called *Things that
will bite you* — thirty-odd entries, every one of which cost real time to find.
Reading it costs ten minutes and has a good chance of saving a day.

Three things that look like tidying and are not:

- **Do not put an agent loop in the pipeline** (ADR-0002). It is a workflow.
- **Do not ask a model to hit a duration target** (ADR-0004). The LLM scores; a
  CP-SAT solver selects; arithmetic places the cuts.
- **Do not widen stage 7's output contract** (ADR-0007). It is what makes manual
  and hybrid modes one pipeline instead of three.

And one that is specific to the new code: **the back-office privilege lives in a
database role, not in a policy clause.** The tempting simplification — a
`platform_admins` flag every RLS policy also accepts — puts the clause that
decides whether one customer can see another's unreleased footage into every
policy in the schema, and puts the code able to set it inside the process facing
the internet. If a future change makes the admin process share a connection, a
credential or an origin with the product API, that is the same mistake wearing a
different hat.

## 4. Where things stand, in one table

| | State |
|---|---|
| Pipeline, 15 stages, AAF/FCPXML/EDL/OTIO | Works, on real Hebrew and English material |
| Transcription | Managed APIs routed by language; self-hosted Whisper one flag |
| Postgres, RLS, tenancy | Works; isolation proved at the database |
| Storage and upload | Works against moto and MinIO; **never through a browser** |
| Orchestration | Works locally; state machine generated, undeployed |
| Auth, invitations, roles, audit | Works; public signup closed by default |
| Billing | Stripe behind an interface, `payment_provider=fake`, no account |
| The ten screens | On real data (C2 done); Buy buttons still inert |
| Cost per job | Recorded exactly for model calls; transcription and compute unmeasured |
| Back-office | Built, committed, **untested by the repo suite** |
| Selection quality (A1) | Unmeasured. No corpus. The product risk. |
| Avid acceptance (A2) | Untested. The technical risk. |
| Deployment | None. [AWS-MIGRATION.md](AWS-MIGRATION.md) |

## 5. The order I would work in

1. `pytest` on the Mac; fix whatever the two new commits broke; push the branch.
2. The click-through in §2, including Media Composer. Write the failures down.
3. Fix what that found. This is the "basic QA" the AWS move waits on.
4. **Then** [AWS-MIGRATION.md](AWS-MIGRATION.md), phase by phase.
5. A1 — the selection corpus — in parallel, whenever there is a finished EDL to
   pair with rushes. It is the single highest-value asset the project does not
   have, and it is the thing that decides whether any of the rest matters.

## 6. Things a new session will trip over in the first hour

- **`alembic`, `pytest` and `python` are in `apps/api/.venv` and not on your
  PATH.** Prefix or activate.
- **`npm run build` needs the Mac.** The SWC binary in `node_modules` is a macOS
  build; a Linux shell cannot run it.
- **`npm install` at the root** before touching `apps/admin` — it is a new
  workspace and nothing else will pull its dependencies.
- **Port 3000 is pinned on purpose.** `dev.sh` refuses to start when it is taken
  rather than letting Next quietly move to 3001, because one origin is named in
  three places that must agree: the API's CORS allowlist, the session cookie's
  scope, and the buckets' CORS rules.
- **Point the API at `APP_DATABASE_URL`, not `DATABASE_URL`.** A superuser
  bypasses RLS silently, and every policy stays in the schema doing nothing.
- **Python 3.14 breaks OTIO.** `setup.sh` picks a supported interpreter.
