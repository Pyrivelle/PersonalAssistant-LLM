# Background Autonomy B0 — persistent consent-aware Goal Store

**Foundation only**. This PR deliberately does **not** start a background worker or send any proactive messages, schedule model calls, install plugins or control real devices. B0 solves reliable task provenance and restart state before enabling autonomy.

## Model

`src/zhuangfy/agent_v2/goals.py` implements a stdlib SQLite `GoalStore`. Every goal has:
- `goal_id` idempotency key and `source_event_id` identifying the explicit authorization event;
- `owner_scope` to keep user's/private-group tasks separate; an allowlist of owner scopes is supplied independently when claiming;
- explicit boolean `consent` (default false);
- `due_at`, `expires_at`, at most 1–3 attempts, `lease_until`, `last_receipt`, and `status`;
- an append-only audit table for execution attempts.

The table intentionally contains no user message bodies, model prompts or account secrets.

## API and lifecycle

```text
approved event -> GoalStore.create(consent=True, ...)
   -> host task runner polls claim_due(now, authorized_owner_scopes)
   -> atomic SQLite lease + unique attempt event_id
   -> trusted READ-ONLY operation + timeout (runner to be added in B1)
   -> GoalStore.finish(event, success, receipt)
   -> done / retry after delay / failed
   -> optional per-owner notification, subject to separate outbound permission
```

If the host crashes while a read-only task is running, the lease expires and a later process can reclaim the task, **at least once**. A successful result from an outdated lease or from a revoked goal is rejected. Duplicate goal creation and double completion are ignored. SQLite writes are atomic, scoped and testable after reopening the same database.

**At-least-once retry is never safe for irreversible physical actions without external idempotency/reconciliation.** Do not use this store for lights, purchases, external messages, file modifications or other side effects yet. Model-proposed goal creation is NOT authorization; consent/owner identity must be checked by a future host policy manager.

## Future B1/B2

- Authorized event adapter (timer/plugin result) -> Focus -> Goal runner → bounded Planner/Tool Runtime.
- A host service with a clock and startup/restart lifecycle; persistence location outside the code repository; retry policies/backpressure/observability.
- B1 must integrate exact skill/tool grants, separate permission-to-observe vs permission-to-act, revoke/cancel in-flight tasks and no unsolicited messages by default.
- B2 may later add proactive social interaction, only after per-channel/person/contact opt-in, quiet hours and outbound frequency budgets.

## Tests

Offline suite validates consent defaults, tenant/owner scoping, atomic claims, expiration, restart recovery, limited retries, stale receipts and cancellation. No external APIs/LLMs are needed.

This branch is independent of Focus F1 and Voice V0; it can be reviewed/merged separately after CI. Current QQ, WeChat and voice behavior remain unchanged.
