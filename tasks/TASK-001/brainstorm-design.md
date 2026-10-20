# Epic-01 Identity and Authentication Design

## Problem Statement

The repository contains the Epic-01 contract but no runnable backend. The first implementation must
establish identity, authentication, tenant isolation, role enforcement, player/parent relationships,
coach invitations, availability, impersonation, auditability, and privacy-preserving erasure without
leaking business logic into HTTP handlers.

## Proposed Solution

Build an async FastAPI service with SQLAlchemy 2.0 models and a service/repository boundary. PostgreSQL
is the production database; SQLite is supported for local tests. The API exposes the Epic-01 routes
under `/api/v1`, uses Pydantic v2 models with `extra="forbid"`, Argon2id password hashing, signed JWT
access tokens, opaque rotating refresh tokens, and a small idempotent in-process job queue abstraction.

## Architecture

`api/v1/*/routes.py` handles parsing, dependency injection, authorization, and serialization only.
`services/` owns transactions and domain rules. `repositories/` owns SQLAlchemy queries. `models/`
contains the identity schema. `core/` contains configuration, errors, security, pagination, clocks,
and shared middleware. A `Principal` dependency re-reads the user for every authenticated request so
deactivation and erasure immediately invalidate access tokens.

## Data Model

The initial schema includes users, profiles, trainer/coach/player profiles, trainer-player links,
share links and events, availability windows, purchase approvals, impersonation sessions, refresh
tokens, user erasures, audit events, and verification/password-reset tokens. Tenant-scoped queries
require a trainer id. Foreign keys use restrictive behavior; erasure anonymizes rows in place.

## API Design

The route surface follows Epic-01 section 8. Authentication routes issue `{access_token,
refresh_token, token_type, expires_in}`. Domain failures use the shared `{error: {code, message,
details}}` envelope. Tenant misses intentionally resolve to 404. Public branding and ShareLink preview
expose only the fields specified by the epic.

## Error Handling

Domain exceptions map centrally to status codes. Login failures use one `INVALID_CREDENTIALS` response
and perform dummy Argon2 verification for unknown emails. Refresh reuse revokes the complete family.
State transitions lock the aggregate where supported and rely on unique constraints for duplicate
links. Input validation occurs in Pydantic schemas; SVG branding is sanitized before persistence.

## Testing Strategy

Unit tests cover password policy, token rotation/reuse, availability boundaries, age validation, and
SVG sanitization. API tests use an isolated SQLite database and cover trainer provisioning, ShareLink
registration, role/tenant isolation, dependent management, impersonation, profile edits, branding,
deactivation, and erasure. PostgreSQL-specific deployment checks are kept in the migration and schema
configuration so the same services can run against PostgreSQL in CI.

## Security Considerations

Secrets are environment-configured, access tokens are short lived, refresh tokens are stored only as
SHA-256 hashes, PII is excluded from logs and audit payloads, admin routes are blocked for impersonated
principals, child accounts cannot redeem ShareLinks or manage purchases, and original emails are stored
only as hashes in the erasure ledger.

## Open Questions

- UUIDv7 is not available in the Python 3.12 standard library, so the implementation uses UUID values
  with a replaceable id factory; this can be switched to UUIDv7 when the deployment standardizes one.
- Background delivery is represented by a queue protocol and durable job records are left to the
  application-wide worker infrastructure.
