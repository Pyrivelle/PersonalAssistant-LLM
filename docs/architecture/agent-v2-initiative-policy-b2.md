# B2 (first step) — when should ZhuangFY initiate a conversation?

**Review-only, opt-in prototype.** The social policy does not send QQ/WeChat messages or synthesize voice, and cannot bypass contact consent. It turns grounded B1 goal follow-up ideas into a stored proposal only.

## Model: two separate decisions

1. **Host timing gate**: contact/owner opt-in, local quiet hours, human interaction idle duration, active voice/chat/typing state, per-contact gap and daily proactive reply budget. These are facts from the host, not information that the LLM may imagine or override.
2. **Initiative Planner**: only for eligible, grounded B1 `initiative_drafts` with a confirmed source event and a user-approved topic. Uses the preconfigured model `planner` route to choose `ask | share | defer | silence`, with confidence and a short draft. Malformed/low-confidence replies fail closed or defer. Outputs are **suggestions**, not a dispatched message.

The model must never fabricate shared experience, infer user emotions without evidence, or start a chat because it is time to produce a greeting.

The `InitiativeReviewer.review_next` service reads existing B1 drafts, checks scope, asks the host social gate, calls a supplied planner only when eligible and stores an idempotent review in `initiative_reviews`. The `InitiativeInbox` retains the original draft and its evidence; different owners cannot read each other's review state. Repeated checks do not duplicate an existing model review.

## Default policy

```python
PresencePolicy(
    owner_scope="qq:private:YOUR_ALLOWED_USER",
    enabled=False,            # explicit opt-in required
    quiet_start=22,
    quiet_end=8,
    min_human_idle_seconds=1800,
    min_outbound_gap_seconds=8*3600,
    daily_limit=1
)
```

The host must provide an explicit local hour in the user's intended timezone, genuine recent human-message and outbound-delivery timestamps, and truthful `voice_active`, `user_typing` and `active_chat_turn` signals. Unknown/stale values must not be silently replaced with a claim that the user is free. No unattended social send is enabled here.

`plan_initiative(settings, api_key, topic, evidence)` routes through the existing stage-aware Planner model with a special bounded system instruction. The tool receipt is marked untrusted and cannot grant permissions. That function is *not* called by an automatic service until per-account profile permissions and runtime presence signals have been implemented.

## What is still required for B2 actual sending

- Unified event/presence feed from QQ/voice/WeChat into one host with durable recent-activity and real timezone settings;
- A separate, explicit **outbound consent** per recipient and channel, quiet time + quota enforcement at both planning and send time;
- Replyer that transforms a social act into persona-aware wording, with audited message ACK/idempotency;
- Avoid repeating already-asked questions, distinguish one-shot follow-ups from unsolicited social initiatives;
- Evaluate false interruptions, frequency, user responses and whether the generated interest feels natural.

This first step deliberately keeps the most sensitive part (spontaneous outreach) disabled while proving the decision flow end-to-end with offline reviews.
