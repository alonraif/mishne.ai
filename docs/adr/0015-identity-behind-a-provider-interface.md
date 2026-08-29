# ADR-0015 — Identity behind a provider interface, and one email is one person

**Status:** Accepted · **Date:** 2026-08-29

## Context

B1 put row-level security on every table, keyed on a session variable, and left
"what sets that variable" to B4. Two decisions had to be made to close it.

**Which identity provider.** `docs/architecture/04-security.md` names WorkOS,
because SAML SSO and SCIM directory sync come up in the first procurement
conversation with any broadcast buyer and building them in-house is weeks of
work with a long tail of provider-specific quirks. But a hosted provider on the
critical path of the test suite means every test needs a vendor account, a
network, and a browser redirect.

**How a request establishes a tenant.** The policies read `app.org_id`. A
request arrives holding an opaque session token. Reading the session row to
learn the org requires seeing a row; seeing a row requires already knowing the
org.

## Decision

**Authentication sits behind a provider interface**, the same shape as ASR
(ADR-0003) and LLM routing (ADR-0011). A provider answers exactly one question
— *who is this?* — and returns an `ExternalIdentity`. It does not create users,
create orgs, or issue sessions.

- `LocalProvider` — email and password in our own database, scrypt from the
  standard library. What a developer's machine and the whole test suite run on,
  and a real path for a single-editor customer who will never do SSO.
- `WorkOSProvider` — the redirect flow, for organisations that want it.

**Sessions are ours either way.** An opaque 256-bit token in an httpOnly
cookie; only its SHA-256 is stored, so a dump of the sessions table is a list of
session ids rather than a set of working credentials.

**The tenant is established through two narrow policy escapes**, not by
bypassing RLS. Migration 0003 widens three policies by one clause each, and each
clause is keyed on something the caller has already presented:

| table | may also be read when |
|---|---|
| `sessions` | `token_hash = app.session_token` |
| `users` | `lower(email) = app.login_email` |
| `user_credentials` | `user_id = app.login_user` |

Every one is set with `is_local => true`, so it lives for one transaction. A
request can therefore see the session row for the token it holds and nothing
else; a login can see the one user it is signing in as. `WITH CHECK` stays
org-only on all three: none of these may be used to write.

**One email address is one person**, enforced by a unique index on
`lower(email)` across all orgs.

## Rationale

- **A BYPASSRLS role for the auth path** would put a credential in the system
  whose purpose is to ignore tenancy. A `SECURITY DEFINER` function is exempt
  only while `FORCE ROW LEVEL SECURITY` is off, and turning that off is the
  mistake 0001 exists to prevent. A policy that opens up when `app.org_id` is
  unset makes "forgot to set the org" a scan of every tenant instead of an empty
  result.
- **The escapes are narrower than the org policy, not wider.** A leaked org id
  reads nothing through them.
- **The local provider is not a stub.** A test suite that cannot sign in without
  a vendor is a test suite people skip.
- **Global email uniqueness** is what makes a login by email unambiguous. It is
  a tightening, which expand/contract forbids — permissible exactly here because
  authentication did not previously exist, so no row can violate it.

## Consequences

- **One person cannot be in two organisations.** A freelancer working for two
  production houses needs two addresses today. Supporting it means a
  `memberships` table and dropping the unique index — a contract step with its
  own release, and the reason `org_id` is a column on every table rather than a
  join away.
- **SSO signs in a user who already exists.** An identity provider asserts that
  someone controls an email address; turning that into membership of a
  customer's organisation is a decision an owner makes. SCIM is the supported
  way to automate it and is not built.
- **Roles stay at three.** `owner`, `member`, `viewer`
  (docs/architecture/04-security.md). Per-project ACLs are the first thing
  enterprises ask for and the first thing that makes a permission model hard;
  the schema can carry `project_members` later without a painful migration.
- **Cross-org project sharing remains open**, and nothing here forecloses it: a
  shared project would be a membership row, not a second org_id on a table.
- **The session cookie is the credential for every request**, so `Settings`
  refuses to boot outside `local` with a plain-http `app_origin`, and
  credentialed CORS names its origins rather than using a wildcard.
- **A pooled connection must never carry one request's org into the next.**
  `tests/test_pool_isolation.py` runs the API against a pool of exactly one
  connection and proves it does not; with a normal pool the second request would
  usually get a different connection and pass either way.
