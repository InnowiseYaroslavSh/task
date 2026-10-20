# API Specification

### [TASK-001] Epic-01 Identity API (2026-10-20)

All routes use the `/api/v1` prefix, typed Pydantic v2 request/response models, and the shared error
shape `{ "error": { "code": str, "message": str, "details": object } }`. Authenticated tenant
routes derive `trainer_id` from the access token; a missing object in another tenant is always 404.

## Authentication

- `POST /auth/register?share_code=` accepts email, password, names, and optional owned profile ids;
  returns 201 for a new player or 200 for an existing authenticated link.
- `POST /auth/setup` consumes the single-use 24-hour trainer invitation token and activates the
  provisioned account with a forced-password-change completion.
- `POST /auth/login` accepts email/password and returns access + rotating refresh tokens; every failure
  is `401 INVALID_CREDENTIALS`.
- `POST /auth/refresh`, `/auth/logout`, password-reset, email verification, and impersonation exit
  implement the session lifecycle and return typed token/status responses.

## Identity and tenancy

- `GET/PATCH /me`, avatar prepare/confirm, availability replacement, contexts, dependent CRUD, and
  child trainer-link operations expose only caller-owned or linked data. Parents can create a child
  login once through `POST /me/players/{id}/login`; credentials are stored on a separate player user.
- `GET/POST /trainer/share-links`, trainer availability reads, and branding updates derive tenant
  ownership from the principal.
- `GET /public/share-links/{code}` and `GET /public/trainers/{slug}/branding` are unauthenticated
  and intentionally expose preview/public branding fields only.

## Administration

- `GET /admin/users` supports cursor pagination and role/status/trainer/query filters.
- Trainer provisioning, user patch, deactivate/reactivate, erase, and impersonate require a
  non-impersonated `super_admin` principal.

## Validation and status codes

Pydantic rejects unknown fields and invalid email/password/colour/date/time values with 422. Domain
errors map to 401, 403, 404, 409, 429, or 202 according to the Epic-01 acceptance criteria. OpenAPI
is generated directly from route signatures and response models.
