# Focus F2 — durable fairness and channel-neutral identity

**Opt-in**. Existing QQ Focus `off`/`shadow`/`active` remains unchanged unless the new checkpoint switch is enabled.

## What is persisted?

`FocusCheckpointStore` persists only the explicit Focus group's membership fingerprint, the last completed session ID and fairness streak. **It does not store message text, private memory, pending events, tool inputs, or active leases.** On restart an in-flight turn is not resumed or blindly sent again: new events must pass through the QQ admission policy as usual. A member change invalidates the old checkpoint.

Checkpoint storage is per explicitly configured bot identity, preventing one QQ account's state from accidentally affecting another. For example:

```json
{
  "focus_mode": "active",
  "focus_groups": [{"id":"friends","sessions":["private:42","group:9"]}],
  "focus_checkpoint_enabled": true,
  "focus_checkpoint_account": "bot_123456"
}
```

The accounts/groups must already belong to QQ allowlists. Set `focus_checkpoint_account` to a stable unique label for the specific bot account. Checkpoints are written to local ignored `SenseVoice/private_state/qq_focus.sqlite3`. Default settings keep persistence off. Change mode back to `off` to disable Focus scheduling; the persisted state does not send messages.

## Cross-channel interface

`session_ref(channel, account, kind, peer)` returns a collision-resistant, channel-scoped logical key for *future* WeChat and voice adapters, e.g. `qq:bot1:private:42` vs `weixin:bot1:private:42`. It is a metadata contract, not a cross-platform identity merger. Other channels need to implement admission, account verification, event origin validation and correct async/executor integration before entering shared Focus.

## What remains

F2 here is **persistence + stable cross-channel identity contract**, not a single cross-process Focus daemon. QQ still runs its own Focus coordinator; WeChat and Qt voice are not yet members of one live shared event loop. No sharing of private contexts or memories. The independent checkpoint tests cover restart, active-lease nonrecovery, membership changes, bot isolation and default-off behavior.

Focus checkpoint failures do not obstruct cleanup of granted leases. Production-level multi-process Focus needs a separate broker/lease store and distributed ownership before full adoption.
