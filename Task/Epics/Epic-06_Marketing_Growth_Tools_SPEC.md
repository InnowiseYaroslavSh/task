# Epic-06 (Python Backend): Marketing & Growth Tools

> Stack, layering, error contract, pagination, tenancy and testing rules: [00_Backend_Conventions.md](00_Backend_Conventions.md).

**Priority**: P2 — growth
**Complexity**: Medium (attribution correctness, discount arithmetic, anti-abuse)
**User stories**: 8

---

## 1. Description

Two growth mechanisms: the **"Get the Assist"** referral programme (a player's link, an attributed
signup, a reward on the referee's first purchase) and **coupon codes** (trainer-scoped discounts
applied before Stripe ever sees the amount).

Scope boundary: this epic decides *who gets credit* and *what the price becomes*.
It never moves value itself — token rewards go through `TokenService.credit`, discounts are handed to
Epic-05 as a reduced `amount_cents`.

Two constraints shape the design:

- **Attribution is recorded at signup and cashed in at first purchase**, possibly weeks later. It
  must survive that gap using server-side state only; a client-supplied signal is not trusted.
- **Discount arithmetic is money arithmetic.** Percentage discounts on integer cents round; a fixed
  discount larger than the price clamps; neither may ever produce a negative charge.

## 2. Backend outcomes

- Server-side attribution: a `referral_code` carried through registration, never inferred from a cookie.
- An assist counter that increments exactly once per referee, ever, under concurrency.
- Coupon validation and discount computation as a pure, exhaustively tested function.
- Reward grants that are idempotent and traceable back to the triggering purchase.

---

## 3. In scope (MVP)

### Referrals
- [ ] An implicit referral code per (player, trainer) pair — no generation endpoint
- [ ] Attribution captured at registration from the code
- [ ] Platform-wide reward rule configured by Super Admin (`N` qualifying referrals ⇒ `M` tokens`)
- [ ] Reward granted on the referee's **first purchase** of any kind
- [ ] Optional welcome token for the referee
- [ ] Trainer referral dashboard: totals, conversion rate, top referrers, activity log
- [ ] Per-player assist statistics

### Coupons
- [ ] Trainer-scoped codes: percentage or fixed amount
- [ ] Applicability: events, content, or both
- [ ] Usage limits and expiry
- [ ] Redemption at checkout, one coupon per transaction
- [ ] Usage log and per-coupon analytics

---

## 4. Out of scope (Post-MVP)

Email list export · campaign grouping · trigger-based automated campaigns · per-trainer referral
rules · non-token rewards · social sharing integrations · referral leaderboards and contests ·
SMS campaigns · trainer-to-trainer affiliate programme.

---

## 5. Dependencies

**Required before**: Epic-01 (accounts, ShareLinks, registration), Epic-05 (tokens, transactions,
the first-purchase signal).

**Blocks**: nothing.

**Consumes**: `TokenService.credit` · `transactions` (first-purchase detection) ·
`trainer_player_links` · `FeatureFlagService` (the `marketing` toggle gates every route here).

**Provides**: `CouponService.validate_and_price(code, trainer_id, player, subject) -> PriceQuote`,
called by Epic-02 RSVP and Epic-04/05 content purchase before a charge is created.

---

## 6. Roles & permission matrix

| Capability | super_admin | trainer | coach | player |
|:---|:---:|:---:|:---:|:---:|
| Read own referral code / stats | ❌ | ❌ | ❌ | ✅ own |
| Read referral dashboard | ✅ all | ✅ own tenant | ❌ | ❌ |
| Create / edit / deactivate coupon | ✅ any | ✅ own tenant | ❌ | ❌ |
| Delete unused coupon | ✅ | ✅ own | ❌ | ❌ |
| Read coupon analytics | ✅ all | ✅ own | ❌ | ❌ |
| Redeem coupon | ❌ | ❌ | ❌ | ✅ |
| Configure referral rule | ✅ | ❌ | ❌ | ❌ |

---

## 7. Domain model

`app/models/marketing.py`.

### `referral_codes`
`id` · `player_profile_id` FK · `trainer_id` FK · `code` text `UNIQUE` · `created_at`.
`UNIQUE (player_profile_id, trainer_id)`.

Created **eagerly** when a `trainer_player_links` row appears, so the code exists before anyone asks
for it and `GET /me/referral-code` is a plain lookup rather than a lazy write on a read path.
`code` is `secrets.token_urlsafe(9)` — 12 chars, ~72 bits, non-guessable and non-enumerable.

### `referrals`
| Column | Type | Notes |
|:---|:---|:---|
| `id` | UUID | PK |
| `referrer_player_profile_id` | UUID FK | |
| `referee_player_profile_id` | UUID FK | |
| `trainer_id` | UUID FK | |
| `referral_code_id` | UUID FK | attribution source |
| `status` | enum | `pending`/`converted`/`rewarded`/`void` |
| `registered_at` | timestamptz | |
| `converted_at` | timestamptz | nullable |
| `first_transaction_id` | UUID FK | nullable |
| `first_purchase_cents` | int | nullable |

**`UNIQUE (referee_player_profile_id, trainer_id)`** — a person can be referred into a tenant once,
ever. This single constraint is what makes the whole reward system abuse-resistant: no re-registration
loop can produce a second credit.

`CHECK (referrer_player_profile_id <> referee_player_profile_id)` — self-referral is a schema error.

### `assist_counters`
`player_profile_id` · `trainer_id` · PK on the pair ·
`total_assists` int · `assists_toward_next` int · `tokens_earned` int · `last_assist_at` · `updated_at`.

`assists_toward_next` is the modulo remainder against the current rule threshold. Keeping it
separate from `total_assists` is what lets the Super Admin change the ratio without retroactively
re-granting or clawing back tokens (US-06.08).

### `referral_rules` — singleton, platform-wide
`id` · `referrals_required` int `CHECK (> 0)` · `tokens_awarded` int `CHECK (> 0)` ·
`reward_referee` bool · `referee_tokens` int · `attribution_window_days` int default 30 ·
`updated_by_user_id` · `updated_at`.

Exactly one row, guarded by `CHECK (id = '00000000-0000-0000-0000-000000000001')`.

### `coupons`
`id` · `trainer_id` FK · `code` text · `discount_type` enum (`percentage`/`fixed`) ·
`discount_value` int (percent 1–100, or **cents**) · `applies_to` enum (`events`/`content`/`both`) ·
`new_players_only` bool default false · `usage_limit` int nullable · `usage_count` int default 0 ·
`expires_at` timestamptz nullable · `status` enum (`active`/`inactive`/`expired`) ·
`created_by_user_id` · `created_at`.

`CREATE UNIQUE INDEX uq_coupon_code ON coupons (trainer_id, upper(code));`
`CHECK (discount_type <> 'percentage' OR discount_value BETWEEN 1 AND 100)`
`CHECK (discount_type <> 'fixed' OR discount_value > 0)`
`CHECK (usage_limit IS NULL OR usage_count <= usage_limit)`

### `coupon_redemptions`
`id` · `coupon_id` FK · `player_profile_id` FK · `trainer_id` FK ·
`subject_type` enum (`event`/`content`) · `subject_id` UUID · `transaction_id` FK nullable ·
`original_cents` int · `discount_cents` int · `final_cents` int · `redeemed_at`.

`CHECK (final_cents = original_cents - discount_cents AND final_cents >= 0)` — the arithmetic is
checked by the database, not only by the code that wrote it.

### `referral_rewards`
`id` · `player_profile_id` FK · `trainer_id` FK · `token_amount` int ·
`triggered_by_referral_id` FK · `token_ledger_entry_id` FK · `granted_at`.
`UNIQUE (triggered_by_referral_id, player_profile_id)` — one grant per referral per recipient.

---

## 8. API surface

| Method | Path | Role |
|:---|:---|:---|
| GET | `/api/v1/me/referral-code?trainer_id=` | player |
| GET | `/api/v1/me/referral-stats?trainer_id=` | player |
| GET | `/api/v1/trainer/referrals` | trainer |
| GET | `/api/v1/trainer/referrals/top-referrers` | trainer |
| GET/POST | `/api/v1/trainer/coupons` | trainer |
| GET/PATCH/DELETE | `/api/v1/trainer/coupons/{id}` | trainer |
| GET | `/api/v1/trainer/coupons/{id}/redemptions` | trainer |
| POST | `/api/v1/coupons/validate` | player |
| GET/PATCH | `/api/v1/admin/referral-rules` | super_admin |
| GET | `/api/v1/admin/referrals` | super_admin |

Redemption itself is not an endpoint: a `coupon_code` field on the RSVP and content-purchase requests
is passed to `CouponService.validate_and_price` inside the charging transaction.

---

## 9. User stories → backend tasks

### US-06.01 — Player has an automatic referral code

`GET /api/v1/me/referral-code?trainer_id=`

**Acceptance criteria**
- [ ] A `referral_codes` row is created in the same transaction as every new
      `trainer_player_links` row — the read path never writes
- [ ] Response: `{code, share_path, total_referrals, converted_referrals}` where `share_path` is
      `/join/{trainer_slug}?ref={code}`; the backend returns a **path**, not an absolute URL, so the
      public base URL stays a deployment concern
- [ ] `trainer_id` required; a trainer the caller is not linked to ⇒ `404`
- [ ] A player linked to three trainers has three distinct codes; a test asserts they differ
- [ ] Codes never expire and never rotate
- [ ] A parent may fetch a dependent's code with `?player_profile_id=`; another owner's ⇒ `404`

### US-06.02 — Player refers a friend

Attribution happens inside `POST /api/v1/auth/register?share_code=X&ref=Y` (Epic-01).

**Acceptance criteria**
- [ ] `ref` is resolved server-side against `referral_codes`; an unknown code is **ignored silently**
      (registration still succeeds) and logged — a bad referral link must never block a signup
- [ ] On success, a `referrals` row is created `pending` in the same transaction as the account
- [ ] The referral's `trainer_id` must equal the tenant the referee is joining ⇒ otherwise no credit
- [ ] Referring yourself ⇒ no row (the `CHECK` would reject it; the service returns success without credit)
- [ ] A referee who already has an account ⇒ no credit; the link still works for the tenant association
- [ ] A second referral of the same referee into the same tenant ⇒ no second row (unique constraint)
- [ ] **Attribution window**: the `attribution_window_days` limit (default 30) applies to the
      *registration → first-purchase* gap; a referral not converted within it is set `void`
- [ ] `POST /api/v1/public/referrals/{code}/visit` optionally records an anonymous visit
      (salted IP hash only, rate limited 20/h/IP) for dashboard "opens" — never used for attribution

### US-06.03 — System awards a referral reward on first purchase

Triggered by Epic-05 when a transaction reaches `succeeded`.

**Acceptance criteria**
- [ ] "First purchase" = the referee's earliest `succeeded` transaction with this trainer, of any kind
      (event, content, or tokens)
- [ ] Detection is a single query with `LIMIT 2` — if two succeeded transactions already exist, this
      one is not the first and the job exits
- [ ] Transitions the referral `pending` → `converted`, storing `first_transaction_id` and `first_purchase_cents`
- [ ] Increments `assist_counters` **atomically**: `UPDATE ... SET total_assists = total_assists + 1,
      assists_toward_next = assists_toward_next + 1 ... RETURNING`
- [ ] When `assists_toward_next >= referrals_required`: grants `tokens_awarded` via
      `TokenService.credit` with key `f"referral:{referral_id}:reward"`, subtracts the threshold from
      `assists_toward_next` (does not zero it — a surplus carries forward), writes `referral_rewards`,
      transitions the referral to `rewarded`
- [ ] When `reward_referee` is on, the referee is credited `referee_tokens` with key
      `f"referral:{referral_id}:welcome"`
- [ ] **Idempotency**: the job run twice for the same transaction grants once — asserted by a test
      that invokes the handler 5 times and counts one ledger row
- [ ] A refunded first purchase does **not** claw back the reward in MVP, but voids the referral for
      future counting; this is stated explicitly in the response contract and tested
- [ ] Either party being inactive or erased ⇒ referral set `void`, no grant
- [ ] **Concurrency**: 10 simultaneous first-purchase events for 10 referees of one referrer with a
      3:1 rule ⇒ exactly 3 rewards and `assists_toward_next = 1`. Merge gate

### US-06.04 — Trainer reads the referral dashboard

`GET /api/v1/trainer/referrals?from=&to=&status=` · `GET .../top-referrers`

**Acceptance criteria**
- [ ] Summary: `total_referrals`, `converted`, `rewarded`, `conversion_rate`,
      `referral_revenue_cents` (sum of `first_purchase_cents` for converted referrals)
- [ ] Activity log: date, referrer name, referee name, status, first-purchase amount — cursor-paginated
- [ ] Top referrers: name, total, converted, conversion rate, last referral date, limit 10
- [ ] All of it scoped to the tenant; another trainer's referrals never appear
- [ ] Aggregates are computed in one grouped query each; a query-count test caps the endpoint at 3
- [ ] p95 < 1 s at 10 000 tenant referrals

### US-06.05 — Trainer creates a coupon

`POST /api/v1/trainer/coupons`

```json
{ "code": "SUMMER20", "discount_type": "percentage", "discount_value": 20,
  "applies_to": "both", "usage_limit": 50, "expires_at": "2026-08-31T23:59:59Z",
  "new_players_only": false }
```

**Acceptance criteria**
- [ ] `code` 4–20 chars, `^[A-Za-z0-9-]+$`, stored as given and matched case-insensitively ⇒ else `422`
- [ ] Duplicate code in the tenant (case-insensitive) ⇒ `409 COUPON_EXISTS`
- [ ] Percentage outside 1–100 ⇒ `422`; fixed value ≤ 0 ⇒ `422`
- [ ] `expires_at` in the past ⇒ `422`
- [ ] `usage_limit` below the current `usage_count` on a `PATCH` ⇒ `409 LIMIT_BELOW_USAGE`
- [ ] `DELETE` allowed only when `usage_count = 0` ⇒ else `409 COUPON_USED`; a used coupon is
      deactivated instead, because deleting it would orphan the redemption history
- [ ] Another tenant's coupon ⇒ `404` on read, patch, and delete
- [ ] `PATCH` may change expiry, limit, and status; **never** `code`, `discount_type`, or
      `discount_value` ⇒ `422 IMMUTABLE_FIELD`. A discount that changes after redemptions would make
      the analytics lie

### US-06.06 — Player redeems a coupon

`POST /api/v1/coupons/validate` (preview) and a `coupon_code` field on the purchase request (apply)

**Validate** — `{ "code": "SUMMER20", "trainer_id": "...", "subject_type": "event", "subject_id": "..." }`

**Acceptance criteria**
- [ ] `200` returns `{original_cents, discount_cents, final_cents, coupon_id}` — a **quote only**,
      nothing persisted, no counter moved
- [ ] Unknown / inactive / expired / exhausted / wrong-tenant ⇒ `404 COUPON_INVALID` — one code for
      all five, so the endpoint is not a probe for other tenants' coupon names
- [ ] `applies_to` mismatch (a content coupon on an event) ⇒ `404 COUPON_INVALID`
- [ ] `new_players_only` and the caller has any prior succeeded transaction with this trainer ⇒
      `409 COUPON_NEW_PLAYERS_ONLY`
- [ ] Rate limited 20/min per principal — a coupon validator is a brute-force target

**Apply**
- [ ] Validation and redemption happen **inside the charging transaction**, not before it; the quote
      from `/validate` is advisory and revalidated
- [ ] `usage_count` increments via `UPDATE ... WHERE usage_count < usage_limit RETURNING`; zero rows ⇒
      `409 COUPON_EXHAUSTED` and the whole purchase rolls back
- [ ] Reaching the limit flips `status` to `inactive` in the same statement
- [ ] Stripe receives only `final_cents`; the coupon is never sent to Stripe
- [ ] One coupon per transaction — a second `coupon_code` field does not exist in the schema
- [ ] A `coupon_redemptions` row is written with the full arithmetic
- [ ] **Concurrency**: 20 parallel redemptions of a 5-use coupon ⇒ exactly 5 succeed. Merge gate

**Discount arithmetic** — a pure function, exhaustively tested:
- [ ] Percentage: `discount = round_half_up(original * pct / 100)`; `$20.00 @ 20% ⇒ 400`;
      `$19.99 @ 33% ⇒ 660` (documented rounding)
- [ ] Fixed: `discount = min(value, original)` — never exceeds the price
- [ ] `final_cents = max(original - discount, 0)`; a `$5` item with `$10` off ⇒ `0`, a legitimate free purchase
- [ ] `final_cents = 0` skips Stripe entirely and records a succeeded zero-value transaction
- [ ] Coupons never apply to token purchases in MVP ⇒ `404 COUPON_INVALID` for that subject type

### US-06.07 — Trainer reads coupon analytics

`GET /api/v1/trainer/coupons` · `GET /api/v1/trainer/coupons/{id}/redemptions`

**Acceptance criteria**
- [ ] Each coupon carries `usage_count`, `usage_limit`, `total_discount_cents`,
      `total_revenue_cents` (sum of `final_cents`), `unique_players`, `status`
- [ ] Aggregates come from one grouped query over `coupon_redemptions`, not per-coupon lookups
- [ ] Redemption detail: player name, subject, original / discount / final, timestamp — cursor-paginated
- [ ] A summary endpoint returns tenant totals for the period
- [ ] Another tenant's coupon ⇒ `404`
- [ ] Money is reported in cents with an explicit `currency` field — no float appears anywhere in the response

### US-06.08 — Super Admin configures the referral rule

`GET|PATCH /api/v1/admin/referral-rules`

**Acceptance criteria**
- [ ] `referrals_required` 1–100, `tokens_awarded` 1–100, `referee_tokens` 0–100 ⇒ else `422`
- [ ] The change applies to **future** grants only
- [ ] Existing `assists_toward_next` values are **preserved verbatim**: a player with 2 assists under a
      3:1 rule keeps 2 under a new 5:1 rule and needs 3 more. Explicitly tested, because silently
      rebasing counters would take away progress players already earned
- [ ] A player whose `assists_toward_next` already meets a newly *lowered* threshold is granted on
      their next assist, not retroactively — no backfill job runs
- [ ] Every change writes an audit row with old and new values
- [ ] A `trainer` calling it ⇒ `403`
- [ ] `attribution_window_days` 1–365

---

## 10. Business rules (service layer)

**Attribution is server-side.** The referral code travels as a query parameter into registration and
is resolved against the database. No client-side attribution signal is accepted, since the server
cannot verify one.

- The code present at registration wins; there is no click history to compare against.
- Carrying the code from the shared link through to the registration call is the client's responsibility.
- `attribution_window_days` (default 30) bounds the **registration → first-purchase** gap. A referral
  not converted within it is set `void` by `expire_stale_referrals`.

**One referral per referee per tenant, forever** — the unique constraint. Everything else about
anti-abuse follows from it plus the first-purchase trigger: fake accounts cost real money to convert,
and each one pays out at most once.

**Rewards.** Threshold `N` from the singleton rule; a surplus carries forward rather than being
discarded. Grants are idempotent by referral id and always flow through `TokenService.credit`, so the
ledger remains the single record of every token in existence.

**Refunds.** A refunded first purchase voids the referral for future counting but does not claw back
a granted token in MVP. Clawback is deliberately out of scope: it would need a negative-balance
policy this system does not have.

**Coupons.** Tenant-scoped, case-insensitive, one per transaction, validated inside the charging
transaction, never transmitted to Stripe. `usage_count` moves only by atomic conditional UPDATE.
Discount fields are immutable after creation.

**Feature flag.** Every route here sits behind Epic-07's `marketing` toggle. Off ⇒ `403
FEATURE_DISABLED` on trainer routes; existing coupons stop validating; referral **tracking continues**
(the data keeps accruing) but rewards are not granted while the flag is off, and are not backfilled
when it returns.

---

## 11. Backend flows

**Registration with attribution**
```
POST /auth/register?share_code=S&ref=R
  ├─ resolve S (Epic-01), resolve R → referral_codes  (unknown R ⇒ log + ignore)
  ├─ assert code.trainer_id == joining tenant, referrer != referee
  ├─ INSERT users/profiles/links  (Epic-01)
  ├─ INSERT referrals (pending)          ← unique (referee, trainer)
  ├─ INSERT referral_codes for the new player  (eager, per tenant link)
  └─ COMMIT
```

**First purchase → reward**
```
Epic-05: transaction → succeeded
  └─ enqueue evaluate_referral_conversion(transaction_id)
        is this the referee's first succeeded txn with this trainer?   (LIMIT 2)
        no  → exit
        yes → BEGIN
                UPDATE referrals SET status='converted', first_transaction_id=..., converted_at=now()
                       WHERE id=:r AND status='pending'   ← 0 rows ⇒ already handled, exit
                UPDATE assist_counters
                   SET total_assists = total_assists + 1,
                       assists_toward_next = assists_toward_next + 1
                 RETURNING assists_toward_next
                if assists_toward_next >= rule.referrals_required:
                    TokenService.credit(referrer, +rule.tokens_awarded,
                                        key=f"referral:{r}:reward")
                    UPDATE assist_counters
                       SET assists_toward_next = assists_toward_next - rule.referrals_required,
                           tokens_earned = tokens_earned + rule.tokens_awarded
                    INSERT referral_rewards
                    UPDATE referrals SET status='rewarded'
                if rule.reward_referee:
                    TokenService.credit(referee, +rule.referee_tokens,
                                        key=f"referral:{r}:welcome")
              COMMIT → enqueue notify_referral_reward
```

**Coupon applied during a purchase**
```
POST /events/{id}/rsvp {coupon_code: "SUMMER20"}
  BEGIN
    quote = CouponService.validate_and_price(...)          ← revalidated here, not trusted
    UPDATE coupons SET usage_count = usage_count + 1,
           status = CASE WHEN usage_count + 1 >= usage_limit THEN 'inactive' ELSE status END
     WHERE id=:c AND status='active'
       AND (usage_limit IS NULL OR usage_count < usage_limit)
     RETURNING usage_count                                  ← 0 rows ⇒ 409, ROLLBACK
    INSERT coupon_redemptions (original, discount, final)
    charge final_cents via Epic-05                          ← 0 ⇒ skip Stripe entirely
  COMMIT
```

---

## 12. Background jobs

| Job | Trigger | Effect | Idempotency key |
|:---|:---|:---|:---|
| `evaluate_referral_conversion` | transaction succeeded | convert + reward per §11 | `referral_id + 'conversion'` |
| `notify_referral_reward` | reward granted | notify referrer (and referee) | `referral_reward_id` |
| `void_referral_on_refund` | first purchase refunded | referral → `void`, no clawback | `referral_id + 'void'` |
| `expire_stale_referrals` | cron, daily | `pending` older than the window ⇒ `void` | per-row guard |
| `expire_coupons` | cron, hourly | `active` past `expires_at` ⇒ `expired` | per-row guard |
| `backfill_referral_codes` | migration + cron, daily | create codes for links missing one | `(player, trainer)` |

---

## 13. Performance & scale targets

| Operation | Target | Verification |
|:---|:---|:---|
| `GET /me/referral-code` | p95 < 100 ms | single-row lookup |
| `POST /coupons/validate` | p95 < 200 ms | k6 |
| `GET /trainer/referrals` at 10 000 referrals | p95 < 1 s, ≤ 3 queries | query-count + k6 |
| `evaluate_referral_conversion` | < 500 ms, async, never blocks the purchase | integration timing |
| Concurrent coupon redemption | 20-way on a 5-use coupon ⇒ exactly 5 | e2e concurrency test (merge gate) |
| Concurrent reward grants | 10 conversions on a 3:1 rule ⇒ exactly 3 rewards | e2e concurrency test (merge gate) |
| Throughput | 1 000 referral visits/day, 100 concurrent redemptions, 500 rewards/day | load suite |

---

## 14. Open questions

| ID | Question | Priority | Status |
|:---|:---|:---:|:---|
| Q-06.10 | Coupon eligibility — new players only, everyone, or per-coupon? | P1 | ✅ Resolved — per-coupon `new_players_only`, default false |
| Q-06.11 | Reward ratio, and does the referee get a welcome token? | P1 | Open — configurable; ships as 3:1, `reward_referee=false` |
| Q-06.12 | 30-day attribution window | P2 | Open — applied to the registration→first-purchase gap (§10) |
| Q-06.13 | Coupon stacking | P2 | ✅ Resolved — one per transaction |

---

## 15. Epic Definition of Done

Conventions §10 plus:

- [ ] All 8 user stories implemented per §8
- [ ] **20-way concurrent coupon redemption ⇒ exactly 5 succeed on a 5-use coupon** — merge gate
- [ ] **10 concurrent conversions on a 3:1 rule ⇒ exactly 3 rewards, remainder 1** — merge gate
- [ ] Reward idempotency: the handler invoked 5 times for one transaction grants once
- [ ] Self-referral rejected at both service and schema level
- [ ] Duplicate referral of the same referee into the same tenant rejected by the unique constraint
- [ ] Discount arithmetic unit-tested exhaustively: percentage rounding, fixed clamping, zero-final,
      and the documented rounding examples
- [ ] `final_cents = 0` path asserted to skip Stripe and still record a transaction
- [ ] Rule change preserves existing `assists_toward_next` — asserted before and after
- [ ] Coupon `code`, `discount_type`, `discount_value` proven immutable after creation
- [ ] Cross-tenant `404` on coupons, referrals, and stats
- [ ] Coupon validation rate limit verified (`429`)
- [ ] Feature-flag test: `marketing` off ⇒ `403`, coupons stop validating, referrals still accrue, no backfill on re-enable
- [ ] Load targets in §13 met against 10 000 seeded referrals
