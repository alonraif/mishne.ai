# Moving to AWS — one staging environment, after basic QA

*Written 31 Aug 2026. Nothing here has been built. This is the plan, the order,
and the traps; it is deliberately opinionated so that a session can pick up any
one phase without re-deciding the ones before it.*

**Do not start this until the QA pass in
[HANDOFF-CLAUDE-CODE.md](HANDOFF-CLAUDE-CODE.md) is done.** Two of the three
things most likely to break in the cloud — browser CORS against real S3, and a
worker materialising media from object storage — are also the two things that
pass locally for reasons that will not hold in AWS. Finding out which is which
while a Terraform apply is half-finished costs a day for no reason.

## The shape, and why

**One environment first: staging.** ADR-0012 says two environments, staging and
production, in separate AWS accounts, with development running against staging
and schema iteration against the local Postgres. Build staging, run the product
against it for a while, and copy the modules to production when there is a
customer. Building both now doubles the surface with nothing to put in the
second.

**Terraform, in the repo, under `infra/terraform/`.** Remote state in S3 with
DynamoDB locking, in a small bootstrap module applied once by hand. One root
module per environment, variables for the things that differ, and modules for
everything else — so production is a directory with different values and not a
second implementation.

**What does not change:** the application. Every AWS-shaped decision in this
codebase was made in advance — the buckets and their key scheme, the presigned
multipart upload, the generated Step Functions definition, the two compute
tiers, the app role that RLS applies to. The move is configuration and
infrastructure, not a rewrite. If a phase below asks for an application change
beyond wiring, treat that as a signal something is wrong with the plan.

**Nothing migrates.** There is no production data. The local database and MinIO
buckets are development fixtures and are thrown away, not moved. This is the
last moment that will ever be true, which is the argument for doing it now.

---

## Phase 0 — the gate

- `pytest` green on the Mac, including the back-office and project-creation
  suites, with the reference run given its sample.
- The browser-to-AAF click-through done, and what it broke, fixed.
- An AAF opened in Media Composer (A2). If Avid refuses the output, stop: there
  is nothing to deploy.
- The branch pushed to `origin`.

## Phase 1 — account, identity, state

1. A dedicated AWS account for staging (Organizations, or a standalone account
   with a billing alarm at a number you would actually mind).
2. Region `eu-west-1`, which is what `.env.example` has always said and what the
   vendors' latency favours from Israel.
3. **No long-lived access keys.** GitHub Actions gets an OIDC role; you get SSO.
   An access key in a `.env` file is the first credential to leak and the last
   one to be rotated.
4. Bootstrap module: the state bucket (versioned, encrypted, public access
   blocked), the lock table, and the OIDC provider. Applied once, by hand, with
   local state, then committed.
5. A budget and a cost anomaly alert before the first resource. The heavy tier
   is EC2 spot and the temptation to leave one running is real.

## Phase 2 — network

A VPC with public and private subnets across two AZs. Everything that holds
customer data or credentials lives in private subnets: RDS, the ECS tasks, the
back-office. NAT is the only line item here worth thinking about — one NAT
gateway rather than one per AZ for staging, and **VPC endpoints for S3 and
DynamoDB** because worker traffic to S3 is the bulk of the bytes and routing it
through NAT is paying for the privilege.

Also endpoints for Secrets Manager, ECR (api + dkr), CloudWatch Logs and Step
Functions, or a private task cannot start, read its secret, or log — a failure
that shows up as a task that dies before your code runs.

## Phase 3 — S3, and the parts that differ from MinIO

Three buckets, by lifecycle rather than by content type: `raw`, `derived`,
`artifacts`. The key scheme is already in `storage.py` and does not change.

Terraform creates them; **their lifecycle and CORS configuration stays in
`infra/s3_lifecycle.py` and `infra/s3_cors.py`**, applied as a step. That is a
deliberate split, and the reason is that those two scripts are testable against
moto and MinIO without an AWS account, and HCL is not.

```bash
python infra/s3_lifecycle.py --apply
python infra/s3_cors.py --apply --origin https://app.mishne.ai
```

Six things that are true in S3 and were not true locally:

- **MinIO answers `NotImplemented` to `PutBucketCors`.** The local rules were
  never actually applied — the browser upload works on a laptop because nothing
  is enforcing anything. S3 enforces. `ExposeHeaders: ["ETag"]` is not optional:
  without it the browser cannot read the ETag of a part and no multipart upload
  can be completed, and the error says nothing about headers.
- **Versioning is on** (it is on locally too, since `3a91fa0`). A delete writes
  a marker rather than removing the object, which is what C4's retention path
  has to be written against.
- **SigV4 explicitly.** `storage.get_client` already sets it; a boto3 client
  built with the defaults signs with SigV2 and every bucket created after 2018
  refuses it — at upload time, long after the code that chose the signature
  returned.
- **The part size the server sends is the part size the client must slice
  with.** Already true in the browser client; S3 will assemble mismatched parts
  without complaining and hand you a corrupt object.
- **KMS makes presigning harder.** A distinct customer-managed key per bucket is
  the right end state, but SSE-KMS on a presigned PUT means the browser must
  send the encryption headers the signature covers, and the signer's role needs
  `kms:GenerateDataKey` as well as `kms:Decrypt`. Start staging with SSE-S3,
  and move to SSE-KMS as its own change with its own browser test, or you will
  be debugging two things at once.
- **Block public access on, and a bucket policy denying non-TLS requests.** Both
  are one-liners and both are the kind of thing a broadcaster's security review
  asks for by name.

Finally, the **S3 event notification on the raw bucket** calling
`mishne.probe.handle_s3_event`. That entry point already exists and is what
stage 0 is locally. In AWS it is a small Lambda from the same image, or an
EventBridge rule into the state machine — Lambda is simpler and probing is
short. It reads the object, so it needs the bucket and the database, which means
it is in the VPC.

## Phase 4 — RDS, and the two roles

Postgres 16 with pgvector, `db.t4g.medium` for staging, single-AZ (staging), 7-day
backups, deletion protection on, encrypted, private subnets only.

**Two roles, and the distinction is load-bearing:**

- The **owner** runs migrations. `alembic upgrade head` and nothing else.
- The **application role** from `infra/local-app-user.sql` is what the API and
  workers connect as, and it is the reason RLS does anything at all. A superuser
  bypasses row-level security silently, and so does a table owner unless the
  table is `FORCE`d.

Both credentials in Secrets Manager, rotation later. `tests/test_rls_isolation.py`
asserts the connecting role can do neither — run its equivalent against staging
once, by hand, because "the policies are in the schema" and "the policies are
doing something" are different claims and only one of them is checked by a
migration succeeding.

Migrations run as a one-off ECS task from the same image, not from a laptop. The
expand/contract rules in `apps/api/migrations/README.md` are not optional once
there is a running deployment: every migration must be backward-compatible with
the code already serving traffic (ADR-0012).

## Phase 5 — secrets and configuration

Everything in `.env.example` becomes either a Terraform variable (bucket names,
region, origins) or a Secrets Manager secret (database URLs, vendor keys, the
Stripe secret and webhook signing secret, the session signing key). The task
definitions reference secrets by ARN; nothing is baked into an image and nothing
is an environment variable in a plan file.

Three settings that decide whether a deployment is safe rather than merely
running:

- `ENVIRONMENT=staging`. `Settings` refuses `use_mocks` outside `local`, which
  is the guard that keeps fixtures from ever being served to a customer.
- `PUBLIC_SIGNUP=false`. It creates a brand-new organisation for anyone who
  reaches it.
- `ADMIN_ALLOW_PUBLIC_BIND` stays **false**. See phase 8.

## Phase 6 — images, and the two compute tiers

One ECR repository per image, immutable tags, scan on push. `apps/api/Dockerfile`
already builds the worker: ffmpeg, the models, non-root.

| Tier | Runs on | Why |
|---|---|---|
| API | Fargate, 1 vCPU / 2 GB, behind an ALB | Stateless, scales on request count |
| Light worker | Fargate | Structure, score, solve, assemble, emit, validate — mostly waiting on vendor APIs |
| Heavy worker | ECS on EC2 spot, compute-optimised, EBS | ffmpeg extract, AAF demux, probe |
| Preview fleet | ECS on EC2 spot, compute-optimised | The proxy transcode, and nothing else (ADR-0021) |

The heavy tier is not a preference. Fargate's ephemeral storage caps at 200 GB
and a worker's disk must hold the largest asset it may be handed, plus its
extracted audio, plus — for an AAF with embedded essence — the essence written
out beside it (ADR-0013). And the task needs **8 GB of memory or large-v3 is an
OOM kill with no useful message**; that matters even though transcription is now
a vendor call, because the self-hosted path is still supported and is what an
air-gapped customer runs.

Spot interruption is handled by the idempotency rule: a step interrupted mid-run
is re-run. That is the same property that makes resume work (ADR-0016), so it is
already true and already tested.

**The preview fleet is separate from the heavy worker on purpose.** They have the
same shape — CPU-bound ffmpeg on spot — and merging them would put the transcode
back on a machine that jobs are waiting for, which is the whole point of
ADR-0021. It is also the tier that can be scaled to zero, throttled, or drained
without a customer noticing anything except a preview arriving later, and that
is worth having as its own dial.

Its entry point is `python -m mishne.orchestration.proxyrunner --serve`; the unit
of work it invokes is `mishne.orchestration.proxyworker`, which takes one org and
one asset id. Same image as the workers — it needs ffmpeg and nothing else.

## Phase 6a — the preview queue

The only piece of infrastructure the preview fleet needs beyond a task
definition.

* **One SQS queue**, `mishne-<env>-previews`, plus a dead-letter queue at
  `maxReceiveCount` matching `preview_max_attempts`.
* **Visibility timeout longer than the longest encode.** A three-hour master is
  around ten minutes at `-preset superfast`; 30 minutes is the safe setting.
  Too short and the same asset is handed to a second worker while the first is
  still going — the DB claim catches that and makes it a wasted receive rather
  than duplicated work, but the receive is still wasted.
* **`preview_lease_seconds` is the backstop, not the timeout.** The queue's
  visibility timeout protects against slow work; the lease protects against a
  worker that will never come back at all, which the queue cannot see. Set the
  lease comfortably above the visibility timeout.
* Producer permission (`sqs:SendMessage`) goes to the API task role — `probe`
  publishes. Consumer permission (`ReceiveMessage`, `DeleteMessage`) goes to the
  preview fleet's role, and to nothing else.
* Config: `PREVIEW_DISPATCH=sqs`, `PREVIEW_QUEUE_URL=...`. `Settings` refuses to
  boot with the first and not the second.

**Do not put previews in the state machine.** Phase 7's machine is generated from
the step registry and a preview is deliberately not a step (ADR-0020). Adding one
re-couples the transcript's latency to the transcode's.

**Scale on queue depth, not CPU.** The fleet is at 100% CPU whenever it is doing
anything at all, so CPU is not a signal — it is the steady state.
`ApproximateNumberOfMessagesVisible` is the signal, and scale-in must respect the
in-flight encode: ECS task protection, or a drain that lets `proxyrunner` finish
the preview it is on. It handles SIGTERM that way already.

## Phase 7 — orchestration

`infra/statemachine.json` is **generated** from the step registry, and
`tests/test_statemachine.py` fails if the checked-in file has drifted:

```bash
cd apps/api && .venv/bin/python -m mishne.orchestration.statemachine > ../../infra/statemachine.json
```

Terraform fills the `${worker_task_arn}` placeholder and creates the state
machine, its execution role, and per-step CloudWatch log groups. Step Functions
passes **references, never payloads** — there is a 256 KB state-size limit and a
transcript exceeds it. That contract is already what the steps obey.

`POST /v1/jobs` starts an execution instead of writing `queued` and hoping;
`orchestration/devrunner.py` is local-only by construction and does not deploy.
Keep it — it is what makes a laptop useful — but make sure nothing outside
`environment=local` can start it.

## Phase 8 — the front doors, and the one that must not be a front door

- **The customer API** behind an ALB, HTTPS only, ACM certificate,
  `api.mishne.ai`. The CORS allowlist names the web origin exactly; credentialed
  CORS cannot use a wildcard.
- **The web app** at `app.mishne.ai`. Next 15 on Fargate behind the same ALB is
  the fewest moving parts; Amplify or Vercel is fewer still if you would rather
  not operate it. Either way the session cookie's scope, the API's CORS
  allowlist and the buckets' CORS origin are three places that must name the
  same origin, and a mismatch shows up in a browser console as a missing header
  and nowhere else.
- **The back-office is not on the internet.** It binds to loopback and refuses
  anything else unless `ADMIN_ALLOW_PUBLIC_BIND` is on, and that flag should
  stay off in AWS. Reach it over SSM port-forwarding into the task, or a VPN.
  If it ever does get an ingress, it needs its own domain, its own certificate,
  SSO with MFA in front of it and an IP allowlist — and it still holds the one
  connection in the system that bypasses row-level security, so the honest
  answer for a long while is a port-forward and a bookmark.

That BYPASSRLS role has to be **created deliberately in RDS**: BYPASSRLS is a
role attribute and attributes are not inherited through membership. The admin
process asserts at startup that it really is exempt, because a role without the
exemption produces a back-office that comes up, signs you in, and shows an empty
list of organisations — which reads as "no customers" rather than
"misconfigured".

## Phase 9 — CI, and what it is allowed to do

GitHub Actions, OIDC role, no keys:

1. `pytest` against a Postgres service container, plus `npm run typecheck` and
   `npm run build`.
2. A check that `statemachine.json` matches the registry (the test already does
   this; make the pipeline fail on it).
3. Build and push images on a merge to the branch.
4. Deploy to staging: migrations task first, then services.

Production later gets the same pipeline with a manual approval between the
migration and the deploy.

## Phase 10 — proving it, before trusting it

The same click-through as the laptop, against staging, in this order — each step
exercises exactly one thing the local stack could not prove:

1. Sign up the first owner with `PUBLIC_SIGNUP=true`, then turn it off.
2. Upload a large file from the browser. **This is the real test of the CORS
   rules**, which MinIO never applied.
3. Watch a job run through Step Functions, with per-step progress in the UI.
4. Kill the heavy task mid-run and confirm the retry re-runs the step and
   performs zero transcription.
5. Download the AAF through the presigned URL, and open it in Media Composer.
6. Grant credits from the back-office over a port-forward, and confirm the line
   appears on the customer's billing screen.
7. Two organisations, and one of them tries to read the other's project by id.
   RLS is the backstop; check it is doing something in the deployment, not only
   in the test suite.

## What this costs, roughly

Staging, idle most of the time: RDS `t4g.medium` single-AZ, one NAT gateway, an
ALB, a small Fargate API and a spot instance that only exists while a job runs.
Order of a few hundred dollars a month, and NAT plus the ALB are most of it
before a single job runs. If that is the wrong number for this stage, the two
economies that do not compromise anything are removing the NAT gateway (VPC
endpoints for everything the tasks need) and running the web app somewhere that
is free until it is not.

## Deliberately not in this plan

- **Production.** A copy of these modules with different values, when there is a
  customer.
- **Multi-region, read replicas, autoscaling policies beyond a floor and a
  ceiling.** No load to shape them to.
- **KMS per-bucket customer-managed keys.** Phase 3 says why: it is a change
  with a browser-visible failure mode and deserves its own pass.
- **Retention and deletion.** That is C4, and it opens with a policy decision
  rather than code: `credit_ledger` and `audit_log` refuse deletion by trigger,
  deliberately, so "delete everything about this customer" cannot mean what it
  says.
