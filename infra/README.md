# infra

`docker-compose.yml` — local Postgres with pgvector, for development.

Terraform for the AWS estate is deliberately absent until Phase 1. What it will
need, per [03 — Platform & Data](../docs/architecture/03-platform-and-data.md):

- Three S3 buckets (raw, derived, artifacts) with distinct lifecycle rules,
  KMS keys and access policies, plus a rule aborting incomplete multipart
  uploads after 7 days
- RDS Postgres Multi-AZ with RLS enabled
- ECS Fargate for the API and light workers; ECS on EC2 spot with EBS for the
  heavy tier (ffmpeg, AAF demux — Fargate's 200 GB ephemeral cap is not enough)
- Step Functions state machine generated from `pipeline.steps.STEPS`
- Separate AWS accounts per environment: dev, staging, prod
