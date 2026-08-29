# B4 — session prompt

Requires B1 (persistence). Can run in parallel with B2.

```
We are working on mishne.ai. This session has one job: workstream B4 —
accounts, organisations, and tenant isolation that holds at the database level
rather than in application code.

Read these three files first, in this order, and nothing else until you have:
  docs/HANDOVER.md                     — what exists, how to run it, the traps
  docs/roadmap/B4-auth-and-tenancy.md  — this workstream's brief
  docs/architecture/04-security.md     — the security design

Do not re-derive the architecture or read the whole codebase. The handover is
accurate and current; trust it.

## Scope

B4 only. B1 (persistence) is done and you build on it — specifically, B1 keyed
its RLS policies on a session variable and left wiring real identity into that
variable to you. That wiring is the centre of this workstream.

Storage is B2 and orchestration is B3; billing is C1. Do not start them.

Phase A (selection corpus, Avid acceptance) is deferred by decision. Nothing
here depends on it.

## What already exists — do not rebuild it

  apps/web/src/app/login/page.tsx       the flows as mockups
  apps/web/src/app/signup/page.tsx      including tier selection at signup
  apps/web/src/components/signup-flow.tsx
  src/mishne/mock.py                    mock.ORG — the single fake org
                                        everything currently assumes
  docs/architecture/03-platform-and-data.md
                                        orgs, users with role, audit_log, and
                                        org_id on every table
  B1's migration                        RLS policies already in place, keyed on
                                        a session variable nothing sets yet

## What to build

1. Authentication. The design deliberately leaves the provider open; a hosted
   identity provider is the cheap correct answer for an MVP.
2. Orgs, membership, roles. The signup flow already collects a tier.
3. Session handling in the Next.js app, and authorisation on every API route.
4. RLS enforcement: the request's org_id is set on the database session, and
   the policies do the isolation. Application code must not be the only thing
   standing between two tenants.
5. The audit log. The table is in the schema design and nothing writes to it.

## Decisions I have already made — do not relitigate

- org_id on every table, including where a join could derive it. Uniform
  policies, and no path where a forgotten join condition leaks.
- Tier is chosen at signup and credits are bought in packs, which shapes the
  org record. (ADR-0006)
- Isolation is enforced by the database, not by the ORM and not by route
  handlers. Those are defence in depth, not the mechanism.

## Decisions still open — raise them, do not quietly pick one

- Which identity provider.
- SSO, which larger broadcast customers will ask for.
- Whether projects can be shared ACROSS orgs — relevant for a production house
  working with an agency, and it changes the isolation model significantly. Do
  not design for it speculatively, but tell me if a choice here forecloses it.

## Traps

- Media is the customer's INTELLECTUAL PROPERTY, often under embargo. A
  cross-tenant leak here is not a privacy incident, it is a broadcast incident.
  That is the reason isolation lives in the database.
- Presigned S3 URLs bypass the API entirely. Scope them to the object and keep
  the TTL short — presign_ttl_seconds is 900.
- Do not let use_mocks=True be reachable in an environment with real data.
  Consider refusing to start if both are set.
- Sessions and auth changes are deployed while the previous release is still
  running. A session format change that the old code cannot read logs everyone
  out mid-deploy. Same expand/contract rule as the schema. (ADR-0012)
- Setting the org_id session variable must be reliable per request, including
  on pooled connections. A connection reused without resetting it is a
  cross-tenant leak with no error message — this is the single most dangerous
  thing in this workstream.

## Definition of done

- Signup, login, logout, and an org with more than one member all work.
- Every API route rejects an unauthenticated request and an out-of-org
  resource.
- A test proves isolation AT THE DATABASE LEVEL, not just at the route — and a
  second test proves a pooled connection cannot carry one request's org_id
  into the next.
- The audit log is written for the actions the security doc lists.
- The existing tests still pass.

## Environment

  docker compose -f infra/docker-compose.yml up -d   local Postgres
  cd apps/api && ./setup.sh                          venv, checks

Start by showing me how org_id gets onto the database session for a request,
and how it is guaranteed to be cleared when the connection returns to the pool.
Everything else in this workstream is ordinary; that part is where the leak
would be.
```
