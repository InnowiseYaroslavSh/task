# Architecture Specification

### [TASK-001] Epic-01 Identity and Authentication (2026-10-20)

**Package layout:** `app/core` for configuration, errors, security, database, and dependencies;
`app/models` for SQLAlchemy entities; `app/repositories` for SQL; `app/services` for transactions
and domain rules; `app/schemas.py` for shared validated DTOs; `app/api/v1` for HTTP re-exports and
thin FastAPI routers; `tests` for unit/API coverage.

**Pattern:** layered handler → service → repository. Services own transaction boundaries and return
domain/ORM objects; routes serialize through Pydantic response models. Tenant-scoped repository methods
require an explicit trainer id.

**Persistence:** async SQLAlchemy 2.0 targets PostgreSQL 16 and supports SQLite for local tests.
Identity tables use restrictive foreign keys, unique constraints for email/slug/link ownership, and
indexes for role/status, tenant links, refresh families, and approval expiry. Production schema
changes use Alembic; table auto-creation is opt-in for tests only.

**Authentication:** Argon2id password hashes; 15-minute JWT access tokens with `sub`, `role`,
`trainer_id`, `act`, and `jti`; opaque 256-bit refresh tokens stored only as SHA-256 hashes and
rotated per use. Current-principal re-reads user/session status on every request.

**Security:** structured domain errors, Pydantic `extra="forbid"`, constant-time missing-user login
path, hashed tokens/IP metadata, admin-route blocking during impersonation, tenant 404 isolation,
and sanitization of SVG branding.

**Background work:** job payloads are represented by a queue protocol and in-process adapter. Jobs are
queued after successful service transactions and carry ids/idempotency keys so ARQ can replace the
adapter without changing domain code.

**Scalability:** stateless access tokens, database indexes, atomic link/counter transitions, and a
replaceable clock/id factory. Redis rate limiting and ARQ are deployment adapters rather than business
logic dependencies.
