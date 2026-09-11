# Epic Areas Plan — Python Backend

Eight epics, **86 user stories**, delivered as one FastAPI service. This document is the delivery
view: what depends on what, which module owns what, what can run in parallel, and what to build first.

---

## 1. Dependency map

```
                          Epic-01 · Identity & Tenancy
                    (auth, RBAC, trainer_player_links, ShareLinks)
                                       │
      ┌────────────────┬───────────────┼───────────────┬────────────────┐
      │                │               │               │                │
  Epic-02          Epic-03         Epic-04         Epic-08          Epic-07
  Events           CRM             Content         Forms            Admin
  (capacity)       (read model)    (playlists)     (public)         (audit + flags)
      │                │               │               │                │
      └────────────────┴───────┬───────┴───────────────┘                │
                               │                                        │
                          Epic-05 · Payments                            │
                    (ledger, Stripe, refunds, entitlements)             │
                               │                                        │
                          Epic-06 · Marketing                           │
                        (referrals, coupons)                            │
                               │                                        │
                               └────────────────────────────────────────┘
                                    Epic-07 gates 04, 06, 08 by flag
```

Two services cut across everything and should exist from day one, even as stubs:

- **`AuditService`** (Epic-07) — Epics 01, 02, 03, 05, 06 all write to it.
- **`FeatureFlagService`** (Epic-07) — Epics 04, 06, 08 are gated by it.

Both are built in the Epic-01 slice, so audit calls and flag gates are in place before the epics that
depend on them.

---

## 2. Module map

One service, one database, module boundaries that mirror the epics:

```
app/
  core/          config · security · errors · logging · pagination · clock · idempotency
  db/            session · base · alembic/
  models/        identity · events · crm · content · payments · marketing · admin · forms
  repositories/  one per model module, tenant_id required on tenant-scoped reads
  services/
    auth_service.py  user_service.py  sharelink_service.py  availability_service.py   ← Epic-01
    approval_service.py                                                               ← Epic-01
    event_service.py  rsvp_service.py  attendance_service.py                          ← Epic-02
    crm_service.py  segmentation_service.py  metrics_service.py                       ← Epic-03
    content_service.py  playlist_service.py  progress_service.py                      ← Epic-04
    payment_service.py  token_service.py  entitlement_service.py  stripe_gateway.py   ← Epic-05
    referral_service.py  coupon_service.py                                            ← Epic-06
    audit_service.py  feature_flag_service.py  platform_metrics_service.py            ← Epic-07
    form_service.py  submission_service.py  form_schema_compiler.py                   ← Epic-08
  api/v1/
    auth/ me/ trainer/ coach/ content/ tokens/ transactions/ public/ admin/ webhooks/
  jobs/          one module per epic + a shared cron registry
tests/
  unit/ integration/ e2e/ load/
```

`stripe_gateway.py` is the only module that imports the Stripe SDK. Everything else talks to
`PaymentService`, which keeps Stripe test-mode setup out of most of the test suite.

---

## 3. Delivery order

**Phase 0 — Foundation (before any epic)**
`uv` project · FastAPI skeleton · SQLAlchemy + Alembic · error handler · pagination · `Clock` ·
idempotency middleware · structlog · pytest tiers · CI running ruff + mypy + pytest · Postgres and
Redis in compose.

No user stories land in this phase, but every epic below assumes the error contract, the pagination
envelope, and the injected clock already exist.

**Phase 1A — Epic-01 + the two cross-cutting services**
Identity, tenancy, RBAC, `AuditService`, `FeatureFlagService`. Every other epic is blocked on the
`deps.py` produced here.

**Phase 1B — Epic-05 (token half) then Epic-02**
`TokenService`, the ledger, and `EntitlementService` are pure-Postgres and need no Stripe, so they
land before events. Epic-02's paid RSVP path is then real from the start rather than stubbed and
later rewired, and the RSVP↔payment ordering rule (Epic-02 §10) is implemented once.

Stripe (Connect, Checkout, webhooks) lands with Epic-02's USD path.

**Phase 1C — Epic-03**
Pure read model over Epic-01 + Epic-02 data. Naturally parallelisable with late Epic-02 work.

**Phase 1D — Epic-04**, gated by the `lppp` flag.

**Phase 1E — Epic-06**, needs Epic-05's ledger and the first-purchase signal.

**Phase 1F — Epic-07 admin surface.** The services shipped in 1A; this is the endpoint layer plus
dashboards and the audit query.

**Phase 1G — Epic-08.** Last, because it is the public surface and benefits from a hardened
rate-limiter, a settled error contract, and a working Stripe integration.

| Phase | Epic | Stories | Blocked by |
|:---|:---|:---:|:---|
| 0 | — | — | — |
| 1A | Epic-01 + audit/flags | 14 | Phase 0 |
| 1B | Epic-05 tokens → Epic-02 → Epic-05 Stripe | 10 + 15 | 1A |
| 1C | Epic-03 | 13 | 1A, 1B |
| 1D | Epic-04 | 11 | 1A, 1B |
| 1E | Epic-06 | 8 | 1B |
| 1F | Epic-07 | 9 | 1A–1E |
| 1G | Epic-08 | 6 | 1A, 1B |

---

## 4. Parallelisation

Two mechanisms enable parallel work inside a single service:

**Protocol-first seams.** Every cross-epic dependency is a `typing.Protocol` (see Epic-05 §5 and
Epic-07 §5). A team building Epic-02 codes against `PaymentService`, tests against a fake
implementing that Protocol, and does not wait for Stripe. mypy verifies the fake still matches the
real implementation.

**Feature flags as delivery tooling.** `lppp`, `marketing`, and `camps` already exist for the
operator. Shipping an epic disabled-by-default and enabling it per tenant makes deployment continuous
without long-lived branches.

Two approaches are explicitly not used:

- *Mocked identity.* Epic-01 lands in Phase 1A. Mocking identity would mean mocking tenancy, and
  tenancy rules must be tested against the real schema.
- *A service per epic.* Every epic shares a transaction boundary with at least one other (RSVP +
  token debit; submission + payment; audit + the change it records). Splitting them would require
  distributed transactions.

**Safe concurrent tracks once 1A is done**

```
Track A: Epic-05 tokens → Epic-02 → Epic-05 Stripe        (the critical path)
Track B: Epic-04 content authoring                         (needs only identity)
Track C: Epic-03 read model                                (needs Epic-02 data; start on schema + rollups)
Track D: Epic-07 dashboards + audit query                  (services already exist)
```

---

## 5. Cross-epic integration points

| From → To | Interface | Contract lives in |
|:---|:---|:---|
| 01 → all | `current_principal`, `require_role`, tenant scope | Epic-01 §4 |
| 01 → 02 | `AvailabilityService.check_conflict` | Epic-01 US-01.10 |
| 01 → 02/05 | `ApprovalService` (child purchases) | Epic-01 US-01.05 |
| 02 → 03 | attendance rows feed engagement stats | Epic-03 §7 |
| 02 → 05 | `PaymentService.charge_for_rsvp` / `refund_rsvp` | Epic-05 §5 |
| 04 → 05 | `EntitlementService.has_access` / `check_many` | Epic-05 §5 |
| 05 → 06 | first-purchase signal → `evaluate_referral_conversion` | Epic-06 §11 |
| 06 → 02/04 | `CouponService.validate_and_price` | Epic-06 §5 |
| 06 → 05 | `TokenService.credit` for rewards | Epic-05 §5 |
| 07 → 04/06/08 | `FeatureFlagService.is_enabled` | Epic-07 §5 |
| all → 07 | `AuditService.record` | Epic-07 §5 |
| 08 → 01 | conversion token → registration | Epic-08 US-08.05 |
| 08 → 05 | Checkout + webhook for camp payment | Epic-08 §11 |

---

## 6. The merge gates

Nine tests decide whether this system is correct. They are named individually in their epics; collected
here because they are the ones to write first and never let go red:

| Gate | Epic | What it proves |
|:---|:---|:---|
| Cross-tenant `404` on every tenant-scoped route | 01 | Isolation is structural, not incidental |
| 20-way ShareLink redemption ⇒ 1 success | 01 | Single-use links hold under concurrency |
| **50-way RSVP on capacity 20 ⇒ exactly 20** | 02 | No oversell |
| Compensating transaction on payment failure | 02 | No seat held without payment |
| Coach scope: 50-player tenant, 3 visible | 03 | Coach access is derived, not filtered |
| Public→private reference survival | 04 | Cross-tenant reuse does not break |
| **10-way debit on a 5-token balance ⇒ exactly 5** | 05 | No negative balance |
| **`verify_token_balances` after a 10 000-op workload** | 05 | Ledger and projection agree |
| 20-way coupon redemption on a 5-use coupon ⇒ 5 | 06 | Usage limits hold |
| Feature-flag round trip preserves all data | 07 | Disable ≠ delete |
| 50-way anonymous form submission on capacity 20 | 08 | Public surface cannot oversell |
| Hostile field definitions rejected at write | 08 | Dynamic schema is not an injection vector |

---

## 7. Key decisions

| # | Decision | Rationale | Epic | Confirm with client |
|:---|:---|:---|:---:|:---:|
| 1 | Referral attribution resolves a server-side `ref` parameter at registration; the attribution window applies to the registration → first-purchase gap | A forgeable client-side signal grants real tokens | 06 | ✅ |
| 2 | Content progress auto-completes on the first `POST /content/items/{id}/open` | The server cannot observe media playback; the open call is the available signal | 04 | ✅ |
| 3 | Token balance is an append-only ledger with a `CHECK`-guarded projection and a nightly proof | A stored balance that can drift from its history loses value silently | 05 | ✅ |
| 4 | Event capacity is not overridable by any role, including `super_admin` | `rsvp_count <= capacity` is a database `CHECK` | 02 | ✅ |
| 5 | Coupon eligibility is a per-coupon `new_players_only` boolean, default false | Covers new-players-only, everyone, and per-coupon choice with one column | 06 | — |
| 6 | Camp refunds are manual via Stripe; deleting a form with paid submissions is refused | Without an automated refund flow, a cascading delete would strand paid participants | 08 | — |
| 7 | Form field limit is 20 per form | Bounds the dynamic validator | 08 | — |

Decisions 1–4 change externally visible behaviour and should be confirmed before build. The rest are
implementation choices.

---

## 8. Post-MVP

- **Advanced communication** — in-app messaging, SMS, push. Adds a notification service and a
  WebSocket or SSE surface, neither of which exists in MVP.
- **Mobile applications** — no backend change beyond token TTLs and push registration.
- **Advanced analytics** — a reporting read-replica or warehouse; the rollup tables in Epics 03 and 07
  are the seam it would attach to.
- **Marketplace / network effects** — cross-trainer content sales and the PLAY pillar. The
  `content_references` table in Epic-04 is already the attribution substrate this would need.

---

## 9. Next steps

```
/requirements-analyst Task/epics_python/Epic-01_User_Management_Authentication_SPEC.md
/architect            module boundaries + Phase 0 skeleton
/api-designer         Epic-01 schemas → specs/api-designer-spec.md
/writing-plans        → tasks/TASK-001/
/coder → /test-generator → /code-reviewer → /verify
```

Per AGENTS.md, `specs/MANIFEST.md` is updated as each epic's architecture and API decisions land.
