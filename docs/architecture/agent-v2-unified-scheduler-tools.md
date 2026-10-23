# Agent Runtime V2 — unified scheduler and tools (A3/A4 continuation)

Status: **optional, integration-ready, default OFF**. This changes the **actual QQ bridge** if the V2 flags are enabled; the legacy route remains the default. PR #13 provided a standalone cycle engine and Maisaka's GPLv3 likelihood/gate source; this change connects them to the transport.

## Core message and action lifecycle

```text
OneBot receive -> identity + allowlist + deduplicate
               -> per-conversation message debounce (A2)
               -> UnifiedTurnScheduler / EventTurnGate (Maisaka-ported gate)
                    -> hold: no LLM invocation, keep limited public group excerpts
                    -> delay: wait and re-evaluate
                    -> trigger:
                      chat memory/privacy checks
                      -> visual model if image supplied
                      -> Planner V2
                         -> reply / wait / stop
                         -> tool: checked registry -> receipt -> replan [bounded]
                      -> Replyer (only for actual reply)
                      -> OneBot physical send(s)
                      -> only after ALL ACKs: commit dialogue/memory and frequency count
```

Private conversations and explicitly @-addressed messages enter the Planner promptly (after debounce and existing cooldown). Group passive listening is **separately disabled** by default, and never turns on just because V2 is enabled.

Group state is keyed by (bot account, group ID), not the sender. The normalized batch marks which member sent it. If a passive group message is not selected for response, only a bounded **in-memory**, group-public excerpt is retained for continuity; it is neither promoted to personal identity nor written into the personal Memory Core. It disappears on process restart.

The Maisaka probability features and sliding-window gate are based on **MaiBot's** observed chat data and have *not* been calibrated for ZhuangFY. The explicit group hourly cap and cooldown from QQ settings still apply on top, including when frequency=1.

## Configuration

In `SenseVoice/cognition_settings.json`, copy the optional fields:

```json
{
  "agent_v2_enabled": true,
  "agent_v2_tools_enabled": true,
  "agent_v2_max_rounds": 4,
  "agent_v2_max_tools": 2,
  "agent_v2_allow_silence_private": false,
  "agent_v2_allow_silence_group": false
}
```

In `SenseVoice/qq_settings.json`:

```json
{
  "message_debounce_mode": "natural",
  "passive_group_ids": [],
  "group_reply_frequency": 0.45
}
```

* `passive_group_ids` is an explicitly opted-in SUBSET of `allowed_group_ids`; empty means only @ in the configured groups is admitted, as before.
* Passive messages are **never** admitted through the legacy always-reply path. Stop V2 or remove the group ID to disable passive behavior.
* `group_reply_frequency` is a numerical 0–1 preference **only for passive group traffic**. Private chats and explicit @ bypass this gate (not the limiter).
* `agent_v2_tools_enabled=false` is the default. Tools only run for V2 QQ sessions when true; otherwise Planner tool requests fall back to a visible reply.
* Maximum 1–8 planning rounds, 0–6 tool invocations per logical turn, overall 90 seconds and a bounded timeout per tool. Duplicate tool calls with the same arguments are stopped.
* Users who directly initiate desktop voice/text sessions still get their existing direct-reply path; this PR does not add periodic Planner calls or tool control to continuous ASR.

## Tools and plugin extension

`src/zhuangfy/agent_v2/runtime.py` defines `AuthorizedToolRegistry`, `ToolBinding`, and `ToolReceipt`. Only the host can register functions. `tools.py` currently registers exactly TWO read-only tools:

- `calculator.evaluate`: AST-interpreted bounded arithmetic. No Python eval, imports, calls, attribute access, filesystem, arbitrary code, or exponentiation.
- `clock.utc_now`: returns an explicit UTC timestamp.

In `QqBridge.__init__`, the registered tools are provided by `build_builtin_registry()`. When tools are enabled, the Planner sees only actual **available tool names**; the loop can choose a single tool per step. An authorization denial or tool error returns a failed receipt and a normal response, never falsely claims success. Executed results are fed back as **untrusted contextual data**, not executable instructions or privileged prompt directions. The Replyer also sees bounded tool receipts.

Example future plugin registration (not enabled in this PR):

```python
from zhuangfy.agent_v2.runtime import ToolBinding

async def read_device_state(args: dict) -> str:
    # The host must validate device IDs, permissions and API connection here.
    raise NotImplementedError

bridge.tool_registry.register(
    ToolBinding(
        "mijia.get_state", read_device_state,
        scopes=frozenset({"private"}), mutating=False,
    )
)
```

**No Mijia, arbitrary HTTP fetch, command execution, file access, or third-party plugin** is registered yet. A future plugin host needs per-account identities, a verified binding, a separate permission check for mutating operations, confirm/retry semantics, audit and consent. Calling `ToolBinding` from a model is impossible; only application code registers it.

## Rollback & tests

Set `agent_v2_enabled=false` to restore the old behavior; set `agent_v2_tools_enabled=false` to keep V2's style decisions without tool execution; delete `passive_group_ids` to return to @-only groups.

```bash
PYTHONPATH=src python -m unittest -v tests.unit.test_agent_v2_scheduler_tools tests.unit.test_agent_v2_cycle_gate
cd SenseVoice && python -m unittest -v test_qq_bridge
```

End-to-end integration tests cover opt-in and denied group admission, shared group-session identity, successful read-only tool → tool receipt → second Planner round → Replyer, and denial of unauthorized device-control tools. Existing legacy QQ tests remain enabled in CI.
