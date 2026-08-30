# infra

## What is here

| file | what it does |
|---|---|
| `docker-compose.yml` | local Postgres with pgvector, for development |
| `local-app-user.sql` | the non-superuser role the API connects as, so RLS applies |
| `s3_lifecycle.py` | per-bucket lifecycle rules, including the 7-day abort of incomplete multipart uploads |
| `s3_cors.py` | what the browser may do directly against the buckets — and `ExposeHeaders: ["ETag"]`, without which no upload can be completed |
| `statemachine.json` | **generated.** The Step Functions definition |

## The state machine is generated, not written

    cd apps/api && python -m mishne.orchestration.statemachine > ../infra/statemachine.json

It comes from `mishne.pipeline.steps.STEPS`, and `tests/test_statemachine.py`
fails if the checked-in file has drifted from what the registry produces. That
test exists because the registry and a hand-written machine are two descriptions
of the same pipeline, and B3 started by finding they had already diverged: the
registry listed a stage that did not exist and omitted four that did.

`${worker_task_arn}` is left as a placeholder for Terraform to fill.

## Still absent, deliberately

Terraform for the AWS estate. What it will need, per
[03 — Platform & Data](../docs/architecture/03-platform-and-data.md):

- Three S3 buckets (raw, derived, artifacts) with distinct KMS keys and access
  policies. The lifecycle and CORS configuration they need is in the two scripts
  above rather than in HCL, so it can be applied and tested against MinIO or
  moto without an AWS account.
- RDS Postgres Multi-AZ with RLS enabled, and two roles: the owner for
  migrations, and the application role from `local-app-user.sql`.
- ECS Fargate for the API and light workers; ECS on EC2 spot with EBS for the
  heavy tier. Fargate's 200 GB ephemeral cap is not enough — a worker's disk
  must hold the largest asset it may be handed plus its extracted audio plus,
  for an AAF with embedded essence, the essence written out beside it
  (ADR-0013), and the task needs 8 GB of memory or large-v3 is an OOM kill with
  no useful message.
- The Step Functions machine from `statemachine.json`, and an S3 event
  notification on the raw bucket calling `mishne.probe.handle_s3_event`.
- Separate AWS accounts per environment: staging and production only.
  Development runs against staging; schema iteration runs against the local
  Postgres above. See ADR-0012.
