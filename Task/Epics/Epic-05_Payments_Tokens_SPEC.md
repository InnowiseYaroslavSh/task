# Epic-05 (Python Backend): Payments & Tokens

> Stack, layering, error contract, pagination, tenancy and testing rules: [00_Backend_Conventions.md](00_Backend_Conventions.md).

**Priority**: P0 — monetisation
**Complexity**: High (external state machine, money, idempotency, webhooks, refunds)
**User stories**: 10

---

## 1. Description

Money movement: Stripe Connect onboarding for trainers, Stripe Checkout for USD, a trainer-scoped
token currency, entitlements for paid content, the 24-hour refund rule, and webhook-driven
reconciliation.

This epic owns **every mutation of value**. No other epic debits a balance, issues a refund, or
grants an entitlement — they call this one. Two invariants govern everything below:

1. **The token ledger is append-only and the balance is derived.** The balance column is a cached
   projection, verified against the ledger nightly.
2. **Every externally visible money operation is idempotent by key.** Networks retry, clients
   resubmit, Stripe redelivers; none of it may charge twice.

## 2. Backend outcomes

- A double-entry token ledger whose sum is the balance, provable at any moment.
- Stripe webhook ingestion that is signature-verified, deduplicated, and replay-safe.
- Refund logic with one implementation shared by player cancellation, trainer cancellation, and admin action.
- A payment path that never leaves an RSVP paid-but-unseated or seated-but-unpaid for longer than the sweeper interval.

---

## 3. In scope (MVP)

### Payment methods
- [ ] Trainer-scoped tokens (pre-purchased credits)
- [ ] USD via Stripe Checkout
- [ ] Player subscriptions — implemented as a **subscription token** granting 30 days of unlimited
      access to qualifying events, activatable from a chosen date
- [ ] Dual pricing per event (USD and/or tokens; player chooses)
- [ ] Free items

### Token system
- [ ] Token purchase per (parent, trainer) pair
- [ ] Configurable packages with volume discounts
- [ ] Flexible per-event token cost
- [ ] Spend on event RSVP and content purchase
- [ ] Balance and ledger reads, scoped to a trainer context
- [ ] Token refunds under the 24-hour rule
- [ ] Trainer token gifting (manual credit, audited, trainers only)

### Stripe
- [ ] Connect Express onboarding + account status sync
- [ ] Checkout Sessions for one-time payments
- [ ] Billing for the trainer's platform subscription
- [ ] Customer + saved payment methods (Stripe-hosted; no card data on our side)
- [ ] Webhook ingestion, verification, dedup, async processing
- [ ] Application fee deduction (5% default, per-trainer override)

### Flows
- [ ] Event RSVP payment (tokens or USD)
- [ ] Content purchase → entitlement grant
- [ ] Token purchase
- [ ] Subscription purchase and activation
- [ ] Refunds under the 24-hour rule

### Parent / child
- [ ] Payment methods owned by the parent's Stripe Customer
- [ ] Token balance owned by the (parent, trainer) pair and usable by every dependent
- [ ] Child-initiated purchases route through Epic-01 approvals

### Transactions
- [ ] Unified transaction history per trainer context
- [ ] Refund tracking
- [ ] Stripe-hosted receipts

---

## 4. Out of scope (Post-MVP)

Multi-currency · payment plans / BNPL · platform-wide wallet · gift cards · trainer→coach payouts ·
in-platform invoicing · tax calculation · low-balance alerts · recurring token auto-purchase · token
expiry · fractional tokens · token transfer between trainers · partial refunds for price changes ·
subscription grace periods · automated refund-request approval.

---

## 5. Dependencies

**Required before**: Epic-01 (accounts, parent/child, approvals), Epic-02 (events to buy),
Epic-04 (content to buy).

**Blocks**: nothing hard — Epic-06 and Epic-07 consume it but can be built in parallel against its interfaces.

**External**: Stripe (Connect, Checkout, Billing, Webhooks). Stripe API rate limit 100 rps;
all calls are wrapped with timeouts, bounded retries, and a circuit breaker.

**Provides to other epics**
```python
class PaymentService(Protocol):
    async def charge_for_rsvp(self, rsvp_id: UUID, method: PaymentMethod,
                              idempotency_key: str) -> Transaction: ...
    async def refund_rsvp(self, rsvp_id: UUID, reason: RefundReason,
                          force_full: bool = False) -> Refund | None: ...

class TokenService(Protocol):
    async def balance(self, owner_user_id: UUID, trainer_id: UUID) -> int: ...
    async def debit(self, owner_user_id: UUID, trainer_id: UUID, amount: int,
                    reason: str, subject_id: UUID, idempotency_key: str) -> TokenLedgerEntry: ...
    async def credit(self, owner_user_id: UUID, trainer_id: UUID, amount: int,
                     reason: str, subject_id: UUID | None, idempotency_key: str) -> TokenLedgerEntry: ...

class EntitlementService(Protocol):
    async def has_access(self, player_profile_id: UUID, playlist_id: UUID) -> bool: ...
    async def check_many(self, player_profile_id: UUID,
                         playlist_ids: Sequence[UUID]) -> dict[UUID, bool]: ...
```

---

## 6. Roles & permission matrix

| Capability | super_admin | trainer | coach | player (parent) | child |
|:---|:---:|:---:|:---:|:---:|:---:|
| Connect Stripe account | ✅ (assist) | ✅ own | ❌ | ❌ | ❌ |
| Set token package prices | ✅ | ✅ own | ❌ | ❌ | ❌ |
| Gift tokens | ✅ | ✅ own tenant | ❌ | ❌ | ❌ |
| Purchase tokens | ❌ | ❌ | ❌ | ✅ | → approval |
| Spend tokens | ❌ | ❌ | ❌ | ✅ | → approval |
| Manage payment methods | ❌ | ❌ | ❌ | ✅ (Stripe portal) | ❌ |
| Read own transactions | ✅ all | ✅ own tenant summary | ❌ | ✅ per context | ✅ read-only |
| Issue manual refund | ✅ | via Stripe dashboard | ❌ | ❌ | ❌ |
| Set per-trainer fee | ✅ | ❌ | ❌ | ❌ | ❌ |

---

## 7. Domain model

`app/models/payments.py`. **Every monetary column is integer cents. There is no float in this epic.**

### `stripe_accounts` (trainer side)
`trainer_id` PK/FK · `stripe_account_id` text `UNIQUE` · `onboarding_status` enum
(`pending`/`incomplete`/`complete`) · `charges_enabled` bool · `payouts_enabled` bool ·
`platform_fee_bps` int default 500 (basis points — 500 = 5%) ·
`subscription_status` enum (`active`/`past_due`/`canceled`/`none`) ·
`stripe_subscription_id` · `connected_at` · `last_synced_at`.

Fee is stored in **basis points**, so 2.5% is representable without a decimal type.

### `stripe_customers` (payer side)
`user_id` PK/FK · `stripe_customer_id` text `UNIQUE` · `default_payment_method_id` nullable · `created_at`.
One Customer per **parent/adult account**, shared across all trainers.

### `token_packages`
`id` · `trainer_id` FK · `token_amount` int · `price_cents` int · `label` · `is_active` · `sort_order`.
`CHECK (token_amount > 0 AND price_cents > 0)`.

### `token_ledger` — append-only, the source of truth
| Column | Type | Notes |
|:---|:---|:---|
| `id` | UUID | PK |
| `owner_user_id` | UUID FK | the parent/adult account |
| `trainer_id` | UUID FK | tokens are trainer-scoped |
| `delta` | int | signed: `+` credit, `−` debit, never 0 |
| `entry_type` | enum | `purchase`/`spend`/`refund`/`gift`/`referral_reward`/`adjustment`/`subscription_grant` |
| `reason` | text | human-readable |
| `subject_type` | enum | `event`/`content`/`token_purchase`/`manual`/`referral` |
| `subject_id` | UUID | nullable |
| `transaction_id` | UUID FK | nullable, links to the USD side |
| `idempotency_key` | text | **`UNIQUE`** |
| `created_by_user_id` | UUID FK | who caused it |
| `created_at` | timestamptz | |

**No `UPDATE` and no `DELETE` on this table, ever.** A correction is a new compensating row. A
database trigger raises on `UPDATE`/`DELETE`, and a test asserts the trigger fires.

`ix_ledger_owner_trainer (owner_user_id, trainer_id, created_at DESC)`.

### `token_balances` — cached projection
`owner_user_id` · `trainer_id` · PK on the pair · `balance` int
`CHECK (balance >= 0)` · `last_entry_id` FK · `updated_at`.

Updated in the **same transaction** as every ledger insert. `verify_token_balances` (§12) proves
`balance = SUM(delta)` nightly and alerts on any mismatch. The `CHECK` makes an over-debit a database
error rather than a negative balance.

### `transactions` — the USD side
`id` · `owner_user_id` FK · `trainer_id` FK · `player_profile_id` FK nullable ·
`kind` enum (`event_payment`/`content_purchase`/`token_purchase`/`subscription`/`camp_registration`) ·
`subject_id` UUID · `amount_cents` int · `currency` char(3) default `'usd'` ·
`platform_fee_cents` int · `coupon_id` FK nullable (Epic-06) · `discount_cents` int default 0 ·
`status` enum (`pending`/`succeeded`/`failed`/`refunded`/`partially_refunded`) ·
`stripe_payment_intent_id` `UNIQUE` nullable · `stripe_checkout_session_id` `UNIQUE` nullable ·
`idempotency_key` text `UNIQUE` · `failure_code` · `created_at` · `settled_at`.

`CHECK (amount_cents >= 0 AND platform_fee_cents >= 0 AND discount_cents >= 0)`.

### `refunds`
`id` · `transaction_id` FK nullable · `token_ledger_entry_id` FK nullable ·
`amount_cents` int nullable · `token_amount` int nullable ·
`reason` enum (`player_canceled`/`trainer_canceled`/`admin`/`price_change`/`failed_fulfilment`) ·
`status` enum (`pending`/`succeeded`/`failed`) · `stripe_refund_id` `UNIQUE` nullable ·
`idempotency_key` `UNIQUE` · `initiated_by_user_id` · `created_at` · `settled_at`.

`CHECK (transaction_id IS NOT NULL OR token_ledger_entry_id IS NOT NULL)`.

### `entitlements`
`id` · `player_profile_id` FK · `trainer_id` FK · `subject_type` enum (`playlist`) ·
`subject_id` UUID · `source` enum (`purchase`/`gift`/`subscription`) ·
`transaction_id` FK nullable · `granted_at` · `expires_at` nullable (NULL = permanent) · `revoked_at` nullable.

`UNIQUE (player_profile_id, subject_type, subject_id) WHERE revoked_at IS NULL`.
Index `ix_entitlement_lookup (player_profile_id, subject_id) WHERE revoked_at IS NULL` — this is the
index `EntitlementService.check_many` rides.

### `player_subscriptions`
`id` · `owner_user_id` FK · `player_profile_id` FK · `trainer_id` FK · `transaction_id` FK ·
`starts_on` date · `ends_on` date · `status` enum (`scheduled`/`active`/`expired`/`canceled`) ·
`advance_bookings_used_on` date nullable.
`CHECK (ends_on = starts_on + 30)`.

### `webhook_events`
`id` · `provider` text · `external_id` text **`UNIQUE`** · `type` text · `payload` JSONB ·
`signature_verified` bool · `received_at` · `processed_at` nullable ·
`status` enum (`received`/`processed`/`failed`/`ignored`) · `attempts` int · `last_error` text.

---

## 8. API surface

| Method | Path | Role |
|:---|:---|:---|
| POST | `/api/v1/trainer/stripe/onboarding-link` | trainer |
| GET | `/api/v1/trainer/stripe/status` | trainer |
| GET | `/api/v1/trainer/stripe/dashboard-link` | trainer |
| GET/PUT | `/api/v1/trainer/token-packages` | trainer |
| POST | `/api/v1/trainer/players/{id}/gift-tokens` | trainer |
| GET | `/api/v1/trainer/earnings/summary` | trainer |
| GET | `/api/v1/tokens/balance?trainer_id=` | player |
| GET | `/api/v1/tokens/packages?trainer_id=` | player |
| POST | `/api/v1/tokens/purchase` | player (`Idempotency-Key`) |
| POST | `/api/v1/subscriptions/purchase` | player (`Idempotency-Key`) |
| POST | `/api/v1/content/playlists/{id}/purchase` | player (`Idempotency-Key`) |
| GET | `/api/v1/payment-methods/portal-link` | player |
| GET | `/api/v1/transactions?trainer_id=` | player |
| GET | `/api/v1/transactions/{id}` | player |
| POST | `/api/v1/webhooks/stripe` | public (signature-verified) |
| GET | `/api/v1/admin/trainers/{id}/pricing` | super_admin |
| PATCH | `/api/v1/admin/trainers/{id}/pricing` | super_admin |
| GET | `/api/v1/admin/transactions` | super_admin |
| POST | `/api/v1/admin/refunds` | super_admin |

Event RSVP payment is **not** a separate endpoint — it happens inside `POST /events/{id}/rsvp`
(Epic-02) through `PaymentService`.

---

## 9. User stories → backend tasks

### US-05.01 — Trainer connects a Stripe account

`POST /api/v1/trainer/stripe/onboarding-link` → `GET /api/v1/trainer/stripe/status`

**Acceptance criteria**
- [ ] Creates a Connect **Express** account if none exists, then returns a one-time
      `AccountLink` URL with `return_url` and `refresh_url`
- [ ] Already `complete` ⇒ `409 STRIPE_ALREADY_CONNECTED`
- [ ] `status` reflects `charges_enabled` / `payouts_enabled` / `requirements_due[]`, refreshed from
      the `account.updated` webhook and by an on-demand sync when `last_synced_at` is older than 5 min
- [ ] While `charges_enabled` is false: creating a priced event ⇒ `409 STRIPE_NOT_CONNECTED`;
      pricing content ⇒ same; free events remain creatable
- [ ] `platform_fee_bps` defaults to 500 and is included in every Checkout Session as `application_fee_amount`
- [ ] `dashboard-link` returns a fresh Express dashboard login link, never a stored URL
- [ ] Stripe API failure ⇒ `502 STRIPE_UNAVAILABLE`, logged with the Stripe request id, no partial local state

### US-05.02 — Player purchases tokens

`POST /api/v1/tokens/purchase` — `Idempotency-Key` required

```json
{ "trainer_id": "...", "package_id": "...", "success_url": "...", "cancel_url": "..." }
```

**Acceptance criteria**
- [ ] Returns `{checkout_url, transaction_id}` with a `pending` transaction; **no tokens are credited yet**
- [ ] Tokens are credited only by the `checkout.session.completed` webhook
- [ ] The Stripe idempotency key is derived from the caller's `Idempotency-Key`, so a retried request
      reuses the same Checkout Session instead of creating a second one
- [ ] `package_id` from another trainer ⇒ `404`
- [ ] Caller not linked to `trainer_id` ⇒ `404`
- [ ] Trainer without `charges_enabled` ⇒ `409 STRIPE_NOT_CONNECTED`
- [ ] A child caller ⇒ `202` with an approval id, no Checkout Session created (Epic-01 US-01.05)
- [ ] Balance is owned by `(parent account, trainer)` — every dependent of that parent spends from it.
      Tested with a 3-child family sharing one balance
- [ ] `GET /tokens/balance` reads `token_balances`, single-row lookup, p95 < 50 ms

### US-05.03 — Player pays for an event with tokens

Invoked by Epic-02's RSVP endpoint via `TokenService.debit`.

**Acceptance criteria**
- [ ] Debit is a single transaction: `SELECT ... FOR UPDATE` on `token_balances`, insert the ledger
      row, update the projection, insert the RSVP linkage
- [ ] Insufficient balance ⇒ `402 INSUFFICIENT_TOKENS` with `details.balance` and `details.required`;
      no ledger row is written and the reserved seat is released (Epic-02 §10)
- [ ] `token_price` may exceed 1 — a 2-token event debits exactly 2
- [ ] The ledger `idempotency_key` is `f"rsvp:{rsvp_id}:debit"`, so a retried RSVP debits once.
      Tested by calling `debit` twice with the same key and asserting one ledger row
- [ ] **Concurrency**: 10 parallel 1-token debits against a 5-token balance ⇒ exactly 5 succeed,
      5 × `402`, final balance 0, ledger sum equals balance. Merge gate
- [ ] The `CHECK (balance >= 0)` is proven to fire by a test that attempts to bypass the service layer

### US-05.04 — Player pays for an event with USD

**Acceptance criteria**
- [ ] Epic-02 reserves the seat, then this service creates a Checkout Session with
      `payment_intent_data.application_fee_amount = round(amount_cents * fee_bps / 10000)` and
      `transfer_data.destination = <trainer connect account>`
- [ ] The response carries `checkout_url`; the RSVP stays `registered` with a null
      `payment_transaction_id` until the webhook lands
- [ ] `sweep_unpaid_rsvps` (Epic-02) cancels the RSVP and releases the seat if no webhook arrives in 15 min
- [ ] `checkout.session.completed` sets the transaction `succeeded` and links it to the RSVP
- [ ] `payment_intent.payment_failed` cancels the RSVP, releases the seat, and enqueues a notification
- [ ] Fee arithmetic is integer-only and asserted for rounding: $20.00 at 5% ⇒ 100 cents, $19.99 at
      2.5% ⇒ 50 cents (round-half-up, documented and tested)
- [ ] Saved payment methods live on the Stripe Customer; **no card data ever reaches our database** —
      a test greps the schema for card-shaped columns and fails if any exists

### US-05.05 — Player cancels and is refunded

`PaymentService.refund_rsvp`, called by Epic-02.

**Acceptance criteria**
- [ ] ≥ 24 h before `starts_at` ⇒ full refund; < 24 h ⇒ none, `refund_status='forfeited'`
- [ ] Trainer-initiated cancellation passes `force_full=True` and refunds regardless of timing
- [ ] Token refund is a `+delta` ledger row with key `f"rsvp:{rsvp_id}:refund"`, applied synchronously
- [ ] USD refund creates a `refunds` row `pending`, calls `stripe.Refund.create` with the same
      idempotency key, and settles on `charge.refunded`
- [ ] The **application fee is refunded proportionally** (`refund_application_fee=True`), so the
      platform does not keep a fee on a reversed sale — explicitly tested
- [ ] Refunding twice ⇒ the second call returns the existing refund, issues nothing new
- [ ] Boundary tested at `-24h0m1s` and `-23h59m59s`
- [ ] A refund on an already-refunded transaction ⇒ `409 ALREADY_REFUNDED`

### US-05.06 — Player purchases content

`POST /api/v1/content/playlists/{id}/purchase` — `Idempotency-Key` required

**Acceptance criteria**
- [ ] Body carries `payment_method` (`usd` or `tokens`) and `player_profile_id`
- [ ] Tokens ⇒ synchronous debit + `entitlements` row in **one transaction**; a failure grants nothing
- [ ] USD ⇒ Checkout Session; the entitlement is granted only by the webhook
- [ ] Already entitled ⇒ `409 ALREADY_ENTITLED`, no charge
- [ ] A free playlist ⇒ `422 CONTENT_IS_FREE`
- [ ] `coach_only` playlist ⇒ `404` (it is not visible to players at all)
- [ ] Entitlements are permanent (`expires_at IS NULL`) for one-time purchases
- [ ] A parent may purchase for a dependent; another owner's profile ⇒ `404`
- [ ] An entitlement is per `player_profile`, not per account — a test asserts one child's purchase
      does not unlock the sibling's library

### US-05.06b — Player purchases a subscription

`POST /api/v1/subscriptions/purchase`

**Acceptance criteria**
- [ ] Body: `trainer_id`, `player_profile_id`, `starts_on` (today or later) ⇒ else `422`
- [ ] Payment is a normal Checkout Session; on webhook success a `player_subscriptions` row is created
      `scheduled` (or `active` when `starts_on` is today) with `ends_on = starts_on + 30 days`
- [ ] `activate_subscriptions` flips `scheduled` → `active` and `active` → `expired` daily
- [ ] An overlapping active subscription for the same (player, trainer) ⇒ `409 SUBSCRIPTION_OVERLAP`
- [ ] While active, Epic-02 accepts `payment_method="subscription"` at zero cost for qualifying events
- [ ] The **advance-booking limit** is enforced here: `advance_bookings_used_on` records the last date
      an advance booking was made; a second advance booking the same day ⇒ `409 SUBSCRIPTION_DAILY_LIMIT`.
      Same-day bookings do not touch the counter
- [ ] **No grace period** — a failed payment grants nothing (confirmed resolution of Q-05.07)

### US-05.07 — Parent manages payment methods

`GET /api/v1/payment-methods/portal-link`

**Acceptance criteria**
- [ ] Returns a fresh Stripe Billing Portal session URL with a `return_url`
- [ ] A Customer is created lazily on first need and stored in `stripe_customers`
- [ ] One Customer per account, shared across every trainer context
- [ ] There is **no** endpoint that accepts a card number, PAN, CVC, or token from the client — a
      schema test asserts no such field exists in any request model in the codebase
- [ ] `payment_method.attached` / `.detached` webhooks keep `default_payment_method_id` in sync
- [ ] A `coach` or `trainer` calling it ⇒ `403`

### US-05.08 — Player reads transaction history

`GET /api/v1/transactions?trainer_id=&from=&to=&kind=&method=`

**Acceptance criteria**
- [ ] `trainer_id` required; the response contains **only** that context's rows
- [ ] Merges `transactions` (USD) and `token_ledger` (tokens) into one chronologically sorted feed
      with a discriminated union response model
- [ ] Each row: timestamp, kind, description, signed amount (cents or tokens), method, status,
      `receipt_url` where Stripe provides one
- [ ] Cursor pagination over `(created_at, id)` across both sources — tested for stability at page
      boundaries when both sources have entries in the same millisecond
- [ ] Another trainer's transactions never appear — one test per direction
- [ ] A child reads the same feed read-only; a `403` is asserted on every write route

### US-05.09 — Trainer reads an earnings summary

`GET /api/v1/trainer/earnings/summary`

**Acceptance criteria**
- [ ] Returns `current_period_cents`, `last_payout_cents`, `next_payout_date`, `lifetime_cents`,
      and `stripe_dashboard_url`
- [ ] Figures come from local `transactions` where `status='succeeded'` net of `platform_fee_cents`,
      and are labelled `is_estimate: true` — **Stripe remains the financial source of truth**
- [ ] The platform does **not** reproduce payout, tax, or dispute reporting; the response says so
      and links out
- [ ] Another tenant's earnings ⇒ `404`

### US-05.10 — Super Admin configures per-trainer pricing

`PATCH /api/v1/admin/trainers/{id}/pricing`

```json
{ "platform_fee_bps": 300, "monthly_subscription_cents": 1000 }
```

**Acceptance criteria**
- [ ] `platform_fee_bps` within 0–3000 ⇒ else `422`
- [ ] The new fee applies to transactions created **after** the change; historical rows keep the fee
      they were charged, asserted by test
- [ ] A subscription price change updates the Stripe Billing subscription at the next cycle
- [ ] Every change writes an audit row with old value, new value, actor, and optional reason
- [ ] A `trainer` calling it ⇒ `403`

### US-05.11 — Trainer gifts tokens

`POST /api/v1/trainer/players/{id}/gift-tokens`

**Acceptance criteria**
- [ ] Body: `amount` (1–1000) and `note` (required) ⇒ else `422`
- [ ] Credits the **owner account's** balance for this trainer with `entry_type='gift'`
- [ ] The player must be linked to this tenant ⇒ else `404`
- [ ] A coach calling it ⇒ `403`
- [ ] Writes an audit row; the ledger `reason` carries the note
- [ ] Idempotency-Key required — a double submission gifts once

---

## 10. Business rules (service layer)

**The ledger is the source of truth.** `token_balances.balance` is a projection updated in the same
transaction as its ledger row. `verify_token_balances` proves `balance = SUM(delta)` for every pair
nightly. A mismatch raises an alert and does not self-heal — the drift is investigated, not silently
corrected.

**Idempotency everywhere.** Every value-moving call carries a key that is:
`f"rsvp:{rsvp_id}:debit"`, `f"rsvp:{rsvp_id}:refund"`, `f"content:{playlist_id}:{player_id}:purchase"`,
or the client's `Idempotency-Key` for entry-point calls. The key is `UNIQUE` in the database, so a
duplicate is a constraint violation translated to a replayed response — not a second charge.

**Token scoping.** Tokens belong to `(owner account, trainer)`. They never cross trainers, never
expire in MVP, and are never fractional. All dependents of an account share its balance.

**Payment ordering with Epic-02.** Seat reserved → charge → confirm, with compensation on failure and
a 15-minute sweeper. See Epic-02 §10 — that ordering is normative and this epic implements its charge step.

**Refund policy.** Player cancels ≥ 24 h ⇒ full; < 24 h ⇒ forfeit; trainer cancels ⇒ always full;
trainer removes a player ⇒ always full; admin ⇒ always allowed and audited. One implementation
(`refund_rsvp`) serves all five; the policy decision is a pure function unit-tested independently of Stripe.

**Fees.** Integer basis points, `round_half_up(amount_cents * bps / 10000)`, charged as Stripe
`application_fee_amount`, refunded proportionally on reversal. The historical fee is stored on the
transaction so a later rate change cannot rewrite the past.

**Webhooks.** Signature verified before parsing. Persist → `200` → process asynchronously. Duplicate
`external_id` acknowledged and skipped. Unknown types recorded as `ignored`, never 500 — an
unrecognised event must not make Stripe retry forever. Out-of-order delivery is handled by making each
handler check current state rather than assume a transition.

Handled types: `checkout.session.completed` · `checkout.session.expired` ·
`payment_intent.succeeded` · `payment_intent.payment_failed` · `charge.refunded` ·
`account.updated` · `customer.subscription.updated` · `customer.subscription.deleted` ·
`payment_method.attached` · `payment_method.detached` · `invoice.payment_failed`.

**Trainer platform subscription.** $15/month default via Stripe Billing. Three failed attempts over
10 days ⇒ `subscription_status='past_due'` ⇒ paid-event creation blocked while free events continue.
Payment ⇒ immediate reactivation.

**PCI.** No card data is accepted, stored, logged, or proxied. All card interaction is Stripe-hosted.

---

## 11. Backend flows

**Token purchase**
```
POST /tokens/purchase  (Idempotency-Key: K)
  ├─ idempotency lookup → replay?
  ├─ validate link, package, charges_enabled
  ├─ INSERT transactions (pending, kind=token_purchase, idempotency_key=K)
  ├─ stripe.checkout.Session.create(..., idempotency_key=f"txn:{transaction_id}")
  ├─ COMMIT → 201 {checkout_url, transaction_id}

webhook checkout.session.completed
  ├─ verify signature → INSERT webhook_events (external_id UNIQUE) → 200
  └─ async: BEGIN
        transaction → succeeded
        INSERT token_ledger (+N, idempotency_key=f"txn:{id}:credit")
        UPDATE token_balances
        COMMIT → enqueue send_receipt
```

**Refund on cancellation**
```
Epic-02 cancel → PaymentService.refund_rsvp(rsvp_id, reason)
  ├─ policy(now, starts_at, reason) → FULL | NONE      ← pure, unit-tested
  ├─ NONE → mark forfeited, return None
  ├─ tokens → INSERT token_ledger (+N, key=rsvp:{id}:refund); UPDATE balance; COMMIT
  └─ usd    → INSERT refunds (pending, key=rsvp:{id}:refund)
              stripe.Refund.create(payment_intent, refund_application_fee=True,
                                   idempotency_key=same)
              webhook charge.refunded → refunds.succeeded, transaction.refunded
```

**Content purchase with tokens (single transaction)**
```
POST /content/playlists/{id}/purchase {method: tokens}
  BEGIN
    SELECT token_balances FOR UPDATE
    assert balance >= token_price            → else 402, ROLLBACK
    INSERT token_ledger (-N, key=content:{pl}:{player}:purchase)
    UPDATE token_balances
    INSERT entitlements (source=purchase)
  COMMIT → 201
```

---

## 12. Background jobs

| Job | Trigger | Effect | Idempotency key |
|:---|:---|:---|:---|
| `process_stripe_webhook` | webhook persisted | apply the event | `webhook_events.external_id` |
| `sync_stripe_account` | `account.updated`, or staleness | refresh capabilities and requirements | `trainer_id + updated_at` |
| `send_receipt` | transaction succeeded | receipt email with the Stripe URL | `transaction_id` |
| `notify_payment_failed` | payment failed | notify payer | `transaction_id + 'failed'` |
| `activate_subscriptions` | cron, daily 00:05 UTC | `scheduled`→`active`, `active`→`expired` | per-row guard |
| `verify_token_balances` | cron, nightly | prove `balance = SUM(delta)`, alert on drift | idempotent |
| `reconcile_stripe_transactions` | cron, hourly | pull Stripe events from the last 24 h, apply any never received | `external_id` |
| `expire_pending_transactions` | cron, 15 min | `pending` older than 24 h ⇒ `failed`; release any held seat | per-row guard |
| `retry_failed_refunds` | cron, hourly | retry `refunds` in `failed`, max 5 attempts | `refund.idempotency_key` |

`reconcile_stripe_transactions` exists because webhook delivery is best-effort. A missed
`checkout.session.completed` would otherwise leave a paying customer without their tokens; the
reconciler closes that gap within the hour.

---

## 13. Performance & scale targets

| Operation | Target | Verification |
|:---|:---|:---|
| `GET /tokens/balance` | p95 < 50 ms | single-row PK lookup |
| `TokenService.debit` | p95 < 150 ms | integration timing |
| `POST /tokens/purchase` (to `checkout_url`) | p95 < 800 ms incl. Stripe | k6 with a stubbed Stripe |
| `POST /webhooks/stripe` (persist + ack) | p95 < 200 ms | k6 at 50 webhooks/min |
| `EntitlementService.check_many` 50 ids | p95 < 100 ms, 1 query | query-count test |
| `GET /transactions` 50 rows merged | p95 < 400 ms | k6 |
| Concurrent debits | 10-way on a 5-token balance ⇒ exactly 5 succeed | e2e concurrency test (merge gate) |
| Throughput | 100 concurrent payments, 1 000 transactions/day, 50 webhooks/min | load suite |

Reliability: 99.9% availability of the payment path (bounded by Stripe's SLA); webhook processing
retried 5 times over 24 h; zero tolerance for lost ledger writes.

---

## 14. Open questions

| ID | Question | Priority | Status |
|:---|:---|:---:|:---|
| Q-05.02 | Content pricing model — per playlist, subscription, or bundle? | P1 | Open — MVP assumes per-playlist one-time |
| Q-05.03 | Token package sizes and discount ladder | P2 | Open — trainer-configurable, no defaults shipped |
| Q-05.01 | 24-hour refund window | P0 | ✅ Resolved |
| Q-05.04 | Failed-payment retry policy | P2 | ✅ Resolved — Stripe handles retries |
| Q-05.05 | Partial refunds on price change | P2 | ✅ Resolved — out of MVP; trainers use token gifting |
| Q-05.06 | Low-balance alerts | P2 | ✅ Resolved — not in MVP |
| Q-05.07 | Subscription grace period | P2 | ✅ Resolved — none; access follows successful payment |
| Q-05.08 | Trainer→coach payouts | P2 | ✅ Resolved — out of platform; analytics only (Epic-03) |

---

## 15. Epic Definition of Done

Conventions §10 plus:

- [ ] All 10 user stories implemented per §8
- [ ] **`verify_token_balances` passes on a seeded dataset after a randomised 10 000-operation workload** — merge gate
- [ ] **10-way concurrent debit test: exactly 5 of 10 succeed on a 5-token balance, ledger sum = balance** — merge gate
- [ ] Append-only trigger on `token_ledger` proven to reject `UPDATE` and `DELETE`
- [ ] Idempotency test per money endpoint: the same key twice ⇒ one effect, identical response
- [ ] Webhook dedup test: the same `external_id` twice ⇒ processed once
- [ ] Webhook signature test: a tampered payload ⇒ `400`, nothing persisted
- [ ] Out-of-order webhook test: `charge.refunded` before `checkout.session.completed` converges correctly
- [ ] 24-hour refund boundary tested on both sides with an injected clock
- [ ] Application-fee refund proportionality asserted
- [ ] Fee rounding asserted on the documented cases
- [ ] Schema test: no card-shaped column exists; request-model test: no card field is accepted
- [ ] Entitlement isolation test: one child's purchase does not unlock a sibling's library
- [ ] Cross-tenant `404` on balance, packages, transactions, and earnings
- [ ] `reconcile_stripe_transactions` recovers a deliberately dropped webhook
- [ ] Stripe test-mode e2e: purchase → RSVP → cancel → refund, asserted end-to-end
- [ ] Load targets in §13 met
