# ZhuangFY Agent Runtime V2 — A0/A1/A2

**Experimental, off by default.** No live memory migration or Planner replacement.

## A0 assessment

MaiBot's Maisaka is not a standalone library: runtime/reasoning_engine/focus depend on its message repository, session manager, plugin/MCP providers, config, LLM wrappers, monitor events and checkpoint store. A direct port of full Maisaka therefore requires either substantial adapters or portions of the MaiBot infrastructure. Original MaiBot code is GPL-3.0. No MaiBot code is copied into this change; debounce is independently implemented from behavior specifications. Review ZhuangFY's distribution/license policy before direct source reuse.

Sources reviewed:
- https://github.com/Mai-with-u/MaiBot/tree/main/src/maisaka
- https://github.com/Mai-with-u/MaiBot/blob/main/src/chat/debounce/README.md
- https://github.com/Mai-with-u/MaiBot/blob/main/LICENSE

## A1 message contract

`UnifiedMessage` carries platform, bot account, session ID, sender ID, source message ID, timestamp, quoted message ID, and typed parts (text, image, emoji, audio, file, mention and quote). QQ group identity is based on group, not sender; personal Memory Core access must still resolve authorized identities separately. `from_qq_incoming` must only run **after** existing allowlist and mention checks. Media references are opaque and not authorization to download or disclose media.

## A2 debounce

Per-conversation debouncer with four configuration choices: `off` (default; preserve existing quiet_seconds), `fast` (1/3/1/0.5 seconds), `natural` (2/5/2/0.5), `steady` (4/7/4/2). Columns mean quiet open / active open / same-user continuation / different-user continuation. Quiet vs active threshold is 30 seconds. Bare image waits at least 4 seconds; emoji continuation cannot extend existing wait; @/mention is at most one second; batch deadline at most eight seconds from first arrival. No LLM is called. Existing QQ rate limits and group @-only policy are preserved.

Test:
```bash
PYTHONPATH=src python -m unittest -v tests.unit.test_agent_v2_foundation
```

Follow-up: A3 Planner + reply/wait/stop, A4 Replyer, A5 group autonomy, A6 shared Focus with privacy scopes, A7 social intent for images/emoji, A8 tools and proactive behavior. End-to-end channel ACK, reconnect and interruption tests remain required before enabling by default.


> 2026-10-23 update: the project subsequently adopted GPLv3 and directly imported two attributed Maisaka gate files. See `docs/development/third-party.md` and `docs/architecture/agent-v2-planner-replyer.md`; A0's earlier "no upstream source" statement documents the initial state only.
