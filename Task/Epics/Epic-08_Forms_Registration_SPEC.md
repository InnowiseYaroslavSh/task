# Epic-08 (Python Backend): Forms & Registration (Camps / Evaluations)

> Stack, layering, error contract, pagination, tenancy and testing rules: [00_Backend_Conventions.md](00_Backend_Conventions.md).

**Priority**: P2 — lead generation
**Complexity**: Medium-High (public unauthenticated surface, dynamic schema validation, payment, conversion)
**User stories**: 6

---

## 1. Description

A forms system for camps and evaluations — **not** calendar events. A trainer defines a form, shares a
public link, non-users submit it (paying through Stripe when priced), and submissions optionally
convert into full accounts.

This is the **only unauthenticated write surface in the system** — every other route requires a
token. The epic is therefore defined as much by its abuse resistance as by its features: rate
limiting, spam mitigation, capacity that cannot be oversold by anonymous callers, and validation of a
schema the trainer defines at runtime.

**`form_fields` is user-supplied schema.** Validating a submission means building a Pydantic model
dynamically from a stored definition, without letting that definition become an injection vector.

## 2. Backend outcomes

- Dynamic per-form validation compiled from a stored, strictly-validated field definition.
- A public endpoint hardened against enumeration, spam, and oversell.
- Capacity that holds under concurrent anonymous submission.
- A conversion path that turns a submission into an Epic-01 account without re-keying data.

---

## 3. In scope (MVP)

### Form definition (trainer)
- [ ] Create camp and evaluation forms from pre-loaded templates
- [ ] Field types: `text`, `email`, `phone`, `number`, `date`, `dropdown`, `multiselect`, `checkbox`
- [ ] Add / remove / reorder fields; mark required
- [ ] Capacity limit (camps only)
- [ ] Optional price
- [ ] Enable / disable (camps); evaluations are always on
- [ ] Public shareable link
- [ ] Edit and delete with guards

### Public submission
- [ ] Fetch a form definition by public code — no authentication
- [ ] Submit with server-side validation against the stored definition
- [ ] Free ⇒ immediate confirmation; priced ⇒ Stripe Checkout, confirmed by webhook
- [ ] Capacity enforced atomically at submission
- [ ] Confirmation email
- [ ] Account-creation offer

### Trainer management
- [ ] Submission listing with filters
- [ ] CSV export
- [ ] Attendance marking for camp participants
- [ ] Bulk email to participants
- [ ] Manual conversion of a submission to an account

### Conversion
- [ ] Registration pre-filled from a submission id
- [ ] Automatic tenant association on conversion
- [ ] Fallback: emailed ShareLink for later registration

---

## 4. Out of scope (Post-MVP)

File uploads, signatures, and tables · conditional logic · multi-page forms · form analytics ·
abandonment reminders · automated camp refunds (manual via Stripe) · waitlists · early-bird and group
pricing · recurring evaluation requests · evaluation time-slot booking · video upload · form
versioning · custom templates beyond the pre-loaded pair · third-party form tool integration.

---

## 5. Dependencies

**Required before**: Epic-01 (account creation, ShareLinks), Epic-05 (Stripe Checkout, webhooks).

**Gated by**: Epic-07's `camps` feature flag.

**Blocks**: nothing.

**Consumes**: `PaymentService` (Checkout + webhook) · `ShareLinkService` (Epic-01) ·
`FeatureFlagService` (Epic-07) · `EmailSender`.

---

## 6. Roles & permission matrix

| Capability | anonymous | super_admin | trainer | coach | player |
|:---|:---:|:---:|:---:|:---:|:---:|
| Read a public form by code | ✅ | ✅ | ✅ | ✅ | ✅ |
| Submit a form | ✅ | ✅ | ✅ | ✅ | ✅ |
| Create / edit / delete a form | ❌ | ✅ any | ✅ own | ❌ | ❌ |
| Read submissions | ❌ | ✅ any | ✅ own | ❌ | ❌ |
| Export submissions | ❌ | ✅ | ✅ own | ❌ | ❌ |
| Mark camp attendance | ❌ | ✅ | ✅ own | ❌ | ❌ |
| Bulk email participants | ❌ | ✅ | ✅ own | ❌ | ❌ |
| Convert a submission | ✅ (own, by token) | ✅ | ✅ own | ❌ | ❌ |

---

## 7. Domain model

`app/models/forms.py`.

### `forms`
| Column | Type | Notes |
|:---|:---|:---|
| `id` | UUID | PK |
| `trainer_id` | UUID FK | not null |
| `form_type` | enum | `camp`/`evaluation` |
| `name` | varchar(120) | not null |
| `description` | text | |
| `display_start_date` / `display_end_date` | date | nullable — **display only**, never calendar-linked |
| `price_cents` | int | default 0, `CHECK (>= 0 AND <= 1000000)` |
| `capacity_limit` | int | nullable, `CHECK (capacity_limit IS NULL OR capacity_limit BETWEEN 1 AND 1000)` |
| `submission_count` | int | default 0 |
| `is_active` | bool | default true |
| `public_code` | text | `UNIQUE`, `secrets.token_urlsafe(12)` |
| `form_fields` | JSONB | the field definition, §7.1 |
| `deleted_at` | timestamptz | nullable |

`CHECK (form_type <> 'evaluation' OR capacity_limit IS NULL)` — evaluations are uncapped by definition.
`CHECK (submission_count <= COALESCE(capacity_limit, submission_count))` — oversell is a DB error.

`public_code` is 16 chars of URL-safe randomness (~96 bits): non-enumerable, so an anonymous scanner
cannot walk the tenant's forms.

### 7.1 `form_fields` — the stored schema

```json
[
  { "field_id": "participant_name", "field_type": "text", "label": "Participant Name",
    "required": true, "max_length": 120 },
  { "field_id": "email", "field_type": "email", "label": "Email", "required": true },
  { "field_id": "age", "field_type": "number", "label": "Age", "required": true,
    "min": 4, "max": 18 },
  { "field_id": "skill", "field_type": "dropdown", "label": "Skill Level",
    "required": false, "options": ["Beginner", "Intermediate", "Advanced"] }
]
```

Validated on **write** by a `FormFieldDefinition` Pydantic model before it ever reaches the database:

- `field_id` matches `^[a-z][a-z0-9_]{0,39}$` and is unique within the form. This is the injection
  guard — the id becomes an attribute name in a dynamically built model, so it must be a safe Python
  identifier by construction.
- `field_type` is a closed enum; unknown types are rejected at definition time.
- `options` required and non-empty for `dropdown`/`multiselect`, forbidden otherwise.
- 1–20 fields per form; at least one `required` field.
- Exactly one field of type `email` is required — the conversion flow and duplicate detection both
  depend on it.
- `label` ≤ 200 chars, stored raw and never rendered as HTML by this service.

### `form_submissions`
`id` · `form_id` FK · `trainer_id` FK (denormalised) · `submission_data` JSONB ·
`submitter_email` `citext` (extracted from the email field, indexed) ·
`payment_status` enum (`free`/`pending`/`paid`/`failed`) ·
`transaction_id` FK nullable (Epic-05) · `submitted_at` ·
`converted_user_id` FK nullable · `converted_at` nullable ·
`conversion_token_hash` text nullable · `conversion_token_expires_at` nullable ·
`attended` bool nullable (camps) · `attendance_marked_at` ·
`ip_hash` · `user_agent_hash`.

**`CREATE UNIQUE INDEX uq_submission_email ON form_submissions (form_id, submitter_email)
WHERE payment_status <> 'failed';`** — one submission per email per form, with failed payment
attempts excluded so a card decline does not lock someone out permanently.

Index `ix_submissions_form (form_id, submitted_at DESC)`.

### `form_templates` — seeded, read-only
`id` · `form_type` · `name` · `fields` JSONB. Two rows shipped by a data migration: a camp template
and an evaluation template. There is no endpoint to create one.

---

## 8. API surface

| Method | Path | Auth |
|:---|:---|:---|
| GET | `/api/v1/trainer/form-templates` | trainer |
| GET/POST | `/api/v1/trainer/forms` | trainer |
| GET/PATCH/DELETE | `/api/v1/trainer/forms/{id}` | trainer |
| POST | `/api/v1/trainer/forms/{id}/activate` \| `/deactivate` | trainer |
| GET | `/api/v1/trainer/forms/{id}/submissions` | trainer |
| GET | `/api/v1/trainer/forms/{id}/submissions/export` | trainer |
| PUT | `/api/v1/trainer/forms/{id}/attendance` | trainer |
| POST | `/api/v1/trainer/forms/{id}/broadcast` | trainer |
| POST | `/api/v1/trainer/submissions/{id}/invite` | trainer |
| **GET** | **`/api/v1/public/forms/{public_code}`** | **none** |
| **POST** | **`/api/v1/public/forms/{public_code}/submit`** | **none** |
| GET | `/api/v1/public/submissions/{id}/status?token=` | none (bearer-in-token) |
| POST | `/api/v1/auth/register?submission_token=` | none (Epic-01) |

The two bolded routes are the entire unauthenticated write surface of the platform. They get their
own threat model (§10) and their own test file.

---

## 9. User stories → backend tasks

### US-08.01 — Trainer creates a camp form

`GET /api/v1/trainer/form-templates` → `POST /api/v1/trainer/forms`

**Acceptance criteria**
- [ ] `POST` with `template_id` seeds `form_fields` from the template; without one, `form_fields` is required
- [ ] `form_fields` validated by `FormFieldDefinition` per §7.1; any violation ⇒ `422` naming the offending `field_id`
- [ ] Duplicate `field_id` within a form ⇒ `422 DUPLICATE_FIELD_ID`
- [ ] Missing an `email` field ⇒ `422 EMAIL_FIELD_REQUIRED`
- [ ] More than 20 fields ⇒ `422 TOO_MANY_FIELDS`
- [ ] `capacity_limit` 1–1000 for camps; supplied for an evaluation ⇒ `422 CAPACITY_NOT_ALLOWED`
- [ ] `price_cents` 0 or 100–1 000 000 ⇒ else `422`; a price above 0 with no connected Stripe account
      ⇒ `409 STRIPE_NOT_CONNECTED`
- [ ] `public_code` generated server-side; a client-supplied one is rejected by `extra="forbid"`
- [ ] `201` returns the form with `public_path = "/forms/{public_code}"`
- [ ] Gated by the `camps` feature flag ⇒ `403 FEATURE_DISABLED` when off

### US-08.02 — Trainer creates an evaluation form

Same endpoint, `form_type="evaluation"`.

**Acceptance criteria**
- [ ] `capacity_limit` must be null; `is_active` is forced true and `POST .../deactivate` ⇒
      `409 EVALUATION_ALWAYS_ACTIVE`
- [ ] Deletion is the only way to retire an evaluation
- [ ] Field types and validation are identical to camps — a parametrised test runs the definition
      suite against both `form_type` values

### US-08.03 — Non-user submits a form

`GET /api/v1/public/forms/{public_code}` → `POST /api/v1/public/forms/{public_code}/submit`

**GET acceptance criteria**
- [ ] Unauthenticated; returns `name`, `description`, display dates, `price_cents`, `form_fields`,
      `spots_remaining`, `is_open`, and the trainer's public branding (Epic-01 US-01.14)
- [ ] Unknown, deleted, or inactive code ⇒ `404 FORM_NOT_FOUND` — one code for all three, so the
      endpoint is not an oracle for which codes exist
- [ ] Returns **nothing** about the tenant beyond public branding: no trainer email, no counts beyond
      `spots_remaining`, no submission data. A field-level test asserts the response model
- [ ] Rate limited 60/min per IP; cached 30 s

**POST acceptance criteria**
- [ ] The submission body is validated against a Pydantic model **built at request time** from
      `form_fields`, with `extra="forbid"` — an unknown key ⇒ `422 UNKNOWN_FIELD`
- [ ] Compiled models are cached by `(form_id, form_fields_hash)`, so a hot form does not rebuild its
      validator per request
- [ ] Per-type validation: `email` RFC-parsed, `number` within `min`/`max`, `date` ISO-8601,
      `dropdown` value within `options`, `multiselect` a subset of `options`, `text` within `max_length`
- [ ] Every `required` field missing or empty ⇒ `422` listing all offending `field_id`s at once,
      not one at a time
- [ ] Inactive form or deleted form ⇒ `404 FORM_NOT_FOUND`
- [ ] Duplicate email on the same form ⇒ `409 ALREADY_SUBMITTED` (from the partial unique index)
- [ ] **Capacity** is taken by an atomic conditional UPDATE:
      `UPDATE forms SET submission_count = submission_count + 1
       WHERE id = :id AND (capacity_limit IS NULL OR submission_count < capacity_limit) RETURNING`;
      zero rows ⇒ `409 FORM_FULL`
- [ ] Free form ⇒ `payment_status='free'`, `201` immediately, `send_submission_confirmation` enqueued
- [ ] Priced form ⇒ `payment_status='pending'`, `201` with `checkout_url`; the seat is **held** during
      checkout and released by `expire_pending_submissions` after 30 minutes
- [ ] `checkout.session.completed` ⇒ `payment_status='paid'` + confirmation email;
      `checkout.session.expired` or payment failure ⇒ `payment_status='failed'` and the seat released
- [ ] **Anti-abuse**: 5 submissions/hour/IP, 20/hour/form; a honeypot field (`_hp`) present and
      non-empty ⇒ `201` with no row written (a silent drop, so bots learn nothing); `submitted_at`
      less than 3 s after the `GET` ⇒ `422 TOO_FAST`
- [ ] Only `ip_hash` and `user_agent_hash` are stored — never a raw IP
- [ ] **Concurrency**: 50 parallel submissions on a capacity-20 camp ⇒ exactly 20 × `201`,
      30 × `409 FORM_FULL`, `submission_count = 20`. Merge gate
- [ ] The response includes a `conversion_token` (single-use, 7-day) for US-08.05

### US-08.04 — Trainer reads submissions

`GET /api/v1/trainer/forms/{id}/submissions?payment_status=&converted=&q=`

**Acceptance criteria**
- [ ] Returns `submission_data` flattened against the current `form_fields`, plus `submitter_email`,
      `payment_status`, `submitted_at`, `converted`, `attended`
- [ ] A field added after a submission renders as `null` for that row; a removed field is still shown
      from `submission_data` under a `legacy_fields` key — historical answers are never lost by a
      later edit, and a test asserts both directions
- [ ] `payment_status='pending'` rows are excluded by default and included with `?include_pending=true`
- [ ] `failed` rows are always excluded from participant counts
- [ ] Filters combine with AND; `q` searches `submitter_email` and text field values
- [ ] Cursor-paginated; another tenant's form ⇒ `404`
- [ ] `GET .../export` streams CSV with one column per current field plus the metadata columns
- [ ] `PUT .../attendance` accepts `{submission_id, attended}` pairs and upserts in one statement;
      an evaluation form ⇒ `422 ATTENDANCE_CAMPS_ONLY`
- [ ] `POST .../broadcast` enqueues one `send_form_broadcast` per **paid or free** participant,
      subject and body required, ≤ 5 000 chars, rate limited 5/day/form

### US-08.05 — Submission converts to an account

`POST /api/v1/auth/register?submission_token=<token>` (Epic-01 endpoint, this epic's parameters)

**Acceptance criteria**
- [ ] The token is looked up by SHA-256 hash; unknown or expired ⇒ `404 INVALID_CONVERSION_TOKEN`
- [ ] The registration form is pre-filled from `submission_data`: name split, email, phone, and the
      player's age/birth date where the form captured them
- [ ] The email is **fixed to the submission's email** and not overridable ⇒ `422 EMAIL_MISMATCH`,
      because the token proves control of that address and nothing else
- [ ] The email already having an account ⇒ `409 ACCOUNT_EXISTS` with `details.action="login_then_link"`;
      an authenticated caller with that account may POST the token to link the submission instead
- [ ] On success: creates the account (Epic-01), creates `trainer_player_links` with
      `source='camp_conversion'`, sets `converted_user_id` and `converted_at`, and consumes the token
- [ ] The token is single-use — a replay ⇒ `404 INVALID_CONVERSION_TOKEN`
- [ ] Conversion is **optional**: a submission never expires into an account by itself
- [ ] `POST /api/v1/trainer/submissions/{id}/invite` mails a fresh conversion token (7-day) for the
      later-registration path; rate limited 3/day/submission
- [ ] A converted submission re-invited ⇒ `409 ALREADY_CONVERTED`

### US-08.06 — Trainer manages form settings

`PATCH /api/v1/trainer/forms/{id}` · `activate` / `deactivate` · `DELETE`

**Acceptance criteria**
- [ ] Editable: name, description, display dates, price, capacity, `form_fields`
- [ ] `capacity_limit` below `submission_count` ⇒ `409 CAPACITY_BELOW_SUBMISSIONS` with the count
- [ ] Editing `form_fields` after submissions exist requires `?confirm=true` ⇒ else
      `409 FORM_HAS_SUBMISSIONS` with `details.submission_count`; existing `submission_data` is
      never rewritten
- [ ] Changing `price_cents` does not affect completed submissions
- [ ] `deactivate` on a camp makes `GET /public/forms/{code}` return `404` and blocks submission;
      the trainer's own views are unaffected
- [ ] `DELETE` is a soft delete. It is refused with `409 FORM_HAS_PAID_SUBMISSIONS` when any
      submission is `paid` — refunds must be issued through Stripe first, since MVP has no automated
      camp refund flow
- [ ] A soft-deleted form's `public_code` is never reissued
- [ ] Another tenant's form ⇒ `404` on every route

---

## 10. Business rules & the public threat model

**Camps vs. evaluations** — a camp is capped, toggleable, and usually priced; an evaluation is
uncapped, always active, and often free. Dates are display strings; nothing in this epic touches the
calendar, and no `events` row is ever created.

**Dynamic validation** — the stored definition is validated strictly on write, then compiled to a
Pydantic model on read. Two rules make this safe:

1. `field_id` is constrained to `^[a-z][a-z0-9_]{0,39}$` at definition time, so it is always a legal,
   non-dunder Python identifier before it is ever used as one.
2. The compiled model is built with `create_model` from a whitelisted type map — the definition
   selects among known types, it never supplies one.

A test feeds hostile definitions (`__init__`, `__class__`, `field; import os`, 10 000-char ids,
unicode homoglyphs) and asserts every one is rejected at write time.

**The public surface** — `GET` and `POST /api/v1/public/forms/{code}` are the only routes reachable
without a token. Their defences, in order:

| Threat | Defence |
|:---|:---|
| Code enumeration | 96-bit random `public_code`; identical `404` for unknown, inactive, and deleted |
| Tenant reconnaissance | Response model carries public branding only; no counts beyond `spots_remaining` |
| Spam submission | 5/hour/IP, 20/hour/form; honeypot field; 3-second minimum fill time |
| Oversell | Atomic conditional UPDATE + `CHECK (submission_count <= capacity_limit)` |
| Duplicate registration | Partial unique index on `(form_id, submitter_email)` |
| Payment bypass | `payment_status` only reaches `paid` via a verified Stripe webhook |
| PII in logs | Only salted hashes of IP and user agent are stored; structlog redacts by key |
| Injection via definition | §10 rules 1 and 2 above |

**Capacity** — held from submission through checkout and released after 30 minutes of `pending`. The
hold duration is configurable per deployment.

**Payment** — Stripe Checkout with the trainer's Connect account and the standard application fee.
Refunds are **manual via the Stripe dashboard** in MVP; the platform issues none automatically, which
is why deleting a form with paid submissions is refused rather than cascading.

**Conversion** — optional, token-gated, single-use, 7-day. The token binds the email; nothing else
about the submission is trusted at registration time.

**Feature flag** — every route here sits behind Epic-07's `camps` toggle. When off: trainer routes
`403 FEATURE_DISABLED`, and public form routes return `404` — a disabled tenant's public links go
dark rather than announcing that the feature was switched off.

---

## 11. Backend flows

**Priced submission**
```
POST /public/forms/{code}/submit
  ├─ rate limit (IP, form) · honeypot · min-fill-time
  ├─ load form (active, not deleted, camps flag on) → else 404
  ├─ build/lookup cached Pydantic model from form_fields
  ├─ validate body (extra="forbid") → 422 with all offending field_ids
  ├─ BEGIN
  │    UPDATE forms SET submission_count = submission_count + 1
  │      WHERE id=:f AND (capacity_limit IS NULL OR submission_count < capacity_limit)
  │      RETURNING submission_count            ← 0 rows ⇒ 409 FORM_FULL, ROLLBACK
  │    INSERT form_submissions (pending)       ← unique (form, email) ⇒ 409 ALREADY_SUBMITTED
  │    INSERT transactions (pending, kind=camp_registration)   [Epic-05]
  │  COMMIT
  ├─ stripe.checkout.Session.create(idempotency_key=f"submission:{id}")
  └─ 201 {submission_id, checkout_url, conversion_token}

webhook checkout.session.completed
  └─ payment_status='paid' → enqueue send_submission_confirmation

expire_pending_submissions (cron, 5 min)
  └─ pending older than 30 min → 'failed', submission_count -= 1
```

**Conversion**
```
POST /auth/register?submission_token=T
  ├─ hash T → load submission, assert not expired, not converted
  ├─ assert body.email == submission.submitter_email     → else 422
  ├─ BEGIN
  │    Epic-01 create account (users, profiles, player_profiles)
  │    INSERT trainer_player_links (source='camp_conversion')
  │    UPDATE form_submissions SET converted_user_id, converted_at,
  │           conversion_token_hash = NULL
  │  COMMIT
  └─ 201 + token pair
```

---

## 12. Background jobs

| Job | Trigger | Effect | Idempotency key |
|:---|:---|:---|:---|
| `send_submission_confirmation` | submission free or paid | confirmation + conversion link | `submission_id + payment_status` |
| `send_conversion_invite` | trainer invite, or 24 h after an unconverted submission | ShareLink email | `submission_id + token_id` |
| `expire_pending_submissions` | cron, 5 min | `pending` > 30 min ⇒ `failed`, release the seat | per-row guard |
| `send_form_broadcast` | trainer broadcast | one email per participant | `broadcast_id + submission_id` |
| `expire_conversion_tokens` | cron, daily | null out tokens past expiry | per-row guard |
| `reconcile_submission_counts` | cron, nightly | correct `submission_count` drift against `COUNT(*)` | idempotent |

---

## 13. Performance & scale targets

| Operation | Target | Verification |
|:---|:---|:---|
| `GET /public/forms/{code}` | p95 < 200 ms, < 50 ms cached | k6 |
| `POST /public/forms/{code}/submit` free | p95 < 400 ms | k6 |
| `POST .../submit` priced (excl. Stripe) | p95 < 800 ms | k6 with a stubbed PSP |
| Dynamic model compilation, cached / cold | < 1 ms / < 20 ms | micro-benchmark |
| `GET /trainer/forms/{id}/submissions` 500 rows | p95 < 500 ms | k6 |
| CSV export 1 000 submissions | streams, peak RSS < 100 MB | integration test |
| 50 concurrent submissions on a capacity-20 camp | 0 oversell | e2e concurrency test (merge gate) |
| Sustained public load | 100 submissions/min without degradation | load suite |

---

## 14. Open questions

| ID | Question | Priority | Status |
|:---|:---|:---:|:---|
| Q-08.01 | Maximum custom fields per form | P2 | **Resolved here**: 20 |
| Q-08.02 | Form analytics (completion, drop-off) | P2 | Open — Phase 2; the backend cannot see drop-off without client telemetry it does not collect |
| Q-08.03 | Evaluation time-slot selection | P2 | Open — Phase 2; needs calendar integration |
| Q-08.04 | Camp refund policy | P2 | **Resolved here**: manual via Stripe; automated refunds out of MVP |
| Q-08.05 | Should a paid submission auto-create an account? | P2 | Open — MVP keeps conversion optional |

---

## 15. Epic Definition of Done

Conventions §10 plus:

- [ ] All 6 user stories implemented per §8
- [ ] **50-way concurrent submission on a capacity-20 camp ⇒ exactly 20 succeed** — merge gate
- [ ] **Hostile field-definition suite rejected at write time** (dunder ids, injection strings,
      oversized ids, homoglyphs) — merge gate
- [ ] Dynamic model built with `extra="forbid"`; an unknown key ⇒ `422`
- [ ] All missing required fields reported in one `422`, not sequentially
- [ ] Duplicate email rejected by the partial unique index; a failed payment does not lock the email out
- [ ] Public endpoint response model asserted to expose no tenant data beyond public branding
- [ ] Rate limits verified (`429`) for both IP and form scopes
- [ ] Honeypot verified to drop silently with no row written
- [ ] Capacity release verified: expire a `pending` submission and re-submit successfully
- [ ] `payment_status` proven unreachable as `paid` without a verified webhook
- [ ] Field add/remove after submissions: added ⇒ `null`, removed ⇒ `legacy_fields`, nothing lost
- [ ] Conversion token single-use and email-bound, both asserted
- [ ] Deleting a form with paid submissions refused
- [ ] Feature-flag test: `camps` off ⇒ trainer `403`, public `404`, data intact on re-enable
- [ ] Cross-tenant `404` on every trainer route
- [ ] Only salted hashes of IP and user agent present in the database — asserted by a schema scan
- [ ] Load targets in §13 met
