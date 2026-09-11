# Epic-04 (Python Backend): LP Content System (Learn · Practice)

> Stack, layering, error contract, pagination, tenancy and testing rules: [00_Backend_Conventions.md](00_Backend_Conventions.md).

**Priority**: P1 — differentiator and upsell
**Complexity**: Medium-High (cross-tenant public library, ordered collections, paywall gating)
**User stories**: 11

---

## 1. Description

Playlist-driven content: **Learn** (tutorial sequences) and **Practice** (drill workouts). The two
pillars are *functionally identical* — one implementation with a `pillar` discriminator, not two
subsystems. Videos are external (YouTube unlisted URLs); the service stores links and metadata, never
media bytes.

Scope boundary: this epic owns content, ordering, assignment, and progress. It owns
the **access decision** for paywalled content but not the payment itself — Epic-05 grants an entitlement,
Epic-04 reads it.

Content is normally tenant-scoped, but **public content is cross-tenant by design**. This is the one
place in the system where the isolation rule of Conventions §3 is relaxed; the relaxation is narrow,
explicit, and tested from both directions.

## 2. Backend outcomes

- One content model serving both pillars, with a discriminator rather than duplicated tables.
- Reference-not-copy reuse of public content, with references that survive the source going private.
- An entitlement check that is a single indexed lookup, callable from every content route.
- Ordered collections that reorder without integer-index rewrites of the whole list.

---

## 3. In scope (MVP)

### Content authoring (trainer)
- [ ] Create Learn playlists: title, description, skill/position/age targeting, visibility
- [ ] Add video items: YouTube URL, title, rich-text instructions, tags, duration
- [ ] Create drills: name, instructions, YouTube URL, difficulty, equipment, space, player count,
      duration range, categories, visibility
- [ ] Create Practice playlists from own drills, public drills, or inline-created drills
- [ ] Reorder items; per-playlist trainer notes on an item
- [ ] Public/private visibility toggle per playlist and per drill
- [ ] **Coach-only playlists** (visible to the tenant's coaches, never to players)
- [ ] Edit and soft-delete playlists and drills

### Discovery
- [ ] Public drill search across all tenants: text, category, difficulty, equipment, duration
- [ ] Public playlist discovery
- [ ] Creator attribution stored and returned on trainer-facing responses

### Assignment & consumption
- [ ] Assign a playlist to explicit players, to a label (Epic-03), or to a filter result
- [ ] Optional due date and assignment note
- [ ] Player content library scoped to the current trainer context, showing locked and unlocked items
- [ ] Trainer "suggestions" — highlighted recommendations
- [ ] Paywall: locked content is listed but not readable until an entitlement exists (Epic-05)
- [ ] Auto-complete progress on first open of an item
- [ ] Progress read for player, parent (per dependent), and trainer

### Super Admin
- [ ] Cross-tenant content statistics and engagement metrics

---

## 4. Out of scope (Post-MVP)

PERFECT and PLAY pillars · direct video upload · trainer response videos · AI feedback · annotation
tools · player-facing attribution · creator usage analytics · content licensing / revenue share ·
workout templates · scheduled release · recommendations · before/after comparison · leaderboards ·
badges · social features · versioning · content export · multi-language · moderation queue.

---

## 5. Dependencies

**Required before**: Epic-01 (users, tenancy, labels via Epic-03 for label-based assignment),
Epic-02 (optional event linkage).

**Blocks**: nothing — Epic-04 is parallel to the CRM/event core.

**Consumes**: `EntitlementService` (Epic-05) · `labels` / segmentation (Epic-03) ·
`FeatureFlagService` (Epic-07 — the `lppp` toggle gates every route in this epic).

**External**: YouTube URL validation is local (regex + `oEmbed` HEAD check); the optional YouTube
Data API call for duration/thumbnail is behind a circuit breaker and never blocks a write.

---

## 6. Roles & permission matrix

| Capability | super_admin | trainer | coach | player |
|:---|:---:|:---:|:---:|:---:|
| Create playlist / drill | ✅ | ✅ | ❌ | ❌ |
| Edit / delete own content | ✅ any | ✅ own | ❌ | ❌ |
| Toggle visibility | ✅ | ✅ own | ❌ | ❌ |
| Search public library | ✅ | ✅ | ❌ | ❌ |
| Reference another tenant's public item | ✅ | ✅ | ❌ | ❌ |
| Assign playlist | ✅ | ✅ own tenant | ❌ | ❌ |
| Read coach-only playlist | ✅ | ✅ own | ✅ own tenant | ❌ |
| Read assigned content | ✅ | ✅ | ✅ | ✅ if entitled |
| Record progress | ❌ | ❌ | ❌ | ✅ own / dependents |
| Read player progress | ✅ | ✅ own tenant | ✅ own event players | ✅ own |
| Cross-tenant analytics | ✅ | ❌ | ❌ | ❌ |

---

## 7. Domain model

`app/models/content.py`.

### `content_items`
| Column | Type | Notes |
|:---|:---|:---|
| `id` | UUID | PK |
| `trainer_id` | UUID FK | **creator/owner tenant**, not the consuming tenant |
| `pillar` | enum | `learn`/`practice` |
| `item_type` | enum | `video`/`drill` |
| `title` | varchar(100) | not null |
| `instructions` | text | ≤ 1000 chars, sanitised on write (`bleach`) — the only rich-text field in the system |
| `youtube_url` | text | not null, `CHECK` regex |
| `youtube_video_id` | varchar(16) | extracted, indexed |
| `duration_seconds` | int | nullable |
| `tags` | text[] | GIN indexed |
| `visibility` | enum | `private`/`public` |
| `made_public_at` | timestamptz | nullable |
| `deleted_at` | timestamptz | nullable — soft delete only |

### `drill_details` — 1:1 extension where `item_type='drill'`
`content_item_id` PK/FK · `difficulty` enum · `equipment` text[] · `space` enum
(`small`/`medium`/`large`) · `player_count` varchar(16) · `duration_min` / `duration_max` int ·
`categories` text[] (GIN indexed).

`CHECK (duration_min <= duration_max)`. At least one category required — enforced by the Pydantic
schema and a `CHECK (cardinality(categories) > 0)`.

### `playlists`
`id` · `trainer_id` FK · `pillar` enum · `title` varchar(100) · `description` text ·
`target_skill_levels` enum[] · `target_positions` text[] · `target_age_levels` enum[] ·
`visibility` enum (`private`/`public`/`coach_only`) · `made_public_at` ·
`price_cents` int nullable · `token_price` int nullable · `is_free` bool generated ·
`deleted_at` nullable.

`CHECK (visibility <> 'coach_only' OR (price_cents IS NULL AND token_price IS NULL))` — coach-only
content is never sold.

### `playlist_items` — ordered many-to-many
`id` · `playlist_id` FK · `content_item_id` FK · `position` numeric(20,10) · `trainer_note` text ·
`estimated_minutes` int nullable · `is_required` bool default true · `added_at`.

`UNIQUE (playlist_id, position)`. **`position` is a fractional rank, not an array index**: inserting
between two items takes the midpoint, so a reorder writes one row instead of renumbering the
playlist. A rebalance job normalises positions when the gap shrinks below a threshold.

The same `content_item_id` may appear twice in one playlist (deliberate repetition in a workout), so
there is no unique constraint on the pair.

### `content_assignments`
`id` · `playlist_id` FK · `player_profile_id` FK · `trainer_id` FK · `assigned_by_user_id` ·
`assigned_at` · `due_date` date nullable · `note` text nullable ·
`source` enum (`explicit`/`label`/`filter`) · `source_ref` UUID nullable (the label id).

`UNIQUE (playlist_id, player_profile_id, trainer_id)` — reassignment refreshes the row rather than
duplicating it, and **never** resets progress.

### `content_progress`
`id` · `content_item_id` FK · `player_profile_id` FK · `trainer_id` FK · `playlist_id` FK nullable ·
`status` enum (`not_started`/`in_progress`/`completed`) · `first_opened_at` · `completed_at` ·
`watch_seconds` int default 0.

`UNIQUE (content_item_id, player_profile_id, trainer_id)`. Progress survives item removal, playlist
deletion, and reassignment — it is history, not state.

### `content_suggestions`
`id` · `playlist_id` FK · `player_profile_id` FK · `trainer_id` FK · `suggested_by_user_id` ·
`suggested_at` · `dismissed_at` nullable. `UNIQUE (playlist_id, player_profile_id)`.

### `content_references` — attribution for cross-tenant reuse
`id` · `content_item_id` FK (the original) · `referencing_trainer_id` FK · `playlist_id` FK ·
`referenced_at`. Feeds creator-reach analytics and the "N trainers use this" count.

---

## 8. API surface

| Method | Path | Role |
|:---|:---|:---|
| GET/POST | `/api/v1/trainer/playlists` | trainer |
| GET/PATCH/DELETE | `/api/v1/trainer/playlists/{id}` | trainer |
| POST | `/api/v1/trainer/playlists/{id}/items` | trainer |
| PATCH | `/api/v1/trainer/playlists/{id}/items/{item_id}` | trainer (note, position) |
| DELETE | `/api/v1/trainer/playlists/{id}/items/{item_id}` | trainer |
| PATCH | `/api/v1/trainer/playlists/{id}/visibility` | trainer |
| POST | `/api/v1/trainer/playlists/{id}/assign` | trainer |
| GET | `/api/v1/trainer/playlists/{id}/progress` | trainer |
| POST | `/api/v1/trainer/playlists/{id}/suggest` | trainer |
| GET/POST | `/api/v1/trainer/drills` | trainer |
| GET/PATCH/DELETE | `/api/v1/trainer/drills/{id}` | trainer |
| GET | `/api/v1/trainer/library/drills` | trainer (public, cross-tenant) |
| GET | `/api/v1/trainer/library/playlists` | trainer (public, cross-tenant) |
| GET | `/api/v1/coach/playlists` | coach (coach-only + tenant public) |
| GET | `/api/v1/content/playlists` | player (`?trainer_id=` required) |
| GET | `/api/v1/content/playlists/{id}` | player (gated) |
| GET | `/api/v1/content/items/{id}` | player (gated) |
| POST | `/api/v1/content/items/{id}/open` | player (records progress) |
| GET | `/api/v1/content/progress` | player |
| GET | `/api/v1/admin/content/stats` | super_admin |

---

## 9. User stories → backend tasks

### US-04.01 — Trainer creates a Learn playlist

`POST /api/v1/trainer/playlists` then `POST .../items`

**Acceptance criteria**
- [ ] `title` required, ≤ 100 ⇒ else `422`
- [ ] `pillar` required; `learn` and `practice` accept identical payloads — a parametrised test runs
      the whole suite against both values and asserts identical behaviour
- [ ] A playlist may be created empty; **assignment** of an empty playlist ⇒ `409 PLAYLIST_EMPTY`
- [ ] Each item requires a valid YouTube URL: watch, `youtu.be`, shorts, and embed forms all parse to
      the same `youtube_video_id`; anything else ⇒ `422 INVALID_YOUTUBE_URL`
- [ ] `instructions` ≤ 1000 chars and sanitised on write; a `<script>` payload is stored stripped —
      asserted by test
- [ ] Duration is enriched asynchronously by `enrich_youtube_metadata`; the API never blocks on
      YouTube and a failed enrichment leaves `duration_seconds = NULL` without failing the write
- [ ] The same video may be added twice to one playlist (`201` both times)
- [ ] `visibility` defaults to `private`
- [ ] `201` returns the playlist with its items in `position` order

### US-04.02 — Trainer creates a drill

`POST /api/v1/trainer/drills`

**Acceptance criteria**
- [ ] Required: `title`, `youtube_url`, `difficulty`, at least one category ⇒ else `422`
- [ ] `equipment` and `categories` validate against controlled vocabularies exposed by
      `GET /api/v1/trainer/drills/vocabularies` — free-form values ⇒ `422`, because the public search
      filters depend on a closed set
- [ ] `duration_min <= duration_max` ⇒ else `422`
- [ ] Creates `content_items(item_type='drill', pillar='practice')` + `drill_details` in one transaction
- [ ] `visibility` defaults to `private`
- [ ] `201` returns the merged item + detail representation

### US-04.03 — Trainer discovers and reuses a public drill

`GET /api/v1/trainer/library/drills?q=&category=&difficulty=&equipment=&duration_max=`

**Acceptance criteria**
- [ ] Returns `visibility='public'`, `deleted_at IS NULL` drills **from every tenant**, including the
      caller's own, each carrying `creator.business_name`
- [ ] `q` runs against title, instructions, and tags via a trigram/GIN index
- [ ] Array filters (`category`, `equipment`) use `&&` overlap against the GIN indexes; the emitted
      plan is asserted not to be a sequential scan
- [ ] Filters combine with AND, values within a filter with OR
- [ ] A **private** drill from another tenant never appears, and `GET /trainer/drills/{id}` on it ⇒ `404`
- [ ] Adding a public drill to a playlist stores a **reference** — a `playlist_items` row pointing at
      the original `content_item_id`. No copy is made; a test asserts `content_items` count is unchanged
- [ ] The reuse also writes a `content_references` row for attribution
- [ ] Cursor pagination; `limit` max 100

### US-04.04 — Trainer creates a Practice playlist (workout)

`POST /api/v1/trainer/playlists` (`pillar="practice"`) then `POST .../items`

**Acceptance criteria**
- [ ] Items may be the trainer's own drills, public drills from any tenant, or a drill created inline
      in the same request (`{"drill": { }}` instead of `{"content_item_id": "..."}`)
- [ ] Inline creation and item insertion happen in one transaction
- [ ] Adding another tenant's **private** drill ⇒ `404`
- [ ] `trainer_note` ≤ 500 chars, `estimated_minutes` optional and independent of the drill's own range
- [ ] Response returns items in `position` order with the drill detail joined

### US-04.04b — Trainer reorders items

`PATCH /api/v1/trainer/playlists/{id}/items/{item_id}` with `{"after_item_id": "..."}` or `{"position": "first"}`

**Acceptance criteria**
- [ ] Reordering writes **exactly one row** — asserted by a query-count test; the fractional rank
      makes a full renumber unnecessary
- [ ] `after_item_id` must belong to the same playlist ⇒ else `422`
- [ ] `null` `after_item_id` means "move to first"
- [ ] Concurrent reorders never produce a duplicate `position` (unique constraint + retry with a fresh midpoint)
- [ ] `rebalance_playlist_positions` renormalises when the smallest gap drops below `1e-6`
- [ ] Reordering does not touch `content_progress`

### US-04.05 — Trainer assigns a playlist

`POST /api/v1/trainer/playlists/{id}/assign`

```json
{ "player_profile_ids": ["..."], "label_id": null, "filter": null,
  "due_date": "2026-04-01", "note": "Before Saturday" }
```

**Acceptance criteria**
- [ ] Exactly one of `player_profile_ids`, `label_id`, `filter` ⇒ else `422 AMBIGUOUS_TARGET`
- [ ] `label_id` resolves through Epic-03 to the current member set; the assignment is a **snapshot**,
      not a live rule — later label changes do not assign or unassign anyone. Documented and tested
- [ ] Targets not linked to this tenant are skipped and reported in `skipped[]`; the call still returns `201`
- [ ] Reassigning to an existing target updates `due_date`/`note` and **preserves progress**
      (asserted by a test that completes an item, reassigns, and re-reads progress)
- [ ] `due_date` in the past ⇒ `422`
- [ ] An empty playlist ⇒ `409 PLAYLIST_EMPTY`
- [ ] Bulk assignment of 500 players uses one `INSERT ... ON CONFLICT DO UPDATE`, not a loop
- [ ] Enqueues one `notify_content_assigned` per player
- [ ] `201` returns `{assigned_count, skipped[]}`

### US-04.06 — Trainer toggles visibility

`PATCH /api/v1/trainer/playlists/{id}/visibility` · `PATCH /api/v1/trainer/drills/{id}` (`visibility`)

**Acceptance criteria**
- [ ] `private` → `public` sets `made_public_at` and makes the item discoverable to every tenant
- [ ] `public` → `private` removes it from discovery **but existing references keep resolving** —
      a playlist in another tenant that references it continues to serve the item to entitled players.
      This is a merge-gate test: make public, reference from tenant B, make private, assert B's players
      still load it and B's trainer no longer finds it in search
- [ ] `coach_only` is settable only on playlists and only when no price is set ⇒ else `422`
- [ ] Toggling another tenant's content ⇒ `404`
- [ ] `GET /trainer/library/...` responses carry `reference_count` from `content_references`

### US-04.07 — Player reads the content library

`GET /api/v1/content/playlists?trainer_id=&pillar=`

**Acceptance criteria**
- [ ] `trainer_id` required; a trainer the caller is not linked to ⇒ `404`
- [ ] Returns the tenant's `public` and assigned `private` playlists; **never** `coach_only` ones
- [ ] Each item carries `is_locked`, `price_cents`, `token_price`, `is_suggested`, `progress`
      (`completed_items` / `total_items`), `due_date`
- [ ] `is_locked` comes from one batched `EntitlementService.check_many(player, playlist_ids)` call —
      not one lookup per playlist; asserted by a query-count test
- [ ] Free playlists are never locked
- [ ] `GET /content/playlists/{id}` on a locked playlist returns metadata **and the item list with
      titles only** — `youtube_url` and `instructions` are omitted. A locked item's
      `GET /content/items/{id}` ⇒ `402 CONTENT_LOCKED` with `details.price_cents` and `details.token_price`
- [ ] The response model for a locked item is a *separate Pydantic class* without the URL field, so a
      leak cannot happen by forgetting a conditional
- [ ] A parent passes `?player_profile_id=` to read a dependent's library; another owner's profile ⇒ `404`
- [ ] Content from a different trainer context never appears — one test per direction

### US-04.08 — Player progress

`POST /api/v1/content/items/{id}/open` · `GET /api/v1/content/progress`

**Acceptance criteria**
- [ ] `open` on an entitled item upserts `content_progress` to `completed` with `completed_at`
      (auto-complete on first open, per the confirmed simplification)
- [ ] Repeat opens are idempotent: `completed_at` is not overwritten, `watch_seconds` accumulates if supplied
- [ ] `open` on a locked item ⇒ `402 CONTENT_LOCKED`, and **no progress row is created**
- [ ] Progress is keyed by `(item, player, trainer)`, so the same public drill consumed under two
      trainers tracks separately
- [ ] `GET /content/progress?trainer_id=` returns overall stats, recent completions, and per-playlist
      progress bars in ≤ 3 queries
- [ ] Removing an item from a playlist leaves its progress row intact; deleting the playlist likewise
- [ ] `GET /trainer/playlists/{id}/progress` returns per-player completion for the trainer, computed
      in one grouped query

### US-04.09 — Trainer edits a playlist

`PATCH /api/v1/trainer/playlists/{id}`

**Acceptance criteria**
- [ ] Editable: title, description, targeting, visibility, pricing; `pillar` is **immutable** ⇒ `422 PILLAR_IMMUTABLE`
- [ ] Assigned players see edits immediately — there is no versioning and no re-publish step
- [ ] Adding items leaves existing progress untouched; the new item reads `not_started`
- [ ] Removing an item leaves its progress row; the playlist's completion denominator shrinks
      accordingly, and a test asserts a previously "5 of 5" player is not reported as "5 of 4"
- [ ] Setting a price on a previously free playlist does **not** revoke existing entitlements or
      progress; already-entitled players keep access (asserted by test)

### US-04.10 — Trainer deletes content

`DELETE /api/v1/trainer/playlists/{id}` · `DELETE /api/v1/trainer/drills/{id}`

**Acceptance criteria**
- [ ] **Soft delete only** — `deleted_at` set, no row removed. A hard `DELETE` statement against these
      tables does not exist in the codebase (grep-asserted by a test)
- [ ] A deleted playlist disappears from player libraries and trainer listings; its assignments are
      marked inactive; progress rows are retained
- [ ] Deleting a drill referenced by other tenants' playlists requires `?confirm=true` ⇒ else
      `409 DRILL_IN_USE` with `details.reference_count`
- [ ] After confirmed deletion, referencing playlists render the slot as
      `{"unavailable": true, "title": "<original title>"}` rather than erroring or vanishing
- [ ] Deleting a drill still inside the owner's own playlists behaves identically
- [ ] Another tenant's content ⇒ `404`

### US-04.11 — Super Admin reads content analytics

`GET /api/v1/admin/content/stats?from=&to=&trainer_id=`

**Acceptance criteria**
- [ ] Content counts (playlists, drills, public/private split), top public drills by
      `reference_count`, top creators by public item count
- [ ] Engagement: total opens, completion rate, average items completed per active player
- [ ] Creation and engagement trends per week
- [ ] All numbers come from `content_daily_stats` rollups, not live scans
- [ ] `?trainer_id=` drills into one tenant
- [ ] CSV export streams
- [ ] A `trainer` calling it ⇒ `403`

---

## 10. Business rules (service layer)

**One implementation, two pillars** — `pillar` is a column, not a type hierarchy. Any behaviour that
differs by pillar must be justified in review; today nothing does. The test suite is parametrised
over both values.

**Video hosting** — the service stores URLs and metadata only. It never proxies, downloads, or
re-hosts media. The optional YouTube Data API enrichment sits behind a circuit breaker; when it is
open, writes still succeed with null duration.

**Visibility**
- `private` — creator tenant only.
- `public` — discoverable and referenceable by every tenant; the creator keeps ownership and may revert.
- `coach_only` — playlists only; visible to the creator's coaches, never to players, never priced.

**Reference, never copy** — reuse stores a foreign key. Consequences, all deliberate: the referencing
tenant sees later edits by the creator; going private does not break references; deletion degrades to
an "unavailable" slot. These three are individually tested.

**Paywall** — access = `is_free OR EntitlementService.has_access(player, playlist)`. Entitlements are
granted by Epic-05 and are permanent for one-time purchases. Locked content is *listed* with metadata
and *not readable*; the split is enforced by two distinct response models, not by a conditional field.

**Assignment** — a snapshot, not a live rule. Label-based assignment resolves membership once, at
assignment time.

**Progress** — auto-completed on first open; keyed by `(item, player, trainer)`; never reset by
reassignment, edit, or deletion. It is an append-mostly history table.

**Ordering** — fractional ranks with a rebalance job. No endpoint accepts an integer index.

**Feature flag** — every route in this epic is behind Epic-07's `lppp` toggle. When it is off for a
tenant, trainer routes return `403 FEATURE_DISABLED` and player library queries return an empty list
for that context. Existing data is retained untouched and reappears when the flag is re-enabled.

---

## 11. Backend flows

**Player opens a locked playlist**
```
GET /content/playlists/{id}?trainer_id=T
  ├─ assert player linked to T, playlist visible in T, not coach_only
  ├─ EntitlementService.has_access(player_profile, playlist) → False
  └─ serialize with LockedPlaylistResponse (item titles only, no URLs)
        + price_cents / token_price for the purchase call (Epic-05)
```

**Trainer references a public drill**
```
POST /trainer/playlists/{id}/items {content_item_id: D}
  ├─ load D: must be visibility='public' OR trainer_id = caller ⇒ else 404
  ├─ position = midpoint(after, next)
  ├─ INSERT playlist_items (reference)
  ├─ INSERT content_references (D, caller_trainer, playlist)  ← if cross-tenant
  └─ COMMIT
```

**Bulk assignment by label**
```
POST /trainer/playlists/{id}/assign {label_id: L}
  ├─ resolve members of L now  (snapshot)
  ├─ filter to active trainer_player_links
  ├─ INSERT ... ON CONFLICT (playlist, player, trainer) DO UPDATE SET due_date, note
  ├─ COMMIT
  └─ enqueue notify_content_assigned per player
```

---

## 12. Background jobs

| Job | Trigger | Effect | Idempotency key |
|:---|:---|:---|:---|
| `enrich_youtube_metadata` | item created / URL changed | fetch duration + thumbnail, update row | `content_item_id + youtube_video_id` |
| `notify_content_assigned` | assignment created | email + in-app | `assignment_id` |
| `notify_content_suggested` | suggestion created | email + in-app | `suggestion_id` |
| `notify_due_date_approaching` | cron, daily | remind on assignments due in 24 h (Q-04.06) | `assignment_id + due_date` |
| `rebalance_playlist_positions` | on tight-gap detection | renormalise `position` values | `playlist_id + revision` |
| `rollup_content_daily_stats` | cron, nightly | fill `content_daily_stats` | `(trainer_id, date)` |
| `recount_content_references` | cron, nightly | correct `reference_count` drift | idempotent |

---

## 13. Performance & scale targets

| Operation | Target | Verification |
|:---|:---|:---|
| `GET /content/playlists` 50 playlists | p95 < 400 ms, ≤ 3 queries incl. one batched entitlement check | query-count + k6 |
| `GET /content/playlists/{id}` 20 items | p95 < 300 ms | k6 |
| `GET /trainer/library/drills` over 10 000 public drills | p95 < 500 ms, GIN index used | `EXPLAIN ANALYZE` |
| `POST /trainer/playlists/{id}/assign` 500 players | < 2 s, one INSERT statement | integration timing test |
| Item reorder | exactly 1 UPDATE | query-count test |
| `POST /content/items/{id}/open` | p95 < 150 ms | k6 |
| `GET /trainer/playlists/{id}/progress` 200 players | p95 < 400 ms, 1 grouped query | query-count test |

---

## 14. Open questions

| ID | Question | Priority | Blocks |
|:---|:---|:---:|:---|
| Q-04.03 | Moderation for inappropriate public content — flag/report flow? | P2 | a `content_reports` table + admin queue |
| Q-04.06 | Due-date reminders 24 h ahead? | P2 | scheduling `notify_due_date_approaching` |
| Q-04.09 | Scheduled assignment release on a future date? | P2 | an `available_from` column on assignments |
| Q-04.10 | Notify referencing tenants when public content is edited? | P2 | a fan-out notification job |
| Q-05.02 | Content pricing model — per playlist / subscription / bundle? | **P1** | `playlists.price_cents` may become a separate price table |

Assumptions where unresolved: per-playlist one-time pricing (the simplest model that satisfies
US-04.07); reminders implemented but unscheduled; no moderation queue in MVP.

---

## 15. Epic Definition of Done

Conventions §10 plus:

- [ ] All 11 user stories implemented per §8
- [ ] **Public→private reference-survival test is a merge gate** (make public → reference from tenant
      B → make private → B's entitled players still load it, B's search no longer finds it)
- [ ] Locked-content test asserts `youtube_url` and `instructions` appear in **no** response for an
      unentitled player, on list and detail alike
- [ ] Cross-tenant private content returns `404` on every trainer route
- [ ] `coach_only` playlists asserted absent from every player-facing response
- [ ] Progress preservation tested across reassignment, item removal, and playlist deletion
- [ ] Reorder query-count test holds at one UPDATE
- [ ] Pillar-parametrised suite passes identically for `learn` and `practice`
- [ ] `bleach` sanitisation test: a script payload in `instructions` is stored stripped
- [ ] Soft-delete-only asserted by a grep test over the repository layer
- [ ] Feature-flag test: `lppp` off ⇒ `403` on trainer routes, empty library for players, data intact on re-enable
- [ ] Load targets in §13 met against 10 000 public drills and 500 assigned players
