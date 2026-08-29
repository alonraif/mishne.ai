# B4 — Accounts, orgs, and tenant isolation

> Thread starter. Read [../HANDOVER.md](../HANDOVER.md) first; you should not
> need any other file.

## Goal

Real accounts, real organisations, and isolation that holds at the database
level rather than in application code.

## What already exists

- `apps/web/src/app/login/page.tsx`, `apps/web/src/app/signup/page.tsx`,
  and `apps/web/src/components/signup-flow.tsx` — the flows as mockups, including tier selection
  at signup.
- The schema design has `orgs`, `users` with `role`, and `org_id` on every table
  ([../architecture/03-platform-and-data.md](../architecture/03-platform-and-data.md)).
- [../architecture/04-security.md](../architecture/04-security.md) — the security
  design.
- `mock.ORG` — the single fake org everything currently assumes.

## What to build

1. Authentication. The design leaves the provider open; a hosted identity
   provider is the cheap correct answer for an MVP.
2. Orgs, membership, roles. The signup flow already collects a tier.
3. Session handling in the Next.js app, and API authorisation on every route.
4. **RLS enforcement**: the request's `org_id` is set on the database session,
   and policies do the isolation. Application code must not be the only thing
   between two tenants.
5. An audit log — the table is in the schema design and nothing writes to it.

## Decisions already made

- `org_id` is on every table, including where a join could derive it. Uniform
  policies, no path where a forgotten join condition leaks.
- Tier is chosen at signup and credits are bought in packs (ADR-0006) — that
  shapes the org record.

## Decisions still open

- Identity provider.
- SSO for larger customers, which broadcast customers will ask for.
- Whether projects can be shared across orgs — relevant for a production house
  working with an agency, and it changes the isolation model significantly.

## Traps

- **Media is the customer's intellectual property**, often under embargo. A
  cross-tenant leak here is not a data-privacy incident, it is a broadcast
  incident. This is why isolation must be at the database level.
- Presigned S3 URLs bypass the API entirely. Scope them to the object and keep
  the TTL short; `presign_ttl_seconds` is 900.
- Do not let `use_mocks=True` reach an environment that has real data. Consider
  refusing to start if both are set.

## Definition of done

- Signup, login, logout, and an org with more than one member all work.
- Every API route rejects an unauthenticated request and an out-of-org resource.
- A test proves isolation at the database level, not just at the route.
- Audit log written for the actions the security doc lists.
