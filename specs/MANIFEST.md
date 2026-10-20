# Project: Accelerator Core API

FastAPI backend for a multi-tenant training platform. Epic-01 provides the identity, authentication,
authorization, profile, ShareLink, availability, approval, impersonation, erasure, and branding
foundation consumed by the remaining epics.

## Specs Index

| File | Purpose | Depends On | Last Updated |
|------|---------|------------|--------------|
| architect-architecture.md | System design, components, data flow | - | 2026-10-20 |
| api-designer-spec.md | Endpoints, schemas, authentication | architect-architecture | 2026-10-20 |
| docs-generator-implementation.md | Build process, deployment, tooling | - | - |

## Key Decisions

- Layered FastAPI → service → repository architecture with async SQLAlchemy.
- PostgreSQL is the production source of truth; SQLite is supported for local tests.
- Epic-01 identity is the tenancy boundary; cross-tenant misses are 404.

## Tech Stack

Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2.0 · PostgreSQL 16 · Redis/ARQ adapters ·
Argon2id · JWT · uv · ruff · mypy · pytest

---

*This manifest is updated automatically by architect, api-designer, and docs-generator skills.*
*See `../spec-desc.md` for specification structure guidelines.*
