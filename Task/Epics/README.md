# Epics — Python Backend

Product specification for the training-platform backend: eight epics, **86 user stories**, delivered
as one FastAPI service.

Scope is the **server side only**. The deliverable is a JSON API plus its background workers; no
templates, no frontend assets, no presentation concerns.

## Files

| File | Covers | Depends on |
|:---|:---|:---|
| [00_Backend_Conventions.md](00_Backend_Conventions.md) | Stack, layering, auth, error contract, pagination, testing, DoD — **read first** | — |
| [Epic-01_User_Management_Authentication_SPEC.md](Epic-01_User_Management_Authentication_SPEC.md) | Auth, RBAC, multi-tenancy, profiles, ShareLinks, impersonation, GDPR | — |
| [Epic-02_Event_Management_Scheduling_SPEC.md](Epic-02_Event_Management_Scheduling_SPEC.md) | Events, RSVP + capacity, coach assignment, attendance | Epic-01 |
| [Epic-03_CRM_Player_Management_SPEC.md](Epic-03_CRM_Player_Management_SPEC.md) | Player directory, labels, flags, notes, segmentation, metrics | Epic-01, Epic-02 |
| [Epic-04_LP_Content_System_SPEC.md](Epic-04_LP_Content_System_SPEC.md) | Playlists, drills, assignments, progress, paywall | Epic-01, Epic-02 |
| [Epic-05_Payments_Tokens_SPEC.md](Epic-05_Payments_Tokens_SPEC.md) | Stripe Connect, tokens, refunds, webhooks, ledger | Epic-01, Epic-02, Epic-04 |
| [Epic-06_Marketing_Growth_Tools_SPEC.md](Epic-06_Marketing_Growth_Tools_SPEC.md) | Referrals, assist counters, coupons | Epic-01, Epic-05 |
| [Epic-07_Super_Admin_System_Management_SPEC.md](Epic-07_Super_Admin_System_Management_SPEC.md) | Admin endpoints, feature flags, audit log, metrics | Epic-01, Epic-02, Epic-05 |
| [Epic-08_Forms_Registration_SPEC.md](Epic-08_Forms_Registration_SPEC.md) | Public camp/evaluation forms, submissions, conversion | Epic-01, Epic-05 |
| [Epic_Areas_Plan_Python.md](Epic_Areas_Plan_Python.md) | Delivery order, module map, parallelisation strategy | — |

## Structure of an epic

Each epic follows the same layout:

1. Description and backend outcomes
2. In scope (MVP) / Out of scope (Post-MVP)
3. Dependencies and provided interfaces
4. Roles and permission matrix
5. Domain model — tables, columns, constraints, indexes
6. API surface — endpoint table
7. User stories with acceptance criteria stated as API behaviour
8. Business rules enforced in the service layer
9. Backend flows
10. Background jobs
11. Performance and scale targets
12. Open questions
13. Epic Definition of Done

Acceptance criteria are written as verifiable server behaviour: request → status code → response
body → persisted state. Each maps to at least one named test.

## Working through an epic

Each epic is a specification, not an implementation plan. To turn one into executable work:

```
/requirements-analyst   # decompose an epic into tasks
/architect              # module placement + pattern decisions per epic
/api-designer           # nail the request/response schemas for one epic's endpoints
/writing-plans          # produce tasks/TASK-{N}/ implementation plan
/coder → /test-generator → /code-reviewer → /verify
```

Epic-level Definition of Done in each file is written to be checkable by `/verify` against
[../../.claude/DOD.md](../../.claude/DOD.md).
