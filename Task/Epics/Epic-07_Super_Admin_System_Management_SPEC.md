# Epic-07 (Python Backend): Super Admin & System Management

> Stack, layering, error contract, pagination, tenancy and testing rules: [00_Backend_Conventions.md](00_Backend_Conventions.md).

**Priority**: P1 — operational necessity
**Complexity**: Medium (cross-tenant reads, audit integrity, flag propagation)
**User stories**: 9

---

## 1. Description

The platform-operator surface: operational metrics, tenant administration, per-tenant feature flags,
cross-tenant event and user tools, and the audit log.

This epic owns **two shared services every other epic depends on** — `AuditService` and
`FeatureFlagService` — plus the cross-tenant read endpoints that bypass the isolation rule of
Conventions §3. Every such bypass is explicit, marked with `SuperAdminScope`, and audited.

Financial reporting is **not** in scope. Stripe is the financial source of truth; this epic links out
to it and never reproduces revenue, payout, tax, or dispute data.

## 2. Backend outcomes

- An append-only, tamper-evident audit log that every epic writes to through one interface.
- A feature-flag service with a cached read path fast enough to sit in every request.
- Operational dashboards that read rollups, never live tables.
- A `SuperAdminScope` marker that makes each isolation bypass visible in code review.

---

## 3. In scope (MVP)

### Dashboard & metrics
- [ ] Operational metrics: user counts, growth, session and RSVP counts, attendance and no-show rates
- [ ] Most active trainers; top players platform-wide
- [ ] Date presets: 7 / 30 / 90 days
- [ ] A link out to Stripe; **no financial figures served from this platform**

### Tenant administration
- [ ] Trainer listing with subscription status
- [ ] Trainer detail and edit
- [ ] Deactivate / reactivate a trainer
- [ ] Create a trainer (delegates to Epic-01 US-01.01)

### Configuration
- [ ] Per-trainer feature flags: `lppp`, `marketing`, `camps`
- [ ] Per-trainer pricing (delegates to Epic-05 US-05.10)

### Tools
- [ ] Event Master — cross-tenant event query and override (delegates to Epic-02 US-02.15)
- [ ] Users tool — cross-tenant user query (delegates to Epic-01)
- [ ] Impersonation (delegates to Epic-01 US-01.07)
- [ ] Audit log: query, filter, export

---

## 4. Out of scope (Post-MVP)

Financial metrics on-platform · infrastructure health monitoring (that belongs to the observability
stack, not the API) · bulk operations · custom report builder · a role editor · content moderation ·
campaign creation · onboarding workflows · platform-wide announcements · backup/restore endpoints ·
Super-Admin-managed portal branding (trainers own their own; impersonation covers support cases).

---

## 5. Dependencies

**Required before**: Epic-01 (users, impersonation), Epic-02 (events), Epic-05 (Stripe status).

**Blocks**: nothing hard, but `AuditService` and `FeatureFlagService` are consumed by Epics 01–06 and
should land early — build them in the Epic-01 slice and fill in the admin surface here.

**Provides**
```python
class AuditService(Protocol):
    async def record(self, *, action: AuditAction, actor_user_id: UUID,
                     subject_type: str, subject_id: UUID | None,
                     trainer_id: UUID | None, details: Mapping[str, JSONValue],
                     request_id: str) -> None: ...

class FeatureFlagService(Protocol):
    async def is_enabled(self, feature: Feature, trainer_id: UUID) -> bool: ...
    async def enabled_map(self, trainer_id: UUID) -> dict[Feature, bool]: ...
```

---

## 6. Roles

Only `super_admin` reaches any route in this epic. Every other role receives `403` — asserted once per
route by a parametrised test.

`FeatureFlagService` is the exception: it is called by every epic on behalf of any role.

---

## 7. Domain model

`app/models/admin.py`.

### `audit_log` — append-only
| Column | Type | Notes |
|:---|:---|:---|
| `id` | UUID | PK (v7 — chronological by construction) |
| `occurred_at` | timestamptz | not null, default `now()` |
| `action` | enum `audit_action` | closed set, §10 |
| `actor_user_id` | UUID FK | who did it |
| `impersonated_user_id` | UUID FK | nullable — set when the actor was impersonating |
| `subject_type` | text | `user`, `trainer`, `event`, `coupon`, `flag`, `pricing`, … |
| `subject_id` | UUID | nullable |
| `trainer_id` | UUID FK | nullable — the affected tenant |
| `details` | JSONB | action-specific; **must not contain PII** |
| `request_id` | text | correlation id, ties to structured logs |
| `ip_hash` | text | salted SHA-256 |

Indexes: `ix_audit_time (occurred_at DESC)` · `ix_audit_actor (actor_user_id, occurred_at DESC)` ·
`ix_audit_subject (subject_type, subject_id)` · `ix_audit_trainer (trainer_id, occurred_at DESC)` ·
GIN on `details`.

**Append-only, enforced by a database trigger** that raises on `UPDATE` and `DELETE`. The application
role has `INSERT, SELECT` only on this table. A retention job runs as a separate role. A test asserts
the trigger fires and that the grant set is what it claims to be.

Partitioned monthly by `occurred_at` (`PARTITION BY RANGE`) — retention then drops a partition instead
of deleting 100 000 rows.

### `feature_flags`
`trainer_id` · `feature` enum (`lppp`/`marketing`/`camps`) · PK on the pair ·
`is_enabled` bool not null default true · `updated_by_user_id` · `updated_at`.

Absence of a row means **enabled** — new tenants get every feature without a provisioning step, and
the table only ever records deviations from the default.

### `platform_daily_metrics`
`metric_date` PK · `total_trainers` · `active_trainers` · `total_players` · `total_coaches` ·
`new_users` · `events_count` · `rsvp_count` · `attended_count` · `no_show_count` · `computed_at`.

Written nightly by `rollup_platform_metrics`, plus an hourly refresh of the current day. The dashboard
sums this table; it never touches `users`, `events`, or `rsvps`.

### `admin_settings` — singleton
`id` (fixed UUID, `CHECK`) · `audit_retention_days` int default 400 ·
`dashboard_cache_seconds` int default 300 · `updated_by_user_id` · `updated_at`.

400 days rather than 365 so a full year is always fully retrievable including the current partition.

---

## 8. API surface

| Method | Path |
|:---|:---|
| GET | `/api/v1/admin/dashboard?period=7d\|30d\|90d` |
| GET | `/api/v1/admin/dashboard/top-trainers` |
| GET | `/api/v1/admin/dashboard/top-players` |
| GET | `/api/v1/admin/trainers` |
| GET/PATCH | `/api/v1/admin/trainers/{id}` |
| POST | `/api/v1/admin/trainers/{id}/deactivate` \| `/reactivate` |
| GET/PUT | `/api/v1/admin/trainers/{id}/features` |
| GET | `/api/v1/admin/audit-log` |
| GET | `/api/v1/admin/audit-log/export` |
| GET | `/api/v1/admin/stripe/dashboard-link` |
| GET | `/api/v1/admin/trainers/{id}/stripe/dashboard-link` |
| GET | `/api/v1/features` (any authenticated role — own tenant's flags) |

Users, events, and pricing administration live in Epics 01, 02, and 05 respectively; this epic
documents them as delegations rather than duplicating their contracts (AGENTS.md: reference, don't duplicate).

---

## 9. User stories → backend tasks

### US-07.01 — Super Admin reads the operational dashboard

`GET /api/v1/admin/dashboard?period=30d`

**Acceptance criteria**
- [ ] Returns user metrics (`total_trainers`, `active_trainers`, `total_players`, `total_coaches`,
      `new_users_period`, `growth_series[]`), session metrics (`events_count`, `rsvp_count`,
      `attendance_rate`, `no_show_rate`), and previous-period comparisons
- [ ] Every figure is summed from `platform_daily_metrics` — a query-count test asserts the endpoint
      never touches `users`, `events`, or `rsvps`
- [ ] `active_trainers` counts `stripe_accounts.subscription_status = 'active'`
- [ ] `period` ∈ `7d`, `30d`, `90d` ⇒ else `422`; default `30d`
- [ ] Redis-cached for `admin_settings.dashboard_cache_seconds` (default 300), keyed by period
- [ ] The response contains **no monetary field at all**, and instead carries
      `stripe_dashboard_url`. A schema test asserts no field name matches `revenue|payout|earnings|amount`
- [ ] p95 < 2 s cold with 500 trainers, < 100 ms warm
- [ ] Any non-`super_admin` ⇒ `403`

### US-07.02 — Super Admin queries all users

`GET /api/v1/admin/users` — implemented in Epic-01, listed here for completeness of the admin surface.

**Acceptance criteria (this epic's additions)**
- [ ] Filters: `role`, `status`, `trainer_id`, `q` (name or email), registration date range
- [ ] Each row carries the associated trainer for coaches and players; a multi-tenant player appears
      once per link with the tenant named
- [ ] The route is marked `SuperAdminScope` — a test asserts the marker is present on every
      cross-tenant route in the codebase and that no un-marked route accepts a `trainer_id` filter
      it does not own
- [ ] Reading the directory is **not** audited (too voluminous, per §10); reading a single user's
      detail is also not audited; **every mutation is**

### US-07.03 — Super Admin edits a user profile

`PATCH /api/v1/admin/users/{id}` — Epic-01 endpoint.

**Acceptance criteria (this epic's additions)**
- [ ] Writes `audit_log` with `action='user.updated'` and a `details` diff of **field names only**,
      never values, so that no PII enters the audit body
- [ ] `role` is not patchable ⇒ `422 ROLE_IMMUTABLE`; a role change is a support operation performed
      through deliberate re-provisioning, not a field edit
- [ ] An email change forces re-verification and revokes refresh families
- [ ] An erased user (`status='deleted'`) ⇒ `409 USER_ERASED`

### US-07.04 — Super Admin creates a trainer

`POST /api/v1/admin/users/trainers` — Epic-01 US-01.01.

**Acceptance criteria (this epic's additions)**
- [ ] On creation, `feature_flags` rows are **not** written — absence means enabled (§7)
- [ ] Optionally accepts `disabled_features: []` to provision a restricted tenant in one call
- [ ] Writes `audit_log` `action='trainer.created'`
- [ ] Triggers Epic-05 Stripe Billing subscription creation as a background job; a Stripe failure
      leaves the trainer usable for free events and surfaces in `GET /admin/trainers` as
      `subscription_status='none'`

### US-07.05 — Super Admin configures per-trainer feature flags

`GET|PUT /api/v1/admin/trainers/{id}/features`

```json
{ "lppp": true, "marketing": false, "camps": true }
```

**Acceptance criteria**
- [ ] `PUT` replaces the full map; a missing feature key ⇒ `422` (no partial ambiguity)
- [ ] An unknown feature name ⇒ `422 UNKNOWN_FEATURE`
- [ ] Writes are upserts; setting a feature back to its default `true` still stores a row, so the
      audit trail shows the round trip
- [ ] The change takes effect **within 60 seconds** platform-wide: `FeatureFlagService` caches in
      Redis with a 60 s TTL and the write busts the key immediately, so the bound is a ceiling for
      other application instances, not the typical case
- [ ] Disabling a feature **never** deletes data. Re-enabling restores full visibility — this is a
      merge-gate test: create content, disable `lppp`, assert players see an empty library and the
      trainer gets `403`, re-enable, assert everything returns byte-identical
- [ ] Each toggle writes `audit_log` `action='feature.toggled'` with feature, old, new, tenant
- [ ] `GET /api/v1/features` returns the caller's own tenant flags for any authenticated role, cached,
      p95 < 50 ms

### US-07.06 — Super Admin uses the Event Master tool

`GET /api/v1/admin/events` — Epic-02 US-02.15.

**Acceptance criteria (this epic's additions)**
- [ ] Cross-tenant listing marked `SuperAdminScope`
- [ ] Filters: `q`, `trainer_id`, `location_id`, date range, `event_type`, `status`
- [ ] Sort by start, trainer, or fill ratio; cursor-paginated, `limit` max 200
- [ ] Editing or cancelling any event writes `audit_log` naming the affected tenant
- [ ] p95 < 2 s over 5 000 events

### US-07.07 — Super Admin overrides a scheduling conflict

Same endpoints as US-07.06, with conflict checks suppressed.

**Acceptance criteria**
- [ ] Coach-availability and double-booking conflicts are **not** raised for a `super_admin`; the
      write proceeds without an `override_reason`
- [ ] The suppressed conflict is nonetheless recorded: `audit_log` `action='conflict.overridden'`
      with `details = {conflict_type, coach_id, event_id, conflicting_event_id}`
- [ ] A trainer performing the same write still gets `409` without a reason (Epic-02 US-02.03) —
      a test asserts the two roles diverge exactly here and nowhere else
- [ ] Capacity is **not** overridable even for `super_admin`: `rsvp_count <= capacity` is a database
      `CHECK` and no role bypasses it. Attempting to seat past capacity ⇒ `409 EVENT_FULL`

### US-07.08 — Super Admin reads the audit log

`GET /api/v1/admin/audit-log?from=&to=&action=&actor_id=&subject_type=&subject_id=&trainer_id=`

**Acceptance criteria**
- [ ] Cursor-paginated over `(occurred_at DESC, id DESC)`, `limit` max 200
- [ ] Filters combine with AND; `from`/`to` are required to be within one year unless
      `?allow_wide_range=true`, which guards against an unbounded scan over a partitioned table
- [ ] Each row: timestamp, action, actor name and role, impersonated user when present, subject,
      tenant, details, request id
- [ ] `details` is returned verbatim; a test asserts no email, phone, or full name appears in any
      audit row produced by the whole test suite (a suite-wide PII scan over `audit_log`)
- [ ] `GET .../export` streams CSV for the filtered range via a server-side cursor
- [ ] Reading the audit log is itself **not** audited — otherwise the log grows by reading it
- [ ] `prune_audit_log` drops partitions older than `audit_retention_days`; the drop itself is logged
      to structured logs, not to `audit_log`
- [ ] Append-only proven: a direct `UPDATE` and a direct `DELETE` both raise

### US-07.09 — Super Admin links to Stripe

`GET /api/v1/admin/stripe/dashboard-link` · `GET /api/v1/admin/trainers/{id}/stripe/dashboard-link`

**Acceptance criteria**
- [ ] Returns a freshly minted Stripe login link each call; no URL is ever stored or cached
- [ ] A trainer without a connected account ⇒ `409 STRIPE_NOT_CONNECTED`
- [ ] Stripe unavailable ⇒ `502 STRIPE_UNAVAILABLE`
- [ ] Writes `audit_log` `action='stripe.dashboard_accessed'` with the target tenant — the one *read*
      read operation that is audited, since it opens financial data outside this system
- [ ] No financial figure crosses this boundary: the response is `{url, expires_at}` and nothing else

### US-07.10 — Trainer administration

`GET /api/v1/admin/trainers` · `PATCH` · `deactivate` / `reactivate`

**Acceptance criteria**
- [ ] Listing carries business name, slug, owner email, player and coach counts, events last 30 days,
      `subscription_status`, `charges_enabled`, feature flags, `created_at`
- [ ] Counts come from `platform_daily_metrics` and `trainer_daily_metrics` (Epic-03), not live counts
- [ ] Filters: `subscription_status`, `q`, feature enablement, registration date range
- [ ] Deactivation cascades per Epic-01 US-01.12: the tenant's data is hidden from players, nothing is deleted
- [ ] Deactivating a trainer with **future paid events** ⇒ `409 ACTIVE_COMMITMENTS` listing event ids,
      overridable with `?force=true`, which then triggers Epic-02 cancellation with full refunds
- [ ] Every transition writes `audit_log`

---

## 10. Business rules (service layer)

**Audit — what is recorded**

`audit_action` is a closed enum. The rule: **every state change performed by a `super_admin`, every
impersonated request, and every security-relevant transition by any role.**

| Action | Written by |
|:---|:---|
| `user.created` · `user.updated` · `user.deactivated` · `user.reactivated` · `user.erased` | Epic-01 |
| `trainer.created` · `trainer.deactivated` · `trainer.reactivated` | Epic-01 / this epic |
| `impersonation.started` · `impersonation.ended` · `impersonation.request` | Epic-01 |
| `feature.toggled` | this epic |
| `pricing.updated` | Epic-05 |
| `conflict.overridden` · `event.admin_edited` · `event.admin_canceled` | Epic-02 / this epic |
| `refund.manual` · `tokens.gifted` | Epic-05 |
| `flag.applied` · `flag.resolved` (cross-tenant only) | Epic-03 |
| `referral_rule.updated` | Epic-06 |
| `stripe.dashboard_accessed` | this epic |
| `auth.refresh_reuse_detected` · `auth.rate_limited` | Epic-01 |

**Not** recorded: dashboard views, list and search queries, single-record reads, and audit-log reads.
Recording them would bury the state changes the log exists to capture.

**Audit — integrity.** Append-only by trigger and by grant. Partitioned monthly. Retention 400 days.
`details` carries identifiers and field *names*, never PII values — enforced by a serialisation helper
that rejects a set of banned keys, plus a suite-wide scan asserting the whole test run produced no PII
in the table.

**Impersonation.** When a request carries `act`, `actor_user_id` is the impersonated user and
`impersonated_user_id` is the admin, so a reader sees both. A middleware writes one
`impersonation.request` row per mutating request under impersonation — reads are counted in the
session summary rather than logged individually.

**Feature flags.** Three features in MVP: `lppp` (Epic-04), `marketing` (Epic-06), `camps` (Epic-08).
Absence of a row means enabled. Reads go through a Redis cache with a 60 s TTL, busted on write.
Disabled means **hidden, never deleted**: trainer routes `403 FEATURE_DISABLED`, player-facing
collections come back empty for that tenant, and every row survives untouched.

**Cross-tenant access.** Marked with `SuperAdminScope` at the route. An architectural test enumerates
routes, asserts every route that omits a tenant filter carries the marker, and asserts no marker
appears on a non-admin route. This is the mechanical guard that keeps Conventions §3 honest.

**Financial boundary.** This platform serves no revenue, payout, fee-total, or tax figure. Requests
for them are answered with a Stripe link. `GET /admin/trainers/{id}` may report
`subscription_status`, because that is an access-control fact, not a financial one.

**Metrics.** Rollups only. The dashboard's correctness test mutates source data, runs the rollup, and
asserts the aggregate matches a direct count — the same discipline as Epic-03.

---

## 11. Backend flows

**Feature flag check on a request**
```
any route in Epic-04 / 06 / 08
  └─ Depends(require_feature(Feature.LPPP))
        FeatureFlagService.is_enabled(feature, trainer_id)
          ├─ Redis GET flags:{trainer_id}          → hit  → decide
          └─ miss → SELECT feature_flags WHERE trainer_id=:t
                    default True for absent rows
                    SETEX 60s
        disabled → 403 FEATURE_DISABLED
```

**Toggling a flag**
```
PUT /admin/trainers/{id}/features
  BEGIN
    UPSERT feature_flags for each of the three features
    INSERT audit_log (feature.toggled) per changed feature
  COMMIT
  DEL flags:{trainer_id}          ← immediate bust; other instances converge within their 60s TTL
```

**Audited admin mutation (shape shared by every admin write)**
```
PATCH /admin/<resource>/{id}
  BEGIN
    apply change via the owning epic's service, SuperAdminScope
    AuditService.record(action=..., actor=..., subject=..., trainer_id=...,
                        details={"fields": [...]}, request_id=...)
  COMMIT
```
The audit row is written in the **same transaction** as the change: an admin action that happened but
was not logged, or was logged but did not happen, are both unacceptable.

---

## 12. Background jobs

| Job | Trigger | Effect | Idempotency key |
|:---|:---|:---|:---|
| `rollup_platform_metrics` | cron, nightly + hourly for today | upsert `platform_daily_metrics` | `metric_date` |
| `prune_audit_log` | cron, daily | drop partitions older than retention | partition name |
| `create_audit_partitions` | cron, monthly | pre-create the next two monthly partitions | partition name |
| `sync_trainer_subscription_status` | cron, hourly + Stripe webhook | refresh `subscription_status` | `trainer_id + updated_at` |
| `warm_dashboard_cache` | cron, every 5 min | precompute the three period responses | cache key |

---

## 13. Performance & scale targets

| Operation | Target | Verification |
|:---|:---|:---|
| `GET /admin/dashboard` cold / warm, 500 trainers | p95 < 2 s / < 100 ms | k6 + query-count |
| `GET /admin/users?q=` over 10 000 users | p95 < 500 ms | trigram index |
| `GET /admin/events` over 5 000 events | p95 < 2 s | k6 |
| `GET /admin/audit-log` over 100 000 rows, 30-day filter | p95 < 1 s | partition pruning in `EXPLAIN` |
| `FeatureFlagService.is_enabled` warm / cold | p95 < 5 ms / < 50 ms | micro-benchmark |
| `GET /api/v1/features` | p95 < 50 ms | k6 |
| Audit write overhead on an admin mutation | < 10 ms added | integration timing |
| Audit CSV export, 100 000 rows | streams, peak RSS < 100 MB | integration test |

Scale: 500 trainers · 10 000 players · 5 000 events · 100 000 audit rows/year.

---

## 14. Open questions

| ID | Question | Priority | Status |
|:---|:---|:---:|:---|
| Q-07.02 | Are 7/30/90-day presets enough, or is a custom range needed? | P2 | Open — presets ship; a custom range is an added `from`/`to` pair |
| Q-07.01 | Are camps in MVP? | P1 | ✅ Resolved — yes, Epic-08; the `camps` flag ships |
| Q-07.03 | Bulk trainer operations? | P2 | ✅ Resolved — Post-MVP |
| Q-07.04 | Audit retention beyond 400 days for compliance? | P2 | Open — `admin_settings.audit_retention_days` is configurable |

---

## 15. Epic Definition of Done

Conventions §10 plus:

- [ ] All 9 user stories implemented per §8
- [ ] **Feature-flag round-trip is a merge gate**: create content → disable → players see empty,
      trainer gets `403`, data intact → re-enable → everything returns identical
- [ ] Flag propagation verified to be ≤ 60 s across two running instances
- [ ] Audit append-only trigger proven to reject `UPDATE` and `DELETE`
- [ ] Suite-wide PII scan over `audit_log` finds no email, phone, or full name
- [ ] Every audited action in the §10 table has a test asserting the row is written **in the same
      transaction** as the change (rollback ⇒ no audit row, and vice versa)
- [ ] `SuperAdminScope` architectural test: every cross-tenant route carries the marker; no
      non-admin route does
- [ ] Every route in this epic returns `403` for `trainer`, `coach`, and `player` — parametrised
- [ ] Dashboard schema test: no field name matches `revenue|payout|earnings|amount`
- [ ] Dashboard rollup correctness: mutate source, roll up, assert against a direct count
- [ ] Audit partitioning verified: `EXPLAIN` shows partition pruning on a date-filtered query
- [ ] `prune_audit_log` drops an old partition and retains the rest
- [ ] Capacity proven non-overridable even for `super_admin`
- [ ] Load targets in §13 met against 500 trainers and 100 000 audit rows
