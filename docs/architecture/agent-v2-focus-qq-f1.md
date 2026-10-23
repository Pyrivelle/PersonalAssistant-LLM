# Focus F1 — QQ attention runtime integration

Status: experimental. Default remains `focus_mode: "off"`.

PR #16 added metadata-only FocusCoordinator. This change connects admitted QQ turns to it before the existing global generation lock. The Focus broker does not receive message content, context prompts, memory queries, tool arguments or reply text.

## QQ config

Add to `SenseVoice/qq_settings.json` (all actual local configs remain ignored by Git):

```json
{
  "focus_mode": "shadow",
  "focus_groups": [
    {"id": "test_circle", "sessions": ["private:42", "group:9"]}
  ]
}
```

These are examples only: replace IDs with actual user and group IDs already configured in `allowed_private_ids` and `allowed_group_ids`. A session may join **only one** explicit group. Missing groups are not joined automatically. Group admission still follows existing allowlists and @/passive-group settings.

- `off` (default): Focus does not alter QQ behavior.
- `shadow`: approved turns are observed as metadata; they do not wait for or share the active lease.
- `active`: one Focus lease per explicit group; queued QQ turns compete using directed priority, aging and session fairness. Claim happens before the process-wide `generation_lock`; a lease is released on success, error and cancellation.

Each QQ Bridge instance has its **own** Focus coordinator; this PR does not unify multiple QQ bot accounts or provide cross-process/global Focus. It also does not merge private and group histories: only execution scheduling is shared. `FocusSignal` holds account-local session IDs and event IDs, never message bodies. Authorization and frequency gate remain upstream of the Focus broker. The Focus feature flag is unrelated to enabling autonomy: it does not launch background tasks or unsolicited messages.

## Limitations

This is the first integration step, **not** the complete Maisaka `FocusGroupRuntime`. There is no persistent checkpoint, shared multi-channel Planner history, real-time event preemption of an in-progress LLM request, or voice/WeChat channel registration. The global QQ `generation_lock` remains in place and means Focus can prioritize only turns that have already reached its queue; it cannot cancel an active generation or arbitrarily reorder messages that have not arrived. The `shadow` mode records metadata-only observations for later comparison.

## Validation / rollback

The CI suite tests priority handling, per-scope leases, cancel/unblock, QQ config allowlist rejection and one actual simulated QQ reply through Focus shadow. The legacy QQ tests also run.

For rollback set `focus_mode` to `off` and restart the QQ Bridge. No persistent migration is involved.

Next: F2 durable Focus checkpoint and opt-in multiple channel adapters; VoiceTurnManager V0 followed by Qt/SenseVoice V1; Event/Goal Store B0 and autonomous tasks only after consent and bounded execution rules are validated.
