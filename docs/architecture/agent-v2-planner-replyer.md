# ZhuangFY Agent Runtime V2 — A3 Planner & A4 Replyer

Status: **opt-in experimental**; the legacy Planner remains available and is the default.

## Intent and inspiration

MaiBot [Maisaka Planner](https://github.com/Mai-with-u/MaiBot/blob/main/prompts/zh-CN/maisaka_chat.prompt) separates next-action selection from a [Replyer](https://github.com/Mai-with-u/MaiBot/blob/main/prompts/zh-CN/maisaka_replyer.prompt). This PR independently implements a smaller, transport-neutral Planner decision contract and Replyer guidance rather than copying GPL-3.0 files. It **does not** claim to be a complete Maisaka port, Focus or tool runtime.

## Configuration

Copy the keys from `SenseVoice/cognition_settings.example.json` into your existing private `cognition_settings.json`:

```json
{
  "agent_v2_enabled": true,
  "agent_v2_allow_silence_private": false,
  "agent_v2_allow_silence_group": false,
  "agent_v2_wait_max_seconds": 8
}
```

- `agent_v2_enabled` is **false by default**. When true, it takes precedence over the older `enabled` switch without deleting the legacy `plan_turn` path.
- `agent_v2_allow_silence_private` and `agent_v2_allow_silence_group` default to false. The caller, not the LLM, ultimately authorizes `stop`.
- `agent_v2_wait_max_seconds` is an integer 1–12; default 8. Planner wait requests are capped and QQ automatically replies on the next pass rather than waiting forever.
- No changes to private account allowlists, group @ admission, cooldowns, memory consent, voice allowlists or model route configuration.

## Planner contract

`zhuangfy.agent_v2.planner` calls the existing `planner` stage of `model_routes.invoke_stage`, using its configured candidates and failure/retry rules. Expected JSON fields: `action` (reply/wait/stop/tool), `confidence`, `social_intent`, `reply_act`, `scene`, `tone`, `attention`, `reply_style`, `prefer`, `avoid`, `delivery`, and optional `reply_parts` (0 means sender decides; 1–3 caps QQ bubbles), `emotion`, `emotion_intensity` (0–1), `wait_seconds`, `tool_name`, `tool_arguments`. Invalid or low-confidence plans degrade safely to reply. Avoid using planner output as a source of new user facts.

- `reply`: create one logical model reply; QQ's existing formatter may deliver it in up to three **physical** bubbles.
- `wait`: in QQ, requeue the same messages and wait at most the configured number of seconds before reconsideration. Only one wait per logical batch; subsequent wait requests are treated as reply. New inbound messages can wake the pending wait early. For image-bearing turns, re-waiting is disabled to avoid repeating a costly vision request. Planner does not run an indefinite polling loop. No separate follow-up speech is generated after reply.
- `stop`: explicitly opt-in per conversation type; no model reply is sent and no fictional assistant message is committed. Explicit commands (such as erase or retry) still follow their existing bridge paths.
- `tool`: recorded as intent only. **No external action runs** until a separate authenticated Tool Runtime provides allowlisted execution and receipts (A8). When unsupported, fall back to a visible reply without claiming the action happened.

Qt desktop text and voice interfaces currently require an output for each initiated turn, so `wait` and `stop` are not enacted there. They still benefit from the structured Planner/Replyer expression guidance.

## Replyer

`zhuangfy.agent_v2.replyer.build_replyer_guidance` builds bounded, non-authoritative style cues; the existing `reply` stage remains the actual generation model. It handles social intent, conversational act, context, uncertainty, emotion intensity, tone, length, and preferred/avoided phrasing, while preserving the user's existing character/persona prompt and memory permissions.

For a standalone affectionate sticker/photo the vision output is considered evidence of **visible content**, not proof that the user is giving a physical rose or confessing love. The Planner may hypothesize social intent; Replyer responds naturally without treating that hypothesis as certain.

## Validation and limitations

Automated test coverage:
- action JSON parsing, schema validation and malformed-output fallback
- wait time cap, one-wait rule and silent-action opt-in
- tool-not-available fallback (never invoke unregistered device actions)
- channel voice eligibility, guidance size, and actual model route reuse
- QQ end-to-end mocks for wait/stop/reply guidance
- CI compiles Python and runs offline Agent V2 regressions

Further work: A5 group participation and send timing; A6 cross-session Focus and per-person/group privacy; A7 richer media/tool-context semantics; A8 authorized tool execution and proactive tasks. Chat history serialization and model-side trace visualizations also remain to be migrated. Live conversation quality, latency and cost require opt-in evaluation with real configured models before switching defaults.

## Rollback

Set `agent_v2_enabled` back to `false` and restart the QQ Bridge / affected desktop conversation window. The legacy Planner and message flow remain intact; no data migration is required.


## Source-port reinforcement

Following the GPLv3 licensing decision, the independent placeholder scoring is replaced by the actual upstream MaiBot fitted **reply-likelihood** and **DynamicReplyGate** classes in `src/zhuangfy/agent_v2/maisaka_port/`. The ZhuangFY `EventTurnGate` adapter preserves authorization, debounce, @ override, conversation scope, timer/tool-result continuation and future plugin admission boundaries. The upstream fitted probabilities are **not validated on ZhuangFY conversations**; do not activate passive group admission until actual session features and user permissions are connected.

A separate `AgentCycleEngine` now models a bounded Planner → authorized Tool → receipt → Planner loop; each external tool must be explicitly registered, scoped and permitted. The real QQ bridge does not register external tools in this PR; `agent_v2_tools_enabled` defaults to false. **These new building blocks do not yet replace Maisaka's full Focus, interrupt control, or event scheduler.**
