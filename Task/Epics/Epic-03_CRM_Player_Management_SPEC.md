# Epic-03 (Python Backend): CRM & Player Management

> Stack, layering, error contract, pagination, tenancy and testing rules: [00_Backend_Conventions.md](00_Backend_Conventions.md).

**Priority**: P1 — business operations
**Complexity**: Medium-High (query-heavy: segmentation, aggregates, coach scoping)
**User stories**: 13

---

## 1. Description

The trainer-facing read model over the player base: directory, search, labels, system flags, notes,
segmentation, aggregate metrics, and a deliberately narrow coach view.

Scope boundary: this epic **owns no source data**. Players come from Epic-01,
attendance from Epic-02, purchases from Epic-05. Epic-03 owns the *annotations* (labels, flags,
notes) and the *query surface* over everything else. The engineering focus is therefore
query performance, not state transitions.

## 2. Backend outcomes

- A composable filter/segmentation layer that stays a single indexed query as criteria multiply.
- Attendance and engagement aggregates that do not scan history on every dashboard load.
- Coach access scoped by *event assignment*, enforced in the query, not by post-filtering.
- Annotation edit windows and authorship rules enforced server-side.

---

## 3. In scope (MVP)

### Trainer CRM
- [ ] Player directory, cursor-paginated, sortable by name / last activity / attendance rate
- [ ] Tool-scoped search: player name, parent name, parent email, school, team, club
- [ ] Player detail: profile + labels + flags + notes + event history + attendance aggregates
- [ ] Custom labels: CRUD, tenant-scoped, applied many-to-many
- [ ] 8 system flags with optional note, apply / resolve, full history
- [ ] Notes: general and per-event, authored by trainer or coach, 24 h edit window
- [ ] Segmentation: skill, age range, gender, labels, flags, school/team, attendance metrics,
      registration date, last activity — combined with AND
- [ ] ShareLink issuing + open/join tracking (rows owned by Epic-01, read here)

### Quick View metrics
- [ ] Events this week + week-over-week delta
- [ ] RSVPs this week by event type
- [ ] Top Players — events attended in the last 90 days, top 10
- [ ] Active flag counts by type
- [ ] ShareLink opens and joins this week
- [ ] Attendance rate and no-show rate, this week vs. last
- [ ] Coach hours: sessions covered and hours delivered per coach (reporting only — the platform
      never pays coaches)

### Coach view
- [ ] Players scoped to the coach's **assigned events only**
- [ ] Player detail limited to that coach's shared history
- [ ] Session feedback create / edit within 24 h
- [ ] Labels and flags read-only
- [ ] ShareLink issuing

### Super Admin
- [ ] Cross-tenant player query with filters
- [ ] Apply / resolve flags across tenants
- [ ] System-wide Quick View with per-trainer drill-down

---

## 4. Out of scope (Post-MVP)

Advanced analytics and charts · player data export beyond CSV · saved segments · automated
flagging · in-app messaging · parent-facing coach feedback · Build-a-Bag · bulk label/flag
operations · structured feedback ratings · player comparison · lifetime-value attribution ·
weighted Top Players · regional breakdowns.

---

## 5. Dependencies

**Required before**: Epic-01 (players, links, ShareLinks), Epic-02 (events, RSVPs, attendance).

**Blocks**: Epic-04 (assignment by label / segment), Epic-06 (referral stats surface here).

**Consumes**: `trainer_player_links`, `events`, `rsvps`, `attendance_records`, `share_link_events`,
`coach_assignments`.

---

## 6. Roles & permission matrix

| Capability | super_admin | trainer | coach |
|:---|:---:|:---:|:---:|
| List players | ✅ all tenants | ✅ own tenant | ✅ own assigned events only |
| Read player detail | ✅ full | ✅ full, own tenant | ✅ reduced, shared events only |
| Create / edit / delete label | ✅ | ✅ own tenant | ❌ |
| Apply / remove label | ✅ | ✅ | ❌ |
| Apply flag | ✅ any tenant | ✅ own tenant | ⚠️ Q-03.05 — off by default |
| Resolve flag | ✅ | ✅ | ❌ |
| Add general note | ✅ | ✅ | ❌ |
| Add session feedback | ✅ | ✅ | ✅ own sessions |
| Edit own note | ✅ anytime | ✅ ≤ 24 h | ✅ ≤ 24 h |
| Edit another author's note | ✅ | ❌ | ❌ |
| Read Quick View | ✅ system-wide | ✅ own tenant | ❌ |
| Issue ShareLink | ✅ | ✅ | ✅ |

---

## 7. Domain model

`app/models/crm.py`.

### `labels`
`id` · `trainer_id` FK · `name` varchar(50) · `color` `CHAR(7)` hex · `created_by_user_id` · `created_at`.
`CREATE UNIQUE INDEX uq_label_name_ci ON labels (trainer_id, lower(name));` — names are
case-insensitively unique within a tenant, and "Elite" for trainer A is a different row from
"Elite" for trainer B.

### `player_labels`
`player_profile_id` FK · `label_id` FK · `applied_by_user_id` · `applied_at`.
PK `(player_profile_id, label_id)`. Index on `label_id` for reverse lookup and the usage count.

### `player_flags`
`id` · `player_profile_id` FK · `trainer_id` FK · `flag_type` enum · `note` text nullable ·
`applied_by_user_id` · `applied_at` · `status` enum (`active`/`resolved`) ·
`resolved_by_user_id` nullable · `resolved_at` nullable.

`flag_type` enum — exactly these 8, no custom values:
`behavior`, `high_no_show`, `injured`, `medical_restriction`, `scholarship`, `financial_aid`,
`contact_priority`, `attendance_risk`.

`CREATE UNIQUE INDEX uq_active_flag ON player_flags (player_profile_id, trainer_id, flag_type) WHERE status = 'active';`
— one active instance per type per player per tenant; resolved rows stay as history and a flag may
be re-applied after resolution.

### `player_notes`
`id` · `player_profile_id` FK · `trainer_id` FK · `event_id` FK nullable ·
`note_type` enum (`general`/`session_feedback`) · `body` text `CHECK (length(body) <= 1000)` ·
`author_user_id` · `author_role` enum · `created_at` · `updated_at` · `deleted_at` nullable.

`CHECK (note_type <> 'session_feedback' OR event_id IS NOT NULL)`.
Index `ix_notes_player (player_profile_id, created_at DESC) WHERE deleted_at IS NULL`.
Session feedback is capped at 500 chars by the Pydantic schema; the DB cap is the outer bound.

### `player_engagement_stats` — materialised aggregate
`player_profile_id` · `trainer_id` · PK on the pair ·
`events_registered` · `events_attended` · `no_show_count` · `attendance_rate` numeric(5,2) ·
`attended_last_90d` · `last_event_at` · `last_activity_at` · `first_linked_at` · `computed_at`.

Refreshed incrementally by `refresh_player_engagement` (§12) on attendance write and RSVP change,
and fully rebuilt nightly. **Segmentation and sorting read this table, never the raw history** —
that is what keeps a 500-player filtered query under 500 ms.

### `trainer_daily_metrics` — Quick View source
`trainer_id` · `metric_date` · PK on the pair ·
`events_count` · `rsvp_count` · `attended_count` · `no_show_count` ·
`share_link_opens` · `share_link_joins` · `new_players` · `computed_at`.

Written by `rollup_trainer_daily_metrics` (nightly + hourly for the current day). Quick View sums
rows over a date range instead of scanning `events`/`rsvps`.

### `coach_workload_stats`
`coach_user_id` · `trainer_id` · PK on the pair · `events_covered` · `hours_delivered` numeric(8,2) ·
`players_served` · `computed_at`. Reporting only — no payment semantics anywhere in this epic.

---

## 8. API surface

| Method | Path | Role |
|:---|:---|:---|
| GET | `/api/v1/trainer/players` | trainer |
| GET | `/api/v1/trainer/players/{id}` | trainer |
| PATCH | `/api/v1/trainer/players/{id}` | trainer (skill level only) |
| GET | `/api/v1/trainer/players/{id}/events` | trainer |
| GET | `/api/v1/trainer/players/export` | trainer (CSV) |
| GET/POST | `/api/v1/trainer/labels` | trainer |
| PATCH/DELETE | `/api/v1/trainer/labels/{id}` | trainer |
| PUT | `/api/v1/trainer/players/{id}/labels` | trainer (replace set) |
| GET/POST | `/api/v1/trainer/players/{id}/flags` | trainer |
| POST | `/api/v1/trainer/flags/{id}/resolve` | trainer |
| GET/POST | `/api/v1/trainer/players/{id}/notes` | trainer, coach |
| PATCH/DELETE | `/api/v1/trainer/notes/{id}` | author, ≤ 24 h |
| GET | `/api/v1/trainer/dashboard` | trainer |
| GET | `/api/v1/trainer/dashboard/top-players` | trainer |
| GET | `/api/v1/trainer/coaches/workload` | trainer |
| GET | `/api/v1/coach/players` | coach |
| GET | `/api/v1/coach/players/{id}` | coach |
| POST | `/api/v1/coach/players/{id}/feedback` | coach |
| GET | `/api/v1/admin/players` | super_admin |
| GET | `/api/v1/admin/dashboard` | super_admin |

---

## 9. User stories → backend tasks

### US-03.01 — Trainer lists players

`GET /api/v1/trainer/players?sort=&limit=&cursor=`

**Acceptance criteria**
- [ ] Returns players with an `active` `trainer_player_links` row for this tenant — nothing else
- [ ] Each row: display name, avatar URL, age, gender, skill level, labels, active flags,
      `attendance_rate`, `last_activity_at`
- [ ] Labels and flags are loaded with `selectinload`, not per-row queries; a query-count test caps
      the endpoint at 3 statements regardless of page size
- [ ] `sort` ∈ `name_asc`, `name_desc`, `last_activity_desc`, `attendance_rate_desc`; anything else ⇒ `422`
- [ ] Sorting reads `player_engagement_stats`, never a computed aggregate over `attendance_records`
- [ ] Cursor pagination is stable under concurrent writes (cursor encodes the sort key **and** id)
- [ ] Another tenant's player never appears; a direct id fetch of one ⇒ `404`
- [ ] p95 < 500 ms at 500 players (§13)

### US-03.02 — Trainer searches players

`GET /api/v1/trainer/players?q=`

**Acceptance criteria**
- [ ] `q` matches player display name, parent first/last name, parent email, school, team, club
- [ ] Case- and accent-insensitive; partial match ("John" finds "Johnny")
- [ ] Implemented as a Postgres `pg_trgm` GIN index over a generated search vector column — not `ILIKE '%...%'` on six columns
- [ ] Scoped to the tenant **inside** the query, so a match in another tenant is never even ranked
- [ ] `q` shorter than 2 chars ⇒ `422 QUERY_TOO_SHORT`
- [ ] Empty result ⇒ `200` with `items: []`, never `404`
- [ ] p95 < 300 ms at 5 000 tenant players

### US-03.03 — Trainer manages labels

`POST /api/v1/trainer/labels` · `PATCH|DELETE /api/v1/trainer/labels/{id}` ·
`PUT /api/v1/trainer/players/{id}/labels`

**Acceptance criteria**
- [ ] Create: `name` ≤ 50, `color` matching `^#[0-9A-Fa-f]{6}$` ⇒ else `422`
- [ ] Case-insensitive duplicate name in the tenant ⇒ `409 LABEL_EXISTS` (from the unique index)
- [ ] `GET /labels` returns each label with `usage_count` from a single grouped query
- [ ] `PUT .../labels` replaces the player's label set atomically (delete + insert in one transaction);
      partial application is impossible
- [ ] A label id from another tenant ⇒ `404`
- [ ] `DELETE` requires `?confirm=true` when `usage_count > 0` ⇒ else `409 LABEL_IN_USE` with the count;
      confirmed deletion removes the label and all its `player_labels` rows in one transaction
- [ ] Deleting a label never touches the player rows themselves

### US-03.04 — Trainer applies system flags

`POST /api/v1/trainer/players/{id}/flags` · `POST /api/v1/trainer/flags/{id}/resolve`

**Acceptance criteria**
- [ ] `flag_type` outside the 8-value enum ⇒ `422`; there is no endpoint to create flag types
- [ ] Optional `note` ≤ 500 chars
- [ ] Applying a type already `active` for this player+tenant ⇒ `409 FLAG_ALREADY_ACTIVE`
- [ ] Resolve sets `status=resolved` with resolver and timestamp; the row is retained as history
- [ ] Re-applying after resolution creates a **new** row — the history shows both episodes
- [ ] `GET .../flags?status=all` returns active and resolved, newest first
- [ ] Flags are tenant-scoped: trainer A's `injured` flag is invisible to trainer B
- [ ] Super Admin may apply and resolve across tenants; each such write is audited
- [ ] Coach flag permission is behind the `coach_can_flag` trainer setting, default **off** (Q-03.05)

### US-03.05 — Trainer adds notes

`POST /api/v1/trainer/players/{id}/notes` · `PATCH|DELETE /api/v1/trainer/notes/{id}`

**Acceptance criteria**
- [ ] `note_type=general` requires no `event_id`; `session_feedback` requires one ⇒ else `422`
- [ ] `event_id` must belong to this tenant and the player must have an RSVP on it ⇒ else `422 PLAYER_NOT_ON_EVENT`
- [ ] `body` ≤ 1000 chars (500 for session feedback) ⇒ else `422`
- [ ] Author and author role are taken from the token, never the body
- [ ] `PATCH` allowed only by the author and only within 24 h of `created_at` ⇒ else `403 EDIT_WINDOW_CLOSED`
- [ ] A trainer editing a coach's note ⇒ `403`; a coach editing a trainer's note ⇒ `403`
- [ ] `DELETE` is a soft delete (`deleted_at`), author-only, and follows the same window; super_admin bypasses both
- [ ] `GET .../notes` returns newest-first with author name and role, excluding soft-deleted rows
- [ ] The 24 h boundary is tested at both sides with an injected clock

### US-03.06 — Trainer segments and filters players

`GET /api/v1/trainer/players` with a `PlayerFilterParams` model

```
?skill_level=beginner,intermediate
&min_age=8&max_age=12
&gender=female
&label_ids=<uuid>,<uuid>
&flag_types=attendance_risk
&school=Lincoln
&attended_min=5
&attendance_rate_min=80
&no_shows_min=2
&attended_since=2026-06-01
&registered_from=&registered_to=
&activity=active|dormant|churned
```

**Acceptance criteria**
- [ ] All criteria combine with **AND**; multiple values within one criterion combine with **OR**
- [ ] `label_ids` semantics: a player must carry **every** listed label (AND across labels) — documented, tested
- [ ] `activity` maps to `last_activity_at`: `active` ≤ 30 d, `dormant` 30–90 d, `churned` > 90 d
- [ ] Attendance criteria read `player_engagement_stats`; the emitted SQL is a single statement,
      asserted by a query-count test
- [ ] Response envelope carries `total_matching` alongside the page (a windowed `COUNT(*) OVER ()`, not a second round trip)
- [ ] Unknown query params ⇒ `422` (`extra="forbid"` on the filter model)
- [ ] A label or flag id from another tenant ⇒ `404`
- [ ] Empty result ⇒ `200` with `items: []` and `total_matching: 0`
- [ ] p95 < 600 ms with 5 criteria over 5 000 players (§13)

### US-03.07 — Trainer reads player detail

`GET /api/v1/trainer/players/{id}` · `GET /api/v1/trainer/players/{id}/events`

**Acceptance criteria**
- [ ] Detail returns profile, parent contact (when the player is a dependent), availability windows,
      labels, active + resolved flags, recent notes (10), engagement stats, coach feedback
- [ ] Event history is a **separate paginated** endpoint — never inlined, so a player with 500 events
      does not blow up the detail payload
- [ ] History rows: date, event title, attendance status, per-event notes, payment method
- [ ] Stats block: `events_registered`, `events_attended`, `no_show_count`, `attendance_rate`,
      `attended_last_90d`, `last_event_at` — read from `player_engagement_stats`
- [ ] All of it in ≤ 4 queries; asserted by a query-count test
- [ ] `PATCH` accepts `skill_level` only; any other field ⇒ `422`. Names, email, and DOB belong to the
      account owner (Epic-01), not the trainer
- [ ] Another tenant's player ⇒ `404`

### US-03.08 — Trainer reads the Quick View dashboard

`GET /api/v1/trainer/dashboard?period=week` · `GET /api/v1/trainer/dashboard/top-players`

**Acceptance criteria**
- [ ] Returns `events_this_period`, `events_previous_period`, `delta`; `rsvps_by_event_type`;
      `active_flag_counts` keyed by all 8 types (zeros included); `share_link_opens`,
      `share_link_joins`; `attendance_rate`, `no_show_rate` with previous-period comparison
- [ ] Every number is summed from `trainer_daily_metrics` — no scan of `events` or `rsvps`
- [ ] Cached in Redis for 5 minutes, keyed by `(trainer_id, period)`; the cache is invalidated on
      attendance write for the current day
- [ ] `period` ∈ `week`, `month`, `quarter` ⇒ else `422`
- [ ] Top Players: attended (`present` + `late`) in the last 90 days, descending, ties broken
      alphabetically by display name, limit 10, players with 0 attendance excluded
- [ ] Fewer than 10 qualifying players ⇒ returns all of them
- [ ] Top Players is a single query over `player_engagement_stats.attended_last_90d`
- [ ] p95 < 400 ms cold, < 50 ms warm

### US-03.08b — Trainer reads coach workload

`GET /api/v1/trainer/coaches/workload?from=&to=`

**Acceptance criteria**
- [ ] Per coach: `events_covered`, `hours_delivered`, `players_served`, `last_event_at`
- [ ] Counts only assignments with `status=confirmed` and events with `status=completed`
- [ ] Hours = `SUM(ends_at - starts_at)` over those events
- [ ] Response carries no monetary field whatsoever — the platform does not pay coaches, and the
      schema must make that unmistakable
- [ ] Scoped to the tenant's own coaches ⇒ another tenant's coach never appears

### US-03.09 — Coach lists assigned players

`GET /api/v1/coach/players`

**Acceptance criteria**
- [ ] Returns exactly the players holding a `registered` RSVP on an event where the caller has a
      `confirmed` assignment — derived by a join, never by fetching the tenant roster and filtering
- [ ] A coach with zero assignments ⇒ `200` with `items: []`
- [ ] Each row: display name, avatar, skill level, `events_with_this_coach` count
- [ ] `?q=` searches within that scope only
- [ ] A player linked to the tenant but never on this coach's events ⇒ absent from the list, and
      `GET /coach/players/{id}` on them ⇒ `404`
- [ ] The scoping test is a merge gate: create tenant with 50 players, assign the coach to one event
      with 3 attendees, assert exactly 3 rows

### US-03.09b — Coach reads a player's detail

`GET /api/v1/coach/players/{id}`

**Acceptance criteria**
- [ ] Returns display name, age, skill level, labels (read-only), active flags (read-only),
      attendance history **restricted to shared events**, and this coach's own past feedback
- [ ] Never returns parent contact details, notes authored by the trainer, or payment information
- [ ] A field-level test asserts the coach response model contains no PII fields beyond that list
- [ ] Attempting any write on the trainer routes ⇒ `403`

### US-03.10 — Coach adds session feedback

`POST /api/v1/coach/players/{id}/feedback`

```json
{ "event_id": "...", "body": "Great improvement on footwork." }
```

**Acceptance criteria**
- [ ] `event_id` must be an event the coach is `confirmed` on and the player attended ⇒ else `422`
- [ ] `body` ≤ 500 chars
- [ ] Stored as `player_notes(note_type='session_feedback', author_role='coach')`
- [ ] Visible to the trainer and super_admin; **not** exposed on any player-facing endpoint in MVP (Q-03.01)
- [ ] Editable and deletable by the author within 24 h only; the trainer cannot edit it
- [ ] Duplicate feedback for the same (coach, event, player) ⇒ allowed, appended — coaches may add
      more than one observation

### US-03.11 — Coach invites a player via ShareLink

`POST /api/v1/coach/share-links`

**Acceptance criteria**
- [ ] Issues a `player_static`-kind link whose `trainer_id` is the coach's own trainer and whose
      `created_by_user_id` is the coach
- [ ] The resulting player links to the **trainer's** tenant, not to the coach
- [ ] `GET /api/v1/coach/share-links` returns the coach's own links with `opens` and `joins` counts
- [ ] A coach cannot issue a `coach_unique` link ⇒ `403`
- [ ] A player who joins via a coach link is visible to that coach only once they RSVP to one of the
      coach's events — the coach view scope in US-03.09 is not widened by having issued the link

### US-03.12 — Super Admin queries the system-wide CRM

`GET /api/v1/admin/players` · `GET /api/v1/admin/dashboard`

**Acceptance criteria**
- [ ] Cross-tenant listing with `trainer_id`, `skill_level`, `gender`, age range, `flag_types`,
      registration date range, `activity`, and `q`
- [ ] Each row names the tenant; a player linked to several tenants appears once per link, and the
      response documents that explicitly
- [ ] Super Admin may apply and resolve flags on any tenant; each write is audited
- [ ] Super Admin may **not** create or edit tenant-owned labels ⇒ `403 TENANT_OWNED_RESOURCE`
- [ ] `admin/dashboard` sums `trainer_daily_metrics` across tenants; `?trainer_id=` drills into one
- [ ] A `trainer` calling any `/admin/*` route ⇒ `403`

### US-03.13 — Trainer invites players via ShareLink

`GET|POST /api/v1/trainer/share-links` (Epic-01 rows, CRM-facing read)

**Acceptance criteria**
- [ ] A tenant has exactly one active `player_static` link; a second `POST` returns the existing one
      (`200`) rather than creating another
- [ ] Optional unique per-recipient links carry `target_email` and appear separately
- [ ] `GET` returns `opens`, `joins`, `last_opened_at`, and conversion rate per link, aggregated from
      `share_link_events` in one grouped query
- [ ] `GET /api/v1/public/share-links/{code}` records an `opened` event, rate-limited to 20/hour/IP,
      storing only a salted IP hash
- [ ] Opens and joins feed `trainer_daily_metrics` and appear in Quick View within one hourly rollup
- [ ] Another tenant's link code returns the *other* tenant's public preview and never any CRM data —
      a test asserts the public endpoint exposes only business name and link kind

---

## 10. Business rules (service layer)

**Association** — a player enters a tenant's CRM through exactly one of: ShareLink redemption, an
RSVP to that tenant's event, a coach invitation, or camp conversion (Epic-08). `trainer_player_links.source`
records which. Multi-tenant players are separate links; no tenant ever sees another's annotations.

**Labels** — tenant-owned, case-insensitively unique, many-to-many, deletion cascades only to
`player_labels`.

**Flags** — the 8 types are a closed enum. At most one active instance per (player, tenant, type).
Resolution preserves history; re-application starts a new episode. Auto-flagging
(`high_no_show` > 3 in 30 d, `attendance_risk` after 60 d idle) is **Phase 2** — the job stubs exist
but are not scheduled (Q-03.04).

**Notes** — author-only edits within 24 h; roles never edit across each other; super_admin
overrides both. Soft delete only. Session feedback requires attendance on the referenced event.

**Attendance semantics** (shared with Epic-02) — `present` and `late` count as attended; `absent`
and `excused` do not. `attendance_rate = events_attended / events_registered`, `NULL` (not 0) when
`events_registered = 0`, so a new player is not mistaken for a bad attender in sorts and filters.

**Top Players** — attendance count over the trailing 90 days, ties alphabetical, zero excluded.

**Aggregates** — `player_engagement_stats` and `trainer_daily_metrics` are derived and disposable.
Both have a full-rebuild job that must reproduce them exactly from source; the correctness test
mutates history, runs the incremental path, runs the rebuild, and asserts the two agree.

**Coach scope** — the join
`coach_assignments (confirmed) → events → rsvps (registered) → player_profiles` **is** the coach's
world. No endpoint widens it, including endpoints the coach reaches through their own ShareLink.

---

## 11. Backend flows

**Filtered directory query**
```
GET /trainer/players?<filters>
  ├─ parse into PlayerFilterParams (extra="forbid")
  ├─ base: trainer_player_links (trainer_id=:t, status='active')
  │        JOIN player_profiles
  │        JOIN player_engagement_stats (trainer_id=:t)
  ├─ conditional joins: player_labels (HAVING COUNT(DISTINCT label_id) = :n)
  │                     player_flags (status='active')
  ├─ WHERE ... AND ... (all criteria)
  ├─ COUNT(*) OVER () AS total_matching
  ├─ ORDER BY <sort_key>, id   |  keyset from cursor
  └─ selectinload labels + flags for the page  → 3 statements total
```

**Attendance write → aggregate refresh**
```
Epic-02 attendance commit
  └─ enqueue refresh_player_engagement(player_profile_id, trainer_id)
        recompute the one pair's row (bounded query over that player's history)
        UPSERT player_engagement_stats
        invalidate dashboard cache key (trainer_id, 'week')
```

---

## 12. Background jobs

| Job | Trigger | Effect | Idempotency key |
|:---|:---|:---|:---|
| `refresh_player_engagement` | attendance write, RSVP create/cancel | upsert one `player_engagement_stats` row | `(player, trainer)` — recompute is naturally idempotent |
| `rebuild_player_engagement` | cron, nightly | full recompute for every pair; logs drift found | — |
| `rollup_trainer_daily_metrics` | cron, hourly (today) + nightly (yesterday) | upsert `trainer_daily_metrics` | `(trainer_id, metric_date)` |
| `rollup_coach_workload` | cron, nightly | upsert `coach_workload_stats` | `(coach, trainer)` |
| `refresh_player_search_vector` | profile or parent-profile write | update the trigram search column | per-row |
| `auto_flag_high_no_show` | **Phase 2, unscheduled** | apply `high_no_show` | `(player, trainer, flag_type, window)` |

---

## 13. Performance & scale targets

| Operation | Target | Verification |
|:---|:---|:---|
| `GET /trainer/players` 500 players, page 50 | p95 < 500 ms, ≤ 3 queries | k6 + query-count test |
| `GET /trainer/players?q=` 5 000 players | p95 < 300 ms | trigram index, `EXPLAIN ANALYZE` |
| Filtered segmentation, 5 criteria, 5 000 players | p95 < 600 ms, 1 statement | query-count + k6 |
| `GET /trainer/players/{id}` | p95 < 300 ms, ≤ 4 queries | query-count test |
| `GET /trainer/dashboard` cold / warm | p95 < 400 ms / < 50 ms | k6 with Redis |
| Top Players over 5 000 players | p95 < 200 ms | index on `attended_last_90d` |
| `refresh_player_engagement` | < 100 ms per pair | integration timing test |
| CSV export 5 000 players | streams, peak RSS < 100 MB | integration test |

---

## 14. Open questions

| ID | Question | Priority | Blocks |
|:---|:---|:---:|:---|
| Q-03.01 | May players/parents read coach feedback? | P2 | a player-facing feedback endpoint |
| Q-03.04 | Auto-flag `high_no_show` at > 3 in 30 d — MVP or Phase 2? | P2 | scheduling `auto_flag_high_no_show` |
| Q-03.05 | May coaches apply flags, or trainers only? | P2 | `coach_can_flag` setting default |
| Q-03.07 | Label usage analytics ("on 12 players, 3 dormant")? | P2 | an extra aggregate on `GET /labels` |

Assumptions where unresolved: feedback is trainer-visible only; auto-flagging is Phase 2 (job
implemented, not scheduled); `coach_can_flag` defaults to **off**.

---

## 15. Epic Definition of Done

Conventions §10 plus:

- [ ] All 13 user stories implemented per §8
- [ ] **Coach scoping test is a merge gate**: 50-player tenant, coach on one 3-attendee event ⇒ exactly 3 visible
- [ ] Query-count tests hold the listing at ≤ 3 and the detail at ≤ 4 statements
- [ ] Segmentation emits exactly one statement for any legal criteria combination
- [ ] Aggregate correctness test: incremental refresh and full rebuild agree after mutated history
- [ ] Note 24 h edit window tested on both sides with an injected clock
- [ ] Cross-tenant `404` on every player, label, flag, and note route
- [ ] Coach response model asserted to contain no parent contact or payment fields
- [ ] Trigram search index present and used (`EXPLAIN ANALYZE` in the test log)
- [ ] Dashboard cache invalidation tested against a same-day attendance write
- [ ] Load targets in §13 met against a 5 000-player seed
