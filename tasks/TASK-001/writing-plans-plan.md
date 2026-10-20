# Epic-01 Identity and Authentication Implementation Plan

**Task:** TASK-001

> **For Claude:** Use the `coder` skill to implement in the current requested branch.

**Goal:** Deliver a runnable, tested Epic-01 identity/authentication service from the supplied spec.

**Architecture:** Async FastAPI routes delegate to services; services own transactions and call
tenant-aware repositories. Shared dependencies enforce current-principal, role, tenant, and
impersonation rules.

**Tech Stack:** Python 3.12, uv, FastAPI, Pydantic v2, async SQLAlchemy, PostgreSQL/SQLite, Argon2,
PyJWT, pytest, ruff, mypy.

---

### Task 1: Bootstrap and cross-cutting infrastructure

Create `pyproject.toml`, application factory, settings, async database/session support, declarative
base, domain errors, structured error handling, clock/id factories, security helpers, and job queue
protocol. Add a local SQLite test configuration and PostgreSQL-compatible URL handling.

### Task 2: Identity persistence and repositories

Create SQLAlchemy identity models for users, profiles, tenant profiles/links, ShareLinks, availability,
approvals, sessions/tokens, audit, and erasure. Add repository methods for tenant-scoped reads,
atomic link/session transitions, and user listing.

### Task 3: Authentication and authorization services

Implement Argon2 password policy, login dummy verification, registration, JWT issuance/validation,
refresh rotation/reuse detection, logout, email verification/password reset, current-principal, role
dependencies, rate-limit boundaries, and impersonation lifecycle.

### Task 4: Player, coach, availability, and approval services

Implement adult/dependent profiles, trainer associations, coach invitation/redemption, child login
constraints, approval transitions, availability replacement/conflict evaluation, and profile updates.

### Task 5: API routers and schemas

Add auth, admin, me, trainer, public routers and strict Pydantic request/response models. Register all
routes with summaries and response codes so OpenAPI includes the Epic-01 surface.

### Task 6: Verification and quality gates

Add unit/API tests for major happy paths, security failures, tenant isolation, refresh reuse,
ShareLinks, availability boundaries, branding sanitization, and erasure. Run `uv run pytest`, `ruff
check`, `ruff format --check`, and `mypy`; fix failures before completion.
