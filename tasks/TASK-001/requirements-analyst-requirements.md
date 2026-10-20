# Epic-01 User Management and Authentication Requirements

## Overview

Implement the root identity and tenancy epic for the FastAPI backend. The system must authenticate
users, enforce the four-role permission matrix, isolate trainer tenants, manage player/parent/coach
relationships, support availability and approval workflows, provide admin impersonation and GDPR
erasure, and expose trainer branding.

## Source

`Task/Epics/Epic-01_User_Management_Authentication_SPEC.md`, together with
`Task/Epics/00_Backend_Conventions.md` and `specs/MANIFEST.md`.

## Functional Requirements

1. FR-001 Authentication and sessions — register through a valid ShareLink, login with Argon2id,
   rotate opaque refresh tokens, detect reuse, logout families, reset passwords, and verify email.
2. FR-002 Authorization — expose current-principal, role, impersonation, and tenant-scope checks;
   return 404 for cross-tenant objects and 403 for forbidden capabilities.
3. FR-003 Trainer administration — super admins provision, list, edit, deactivate/reactivate, and
   erase users; each state-changing admin action is audited.
4. FR-004 Player and parent graph — create adult and dependent player profiles, maintain trainer
   links, enforce ownership, support child logins, and prevent child ShareLink redemption.
5. FR-005 ShareLinks — issue player-static and coach-unique links, preview validity, atomically
   consume links, validate target email/kind, and record analytics.
6. FR-006 Purchase approvals — create and resolve parent approvals with expiry and idempotency.
7. FR-007 Availability — replace full weekly windows, reject invalid/overlapping windows, and expose
   conflict checks to event scheduling consumers.
8. FR-008 Profile and branding — patch allowed profile fields, provide safe two-phase image uploads,
   change email through re-verification, and expose a restricted public branding response.

## Non-Functional Requirements

- NFR-001: async FastAPI + SQLAlchemy 2.0 with PostgreSQL 16 as production target.
- NFR-002: Pydantic v2 boundary validation with `extra="forbid"`; one structured error envelope.
- NFR-003: short-lived JWT access tokens and SHA-256-hashed 256-bit rotating refresh tokens.
- NFR-004: tenant-scoped repository methods require a trainer id; no cross-tenant aggregation.
- NFR-005: service-layer tests, API tests, lint, format, and mypy must run through uv.
- NFR-006: state changes are transactional; jobs are queued after commit and safe to retry.

## Business Rules

- BR-001: trainer accounts are created only by super admins; users have exactly one role.
- BR-002: login failures are indistinguishable and perform dummy password verification.
- BR-003: coach-unique links are single-use, seven days, and bind to the target email.
- BR-004: children cannot redeem links or complete purchases; dependent purchases require approval.
- BR-005: impersonation cannot target super admins, lasts at most 60 minutes, and blocks admin routes.
- BR-006: erasure anonymizes in place, preserves foreign keys/history, and is irreversible.
- BR-007: availability replacement is atomic and rejects `start >= end` and overlap.

## Task Breakdown

### Models and persistence

Implement identity, profile, tenant-link, ShareLink, availability, approval, impersonation, refresh,
one-time-token, audit, and erasure models with restrictive foreign keys, unique constraints, indexes,
and a migration/bootstrap path.

### Services and repositories

Implement repositories with required tenant arguments; services for auth/session, users/profiles,
ShareLinks, availability, approvals, impersonation, audit, and jobs. Services own transaction
boundaries and never import HTTP schemas.

### API surface

Implement all routes listed in Epic-01 §8 under `/api/v1`, including auth, me, trainer, public, and
admin routers with typed request/response schemas and generated OpenAPI metadata.

### Testing

Cover password/token primitives, availability boundary rules, role and tenant isolation, provisioning,
ShareLink registration and reuse, dependents, approvals, impersonation, profile/branding validation,
deactivation, and GDPR erasure. Add concurrency-focused service tests where SQLite permits and keep
PostgreSQL migration compatibility explicit.

## Gap Analysis

- The repository started without application code, dependency metadata, migrations, or tests.
- UUIDv7 is not in the Python 3.12 standard library; the ID factory is isolated so deployment can
  replace UUID4 with UUIDv7 without changing models or API contracts.
- The Epic leaves the transactional email provider and queue implementation abstract; this slice uses
  protocols and an in-process queue adapter, with payload contracts ready for ARQ.
- Open questions are resolved per the specification: password verification is required for purchases
  and ShareLink redemption but not login; refresh tokens default to 30 days.
