# Epic-02 (Python Backend): Event Management & Scheduling

> Stack, layering, error contract, pagination, tenancy and testing rules: [00_Backend_Conventions.md](00_Backend_Conventions.md).

**Priority**: P0 — core revenue path
**Complexity**: High (capacity under concurrency, payment coupling, multi-role state machine)
**User stories**: 15

---

## 1. Description

Event lifecycle, RSVP with hard capacity guarantees, coach assignment with availability checks, and
attendance recording. This epic owns the two most contested resources in the system — **event
capacity** and **event state** — and it is where race conditions become oversold sessions and
double charges.

Scope boundary: this epic decides *whether an RSVP exists*. Epic-05 decides
*whether it was paid for*. The two are coupled by a strict ordering rule (§10) so an unpaid RSVP
never holds a seat.

## 2. Backend outcomes

- Capacity that cannot be oversold, verified by a concurrency test.
- One event state machine, enforced in the service and mirrored by a DB `CHECK`.
- An eligibility predicate computed in SQL, so calendar queries stay indexed at 10 000+ events.
- Deterministic RSVP↔payment ordering with a compensating path when payment fails.

---

## 3. In scope (MVP)

### Events (trainer)
- [ ] Create/edit/cancel events: title, type, start/end, location, capacity, description
- [ ] Types: `training_session`, `private_session`, `small_group`
- [ ] Eligibility restrictions: age range, skill levels, genders
- [ ] **Dual pricing** — an event may carry both `price_cents` and `token_price`; the player picks
- [ ] Flexible token pricing (an event may cost N tokens). Defaults: `token_price = 1`, `price_cents = 0`
- [ ] **Recurring bulk creation** — expand a pattern into N independent event rows
- [ ] Private / invite-only events with an explicit invitee list
- [ ] Duplicate an event (no RSVPs copied)
- [ ] Coach assignment with availability conflict detection + logged override
- [ ] Player-availability counts for a candidate time slot
- [ ] RSVP roster read + manual add/remove + CSV export

### RSVP (player / parent)
- [ ] Calendar query — eligible public events + invited private events, per trainer context
- [ ] RSVP: free ⇒ instant; paid ⇒ payment-first (§10)
- [ ] Choose payment method when both prices are set
- [ ] Subscription holders: **1 advance booking per calendar day**, unlimited same-day
- [ ] Cancel RSVP with the 24-hour refund rule
- [ ] `EVENT_FULL` when capacity is reached — no waitlist in MVP
- [ ] My Reservations listing

### Coach
- [ ] Assignment inbox; confirm / decline
- [ ] Roster read for assigned events
- [ ] Attendance recording: `present` / `absent` / `late` / `excused`
- [ ] Same-day edit window, locked after local midnight

### Super Admin
- [ ] System-wide event query with filters
- [ ] Edit/cancel any event; conflict checks bypassed and audited

---

## 4. Out of scope (Post-MVP)

Waitlists · per-trainer refund policy · true linked recurring series · bulk edit/cancel/clone ·
event templates · AI scheduling suggestions · automated reminders · post-event player ratings ·
video-conferencing integration · equipment tracking · Open Gym / League / referee assignment.
Camps and evaluations moved to **Epic-08** (they are forms, not events).

---

## 5. Dependencies

**Required before**: Epic-01 (users, tenancy, availability, parent approval).

**Blocks**: Epic-03 (attendance analytics), Epic-04 (content attached to events), Epic-05 (paid RSVP),
Epic-06 (event-targeted links).

**Consumes**: `AvailabilityService.check_conflict` (Epic-01) · `ApprovalService` (Epic-01) ·
`PaymentService` / `TokenService` (Epic-05) · `AuditService` (Epic-07).

---

## 6. Roles & permission matrix

| Capability | super_admin | trainer | coach | player | child |
|:---|:---:|:---:|:---:|:---:|:---:|
| Create / edit / cancel event | ✅ any | ✅ own tenant | ❌ | ❌ | ❌ |
| Assign coach | ✅ any | ✅ own coaches + self | ❌ | ❌ | ❌ |
| Confirm / decline assignment | ✅ | ✅ (self-assigned) | ✅ own | ❌ | ❌ |
| Read event roster | ✅ | ✅ own tenant | ✅ assigned events | ❌ | ❌ |
| Record attendance | ✅ anytime | ✅ anytime | ✅ same day only | ❌ | ❌ |
| RSVP | ❌ | ❌ | ❌ | ✅ | → approval |
| Cancel RSVP | ✅ | ✅ (removes player) | ❌ | ✅ own | → approval |
| Read player availability counts | ✅ | ✅ own tenant | ❌ | ❌ | ❌ |
| Override availability conflict | ✅ silently | ✅ with reason | ❌ | ❌ | ❌ |

---

## 7. Domain model

`app/models/events.py`. All PKs UUIDv7; all timestamps `timestamptz` UTC.

### `locations`
`id` · `trainer_id` FK · `name` · `address` · `timezone` (IANA, not null) · `is_active`.
The event's timezone is inherited from its location — all local-time reasoning (midnight lock,
same-day booking) uses it.

### `events`
| Column | Type | Notes |
|:---|:---|:---|
| `id` | UUID | PK |
| `trainer_id` | UUID FK | not null, indexed |
| `location_id` | UUID FK | not null |
| `title` | varchar(100) | not null |
| `description` | text | |
| `event_type` | enum | `training_session`/`private_session`/`small_group` |
| `starts_at` / `ends_at` | timestamptz | `CHECK (ends_at > starts_at)`, `CHECK (ends_at - starts_at <= interval '24 hours')` |
| `capacity` | int | `CHECK (capacity BETWEEN 1 AND 999)` |
| `rsvp_count` | int | not null default 0, `CHECK (rsvp_count >= 0 AND rsvp_count <= capacity)` |
| `visibility` | enum | `public`/`private` |
| `price_cents` | int | default 0, `CHECK (price_cents >= 0)` |
| `token_price` | int | default 1, `CHECK (token_price >= 0)` |
| `min_age` / `max_age` | smallint | nullable |
| `allowed_skill_levels` | enum[] | nullable = no restriction |
| `allowed_genders` | enum[] | nullable = no restriction |
| `status` | enum | `draft`/`published`/`canceled`/`completed` |
| `canceled_at` / `canceled_by` / `cancellation_reason` | | set together |
| `duplicated_from_event_id` | UUID FK | nullable, analytics |
| `recurrence_batch_id` | UUID | nullable — groups bulk-created instances |

Indexes:
`ix_events_calendar (trainer_id, status, starts_at)` ·
`ix_events_upcoming (starts_at) WHERE status = 'published'` ·
`ix_events_batch (recurrence_batch_id)`.

> `rsvp_count` is a **denormalised counter maintained by the atomic UPDATE in §8**, and it is
> reconciled nightly against `COUNT(*)` on `rsvps` by `reconcile_rsvp_counts`. The `CHECK` makes an
> oversell a database error rather than a business incident.

### `event_invitations` (private events)
`id` · `event_id` FK · `player_profile_id` FK · `invited_by_user_id` · `invited_at`.
`UNIQUE (event_id, player_profile_id)`.

### `rsvps`
`id` · `event_id` FK · `player_profile_id` FK · `trainer_id` FK (denormalised for tenant queries) ·
`status` enum (`registered`/`canceled`) · `payment_method` enum (`free`/`usd`/`tokens`/`subscription`) ·
`payment_transaction_id` UUID nullable (Epic-05) · `token_amount` int nullable ·
`amount_cents` int nullable · `registered_at` · `canceled_at` nullable ·
`cancellation_reason` · `refund_status` enum (`none`/`pending`/`refunded`/`forfeited`).

**`CREATE UNIQUE INDEX uq_rsvp_active ON rsvps (event_id, player_profile_id) WHERE status = 'registered';`**
— a player holds at most one live seat, enforced by the database. Cancel-then-rebook works because
canceled rows fall out of the partial index.

Index `ix_rsvps_player (player_profile_id, registered_at DESC)`.

### `coach_assignments`
`id` · `event_id` FK · `coach_user_id` FK · `assigned_by_user_id` · `status` enum
(`pending`/`confirmed`/`declined`) · `assigned_at` · `responded_at` · `decline_reason` ·
`had_availability_conflict` bool · `override_reason` text nullable · `overridden_by_user_id` nullable.

`CHECK (NOT had_availability_conflict OR override_reason IS NOT NULL)` — an override without a
reason cannot be stored. `UNIQUE (event_id, coach_user_id)`.

### `attendance_records`
`id` · `event_id` FK · `player_profile_id` FK · `status` enum
(`present`/`absent`/`late`/`excused`) · `recorded_by_user_id` · `recorded_at` · `updated_at`.
`UNIQUE (event_id, player_profile_id)`.

### `attendance_audit`
`id` · `attendance_record_id` FK · `old_status` · `new_status` · `changed_by_user_id` · `changed_at`.
Append-only; every mutation of `attendance_records` writes one row in the same transaction.

---

## 8. API surface

| Method | Path | Role |
|:---|:---|:---|
| POST | `/api/v1/trainer/events` | trainer |
| POST | `/api/v1/trainer/events/bulk` | trainer (recurrence expansion) |
| GET | `/api/v1/trainer/events` | trainer |
| GET/PATCH | `/api/v1/trainer/events/{id}` | trainer |
| POST | `/api/v1/trainer/events/{id}/cancel` | trainer |
| POST | `/api/v1/trainer/events/{id}/duplicate` | trainer |
| GET | `/api/v1/trainer/events/{id}/rsvps` | trainer |
| POST | `/api/v1/trainer/events/{id}/rsvps` | trainer (manual add) |
| DELETE | `/api/v1/trainer/events/{id}/rsvps/{rsvp_id}` | trainer (remove + refund) |
| GET | `/api/v1/trainer/events/{id}/rsvps/export` | trainer (CSV) |
| POST | `/api/v1/trainer/events/{id}/invitations` | trainer (private) |
| GET | `/api/v1/trainer/availability-preview` | trainer (`?starts_at=&ends_at=`) |
| PUT | `/api/v1/trainer/events/{id}/coach` | trainer (assign / reassign) |
| GET | `/api/v1/coach/assignments` | coach |
| POST | `/api/v1/coach/assignments/{id}/confirm` \| `/decline` | coach |
| GET | `/api/v1/coach/events/{id}/roster` | coach |
| PUT | `/api/v1/coach/events/{id}/attendance` | coach |
| GET | `/api/v1/events` | player (`?trainer_id=` required — the context) |
| GET | `/api/v1/events/{id}` | player |
| POST | `/api/v1/events/{id}/rsvp` | player (`Idempotency-Key` required) |
| POST | `/api/v1/rsvps/{id}/cancel` | player |
| GET | `/api/v1/me/reservations` | player |
| GET | `/api/v1/admin/events` | super_admin |
| PATCH | `/api/v1/admin/events/{id}` | super_admin |
| POST | `/api/v1/admin/events/{id}/cancel` | super_admin |

---

## 9. User stories → backend tasks

### US-02.01 — Trainer creates a training event

`POST /api/v1/trainer/events`

**Acceptance criteria**
- [ ] Required: `title` (≤ 100), `event_type`, `starts_at`, `ends_at`, `location_id`, `capacity`
- [ ] `starts_at` in the past ⇒ `422 START_IN_PAST` (compared against the injected `Clock`)
- [ ] `ends_at <= starts_at` ⇒ `422`; duration > 24 h ⇒ `422`; > 8 h returns `warnings[]` in the body but still `201`
- [ ] `capacity` outside 1–999 ⇒ `422`
- [ ] `location_id` from another tenant ⇒ `404`
- [ ] Defaults applied when omitted: `token_price = 1`, `price_cents = 0`, `visibility = public`, `status = published`
- [ ] `price_cents > 0` while the trainer has no connected Stripe account (Epic-05) ⇒ `409 STRIPE_NOT_CONNECTED`
- [ ] `201` returns the full event including `rsvp_count: 0`
- [ ] The row's `trainer_id` comes from the token; a `trainer_id` in the body is rejected by `extra="forbid"`

### US-02.02 — Trainer creates a private event

`POST /api/v1/trainer/events` with `visibility="private"`, then `POST .../invitations`

**Acceptance criteria**
- [ ] Invitee ids must be players linked to this tenant ⇒ else `404`
- [ ] `GET /api/v1/events` for a non-invited player never returns the event
- [ ] `GET /api/v1/events/{id}` for a non-invited player ⇒ `404` (not 403 — no existence disclosure)
- [ ] Eligibility filters are **not** applied to invitees: an explicit invitation overrides age/skill/gender
- [ ] Re-inviting an existing invitee ⇒ `200`, no duplicate row
- [ ] Removing an invitation for a player who already RSVP'd ⇒ `409 INVITEE_HAS_RSVP`

### US-02.03 — Trainer assigns a coach with an availability check

`PUT /api/v1/trainer/events/{id}/coach`

```json
{ "coach_user_id": "...", "override_reason": "Coach agreed by phone" }
```

**Acceptance criteria**
- [ ] Candidate set = the trainer's active coaches **plus the trainer themself**
- [ ] A coach from another tenant ⇒ `404`
- [ ] `AvailabilityService.check_conflict` returns a conflict and `override_reason` is absent ⇒ `409 COACH_UNAVAILABLE` with `details.windows` showing the coach's windows that day
- [ ] Conflict + `override_reason` present ⇒ `200`, row stores `had_availability_conflict=true`, the reason, and the overriding user; an audit row is written
- [ ] Coach already assigned to an **overlapping** event ⇒ `409 COACH_DOUBLE_BOOKED`, overridable by the same mechanism
- [ ] Assignment created with `status=pending`; enqueues `notify_coach_assignment`
- [ ] Reassignment sets the previous assignment to `declined` with `decline_reason="reassigned"` and notifies both coaches
- [ ] Super Admin performing the same call skips both conflict checks silently and writes an audit row (US-07.07)

### US-02.04 — Trainer previews player availability for a slot

`GET /api/v1/trainer/availability-preview?starts_at=&ends_at=&event_id=`

**Acceptance criteria**
- [ ] Returns `{eligible_count, available_count, unknown_count, busy_count}`
- [ ] "Eligible" = linked to this tenant, active, and passing the event's age/skill/gender predicate
- [ ] Availability is evaluated in the **location's** timezone against `availability_windows`
- [ ] A player with no windows for that weekday counts as `unknown`, never as `busy`
- [ ] When `event_id` is supplied, `GET .../rsvps` decorates each roster row with the same three-state indicator
- [ ] Computed in one SQL statement — no N+1 over players; asserted by a query-count test

### US-02.05 — Trainer duplicates an event

`POST /api/v1/trainer/events/{id}/duplicate`

**Acceptance criteria**
- [ ] `starts_at` and `ends_at` are required in the body; omitting either ⇒ `422`
- [ ] Copies title, type, location, capacity, pricing, eligibility, coach, visibility, invitee list
- [ ] Does **not** copy RSVPs or attendance; the new event starts at `rsvp_count = 0`
- [ ] Stores `duplicated_from_event_id`
- [ ] The source event is unmodified (asserted by a before/after row comparison)
- [ ] Duplicating another tenant's event ⇒ `404`

### US-02.05b — Trainer bulk-creates a recurring pattern

`POST /api/v1/trainer/events/bulk`

```json
{ "template": { }, "rule": { "weekdays": [1], "start_date": "2026-03-01",
  "end_date": "2026-05-31", "start_time": "16:00", "end_time": "18:00" } }
```

**Acceptance criteria**
- [ ] Expands the rule into independent `events` rows sharing one `recurrence_batch_id`
- [ ] Each instance is separately editable and cancellable — there is no series object
- [ ] Expansion is computed in the location's timezone, so DST transitions keep the local wall-clock time
- [ ] Cap of 200 instances per call ⇒ `422 TOO_MANY_INSTANCES` beyond it
- [ ] All rows inserted in one transaction; a failure creates none
- [ ] `201` returns `{recurrence_batch_id, created_count, event_ids[]}`

### US-02.06 — Player reads the training calendar

`GET /api/v1/events?trainer_id=&from=&to=&type=&q=`

**Acceptance criteria**
- [ ] `trainer_id` is **required**; a trainer the caller is not linked to ⇒ `404`
- [ ] Returns public published events passing eligibility, plus private events where the caller's
      profile is invited; excludes everything else
- [ ] Eligibility predicate is evaluated in SQL: `(min_age IS NULL OR age >= min_age) AND (...)`;
      test asserts a single query and an index scan
- [ ] Canceled events are returned with `status="canceled"` when they fall in range and the caller
      has an RSVP on them; otherwise omitted
- [ ] Each item carries `capacity`, `rsvp_count`, `is_full`, `price_cents`, `token_price`,
      `my_rsvp_status`, and `availability_match` (`available`/`unknown`/`busy`)
- [ ] `q` matches title and location name, case-insensitive, scoped to this tenant only
- [ ] Cursor pagination per Conventions §5; `limit` max 200

### US-02.07 — Player RSVPs

`POST /api/v1/events/{id}/rsvp` — `Idempotency-Key` **required**

```json
{ "player_profile_id": "...", "payment_method": "tokens" }
```

**Acceptance criteria**

*Common*
- [ ] `player_profile_id` not owned by the caller ⇒ `404`
- [ ] Player not linked to the event's trainer ⇒ `404`
- [ ] Event not `published` ⇒ `409 EVENT_NOT_BOOKABLE`
- [ ] `starts_at` in the past ⇒ `409 EVENT_STARTED`
- [ ] Ineligible by age/skill/gender and not explicitly invited ⇒ `403 NOT_ELIGIBLE`
- [ ] Already holding a `registered` RSVP ⇒ `409 ALREADY_REGISTERED` (from the partial unique index)
- [ ] Missing `Idempotency-Key` ⇒ `400 IDEMPOTENCY_KEY_REQUIRED`; a replay returns the stored response

*Free event* (`price_cents = 0 AND token_price = 0`)
- [ ] Atomic `UPDATE ... WHERE rsvp_count < capacity RETURNING` — 0 rows ⇒ `409 EVENT_FULL`
- [ ] `201` with `status="registered"`; enqueues `send_rsvp_confirmation`

*Paid event* — see the ordering rule in §10
- [ ] Seat is reserved first (atomic increment), then payment is attempted, then the RSVP is confirmed
- [ ] Payment failure releases the seat in a compensating transaction and returns `402 PAYMENT_FAILED`
- [ ] `payment_method="tokens"` with an insufficient balance ⇒ `402 INSUFFICIENT_TOKENS` with
      `details.balance` and `details.required`, seat released
- [ ] `payment_method` not offered by the event (e.g. `usd` when `price_cents = 0`) ⇒ `422 PAYMENT_METHOD_UNAVAILABLE`

*Child*
- [ ] A dependent's RSVP returns `202` with an approval id and reserves **no** seat (Epic-01 US-01.05);
      the seat is taken at approval time and may then fail with `EVENT_FULL`

*Subscription holder*
- [ ] More than one **advance** booking (event date > today in the location's timezone) per calendar
      day ⇒ `409 SUBSCRIPTION_DAILY_LIMIT`
- [ ] Same-day bookings are unlimited while capacity remains

*Concurrency*
- [ ] **50 parallel RSVPs against a capacity-20 event ⇒ exactly 20 × `201`, 30 × `409 EVENT_FULL`,
      and `rsvp_count = 20`.** This test is a merge gate.

### US-02.08 — Player cancels an RSVP

`POST /api/v1/rsvps/{id}/cancel`

**Acceptance criteria**
- [ ] Another user's RSVP ⇒ `404`
- [ ] Already canceled ⇒ `409 ALREADY_CANCELED`
- [ ] After `starts_at` ⇒ `409 EVENT_STARTED`
- [ ] Sets `status=canceled`, decrements `rsvp_count` atomically (`GREATEST(rsvp_count - 1, 0)`), frees the seat
- [ ] ≥ 24 h before `starts_at` ⇒ refund requested from Epic-05; `refund_status` becomes `refunded`
      (tokens, synchronous) or `pending` (USD, asynchronous via webhook)
- [ ] < 24 h ⇒ `refund_status = forfeited`, no refund; the response states this explicitly
- [ ] The 24 h boundary is tested at `-24h0m1s` (refund) and `-23h59m59s` (forfeit)
- [ ] A dependent-initiated cancellation of a **paid** RSVP goes through parent approval
- [ ] Enqueues `send_cancellation_confirmation`

### US-02.10 — Coach confirms or declines an assignment

`POST /api/v1/coach/assignments/{id}/confirm` · `/decline`

**Acceptance criteria**
- [ ] Another coach's assignment ⇒ `404`
- [ ] Not `pending` ⇒ `409 ASSIGNMENT_NOT_PENDING`
- [ ] Confirm ⇒ `status=confirmed`, `responded_at` set; enqueues `notify_trainer_assignment_response`
- [ ] Decline accepts an optional `reason`; the event keeps `coach_assignment` in `declined` and
      surfaces `needs_coach: true` in trainer listings
- [ ] `GET /api/v1/coach/assignments?status=pending` returns title, start, location, and current `rsvp_count`
- [ ] Auto-confirm after 48 h is behind a per-trainer setting `auto_confirm_coach_assignments`
      (Q-02.02); when off, assignments stay `pending` indefinitely

### US-02.11 — Coach records attendance

`PUT /api/v1/coach/events/{id}/attendance`

```json
{ "records": [ { "player_profile_id": "...", "status": "present" } ] }
```

**Acceptance criteria**
- [ ] Before `starts_at` ⇒ `409 EVENT_NOT_STARTED`
- [ ] Coach not assigned (or assignment not `confirmed`) ⇒ `404`
- [ ] A `player_profile_id` without a `registered` RSVP ⇒ `422 PLAYER_NOT_ON_ROSTER`
- [ ] Absent entries default to `present`; the endpoint upserts the whole roster in one statement
- [ ] Every write appends an `attendance_audit` row in the same transaction
- [ ] **Edit window**: a coach may modify only until local midnight of `starts_at` **in the location's
      timezone**; afterwards ⇒ `403 ATTENDANCE_LOCKED`. Tested at 23:59:59 and 00:00:01 local
- [ ] Trainer and super_admin bypass the lock via `PUT /api/v1/trainer/events/{id}/attendance`
- [ ] Attendance feeds Epic-03 metrics: `present` and `late` count as attended; `absent` and `excused` do not

### US-02.12 — Trainer reads the RSVP roster

`GET /api/v1/trainer/events/{id}/rsvps`

**Acceptance criteria**
- [ ] Each row: player name, age, skill level, `registered_at`, `payment_method`, payment status,
      `availability_match`
- [ ] Envelope carries `{capacity, rsvp_count, is_full}`
- [ ] `POST .../rsvps` manual add bypasses eligibility but **not** capacity ⇒ `409 EVENT_FULL`
      (Q-02.09 may relax this; if relaxed it becomes an audited override)
- [ ] `DELETE .../rsvps/{rsvp_id}` cancels with a **full refund regardless of timing** — a trainer
      removing a player is not the player's cancellation
- [ ] `GET .../rsvps/export` streams CSV (`StreamingResponse`, server-side cursor, no full
      materialisation); columns are fixed and documented
- [ ] Another tenant's event ⇒ `404` on every one of these routes

### US-02.13 — Trainer cancels an event

`POST /api/v1/trainer/events/{id}/cancel`

**Acceptance criteria**
- [ ] `reason` is required ⇒ `422` without it
- [ ] Sets `status=canceled` with `canceled_at`, `canceled_by`, `cancellation_reason`
- [ ] Enqueues `process_event_cancellation`, which refunds **every** registered RSVP in full
      irrespective of the 24 h rule, then notifies players and the assigned coach
- [ ] Already `canceled` ⇒ `409 ALREADY_CANCELED`; already `completed` ⇒ `409 EVENT_COMPLETED`
- [ ] Cancelling within 24 h of `starts_at` is permitted for the trainer but flagged in the audit row;
      super_admin may cancel at any time
- [ ] The event row is never deleted — history and refunds depend on it
- [ ] Refund job is idempotent: replaying it issues no second refund (guarded by `refund_status`)

### US-02.14 — Trainer edits an event

`PATCH /api/v1/trainer/events/{id}`

**Acceptance criteria**
- [ ] Past events ⇒ `409 EVENT_COMPLETED`; canceled events ⇒ `409 EVENT_CANCELED`
- [ ] `capacity` below current `rsvp_count` ⇒ `409 CAPACITY_BELOW_RSVPS` with `details.rsvp_count`
- [ ] Changing `starts_at`, `ends_at`, or `location_id` enqueues `notify_event_changed` to all registered players
- [ ] Raising `price_cents` enqueues `notify_price_increase`, which grants affected players a
      full-refund cancellation window of 48 h regardless of the 24 h rule
- [ ] Changing the coach follows US-02.03 including conflict checks
- [ ] Only supplied fields change; `PATCH` with `{}` ⇒ `200` and no writes
- [ ] Every edit that triggers a notification writes an audit row

### US-02.15 — Super Admin queries all events

`GET /api/v1/admin/events`

**Acceptance criteria**
- [ ] Cross-tenant by design; filters: `trainer_id`, `location_id`, `from`, `to`, `event_type`, `status`, `q`
- [ ] Cursor pagination, `limit` max 200
- [ ] `PATCH` / `cancel` reuse the trainer services with `SuperAdminScope`, skipping tenant checks and conflict warnings
- [ ] Every admin mutation writes an audit row naming the affected tenant
- [ ] A `trainer` calling any `/admin/*` route ⇒ `403`

---

## 10. Business rules (service layer)

**The RSVP↔payment ordering rule** — a paid RSVP follows exactly this sequence, and no other:

```
1. BEGIN
2. atomic reserve:  UPDATE events SET rsvp_count = rsvp_count + 1
                    WHERE id = :id AND rsvp_count < capacity RETURNING rsvp_count
                    → 0 rows ⇒ ConflictError(EVENT_FULL), ROLLBACK
3. INSERT rsvps (status='registered', payment_transaction_id=NULL)
4. COMMIT                                   ← seat is now held
5. charge via Epic-05 (tokens: in-process; USD: Stripe PaymentIntent)
6a. success → UPDATE rsvps SET payment_transaction_id = :tx  → 201
6b. failure → compensating tx: rsvps.status='canceled',
              rsvp_count = GREATEST(rsvp_count - 1, 0)       → 402
```

Rationale: reserving before charging prevents selling a seat that payment then wins; the
compensating step bounds the exposure to a failed charge. A crash between 4 and 6 leaves an unpaid
`registered` row, which `sweep_unpaid_rsvps` (§12) cancels after 15 minutes.

**Capacity** — `rsvp_count` moves only through the atomic conditional UPDATE. No service ever
computes `count + 1` in Python. The `CHECK (rsvp_count <= capacity)` is the backstop.

**Eligibility** — a player is eligible when: linked and active in the tenant, age within
`[min_age, max_age]` computed from `birth_date` at `starts_at`, skill level in `allowed_skill_levels`
when set, gender in `allowed_genders` when set. An explicit private-event invitation overrides all of it.

**Visibility** — private events are excluded at the **query** level, never filtered after fetch, so
they cannot leak through counts, search, or pagination metadata.

**Refunds** — player cancels ≥ 24 h ⇒ full refund; < 24 h ⇒ forfeit; trainer cancels ⇒ always full
refund; trainer removes a player ⇒ always full refund. The window is measured against `starts_at` in UTC.

**Attendance** — recordable only after `starts_at`; coach edit window closes at local midnight;
trainer and super_admin edit without limit; every change is audited. `present` and `late` count as
attended for Epic-03.

**Coach constraints** — one confirmed assignment per event; overlapping assignments and
availability conflicts both warn and both are overridable with a stored reason; super_admin overrides silently.

**Subscription bookings** — at most one advance booking per calendar day in the location's timezone;
same-day unlimited. Evaluated by counting the caller's `registered` RSVPs whose event date equals the
target date and whose `registered_at` date is earlier.

---

## 11. Backend flows

**Free RSVP**
```
POST /events/{id}/rsvp
  ├─ idempotency lookup → replay?
  ├─ load event (published, future), assert eligibility / invitation
  ├─ atomic reserve → 0 rows ⇒ 409 EVENT_FULL
  ├─ INSERT rsvps  (partial unique index rejects a second live seat)
  ├─ COMMIT, store idempotent response
  └─ enqueue send_rsvp_confirmation
```

**Trainer cancels an event**
```
POST /trainer/events/{id}/cancel {reason}
  ├─ assert status = published, load registered rsvps
  ├─ UPDATE events SET status='canceled', ...
  ├─ COMMIT
  └─ enqueue process_event_cancellation(event_id)
        for each rsvp with refund_status='none':
          refund full (tokens sync / Stripe async)
          UPDATE rsvps SET status='canceled', refund_status=...
        enqueue notify_event_canceled(player_ids)
        enqueue notify_coach_event_canceled(coach_id)
```

**Attendance write**
```
PUT /coach/events/{id}/attendance
  ├─ assert event started, assignment confirmed, within edit window
  ├─ INSERT ... ON CONFLICT (event_id, player_profile_id) DO UPDATE
  ├─ INSERT attendance_audit rows for changed statuses
  └─ COMMIT
```

---

## 12. Background jobs

| Job | Trigger | Effect | Idempotency key |
|:---|:---|:---|:---|
| `send_rsvp_confirmation` | RSVP confirmed | email + receipt link | `rsvp_id + 'confirmed'` |
| `send_cancellation_confirmation` | RSVP canceled | email incl. refund outcome | `rsvp_id + 'canceled'` |
| `notify_coach_assignment` | assignment created | coach email + in-app | `assignment_id + 'assigned'` |
| `notify_trainer_assignment_response` | confirm / decline | trainer email | `assignment_id + status` |
| `notify_event_changed` | time/location patch | email to registered players | `event_id + updated_at` |
| `notify_price_increase` | price raised | email + extended cancel window | `event_id + updated_at` |
| `process_event_cancellation` | event canceled | refund all RSVPs, notify all | `event_id + 'cancellation'` |
| `sweep_unpaid_rsvps` | cron, 5 min | cancel `registered` RSVPs older than 15 min with `payment_transaction_id IS NULL` on paid events | per-row guard |
| `complete_past_events` | cron, hourly | `published` → `completed` once `ends_at < now()` | per-row guard |
| `reconcile_rsvp_counts` | cron, nightly | compare `rsvp_count` to `COUNT(*)`, correct + alert on drift | idempotent by construction |
| `auto_confirm_assignments` | cron, hourly | confirm `pending` older than 48 h where the trainer opted in | per-row guard |

---

## 13. Performance & scale targets

| Operation | Target | Verification |
|:---|:---|:---|
| `POST /trainer/events` | p95 < 300 ms | k6 |
| `GET /events` over 100 events in range, tenant with 10 000 historical | p95 < 500 ms | seeded + `EXPLAIN ANALYZE` on `ix_events_calendar` |
| `POST /events/{id}/rsvp` free | p95 < 200 ms | k6 |
| `POST /events/{id}/rsvp` paid (excl. Stripe) | p95 < 800 ms | k6 with a stubbed PSP |
| 50 concurrent RSVPs on one event | 0 oversell, 0 deadlocks | e2e concurrency test (merge gate) |
| `GET /trainer/events/{id}/rsvps` 200 rows | p95 < 400 ms, no N+1 | query-count assertion |
| CSV export 10 000 rows | streams, peak RSS < 100 MB | integration test |

---

## 14. Open questions

| ID | Question | Priority | Blocks |
|:---|:---|:---:|:---|
| Q-02.02 | Auto-confirm coach assignments after 48 h, or always explicit? | P2 | `auto_confirm_assignments` job + trainer setting |
| Q-02.08 | Event reminders 24 h ahead — MVP or Phase 2? | P2 | one extra cron job |
| Q-02.09 | May a trainer manually exceed capacity? | P2 | whether manual add is an audited override |

Assumption where unresolved: auto-confirm ships **off** by default behind a per-trainer flag;
manual add does **not** exceed capacity.

---

## 15. Epic Definition of Done

Conventions §10 plus:

- [ ] All 15 user stories implemented per §8
- [ ] **The 50-way concurrent RSVP test passes with zero oversell** — hard merge gate
- [ ] Compensating-transaction test: a stubbed payment failure leaves `rsvp_count` unchanged and the RSVP canceled
- [ ] 24-hour refund boundary tested on both sides with an injected clock
- [ ] Attendance midnight lock tested on both sides in a non-UTC location timezone
- [ ] Private-event leakage tests: list, detail, search, and count all return nothing for non-invitees
- [ ] Cross-tenant `404` on every trainer and coach route
- [ ] `sweep_unpaid_rsvps` integration-tested against a deliberately orphaned RSVP
- [ ] `reconcile_rsvp_counts` corrects an artificially drifted counter and emits an alert log
- [ ] DST test: a bulk recurrence spanning a DST change keeps local start times constant
- [ ] Load targets in §13 met against 10 000 seeded events
