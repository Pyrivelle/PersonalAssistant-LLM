# Voice V0 — finalized ASR segments → logical user turn

This PR introduces a transport-neutral, **pure state machine** only. The existing Qt/SenseVoice GUI behavior remains unchanged until V1 opt-in integration. No new background microphone capture, model call, network request or recording persistence is added.

## Problem

A VAD boundary (short silence) is not necessarily the end of a user's conversational turn. Current continuous ASR handling may trigger separate ChatWorker/Planner executions on each finalized ASR fragment. We need a bounded aggregation layer before Planner to reduce premature replies and wasted generation during pauses.

## API

`src/zhuangfy/agent_v2/voice_turn.py`:

- `VoiceTurnManager.offer_final(text, at=monotonic_time, language, authorized, final=True)`: accepts only verified **final** ASR snippets; refuses partial/untrusted data. May return an already-expired previous turn when a new snippet arrives later.
- `deadline`: earlier of last-final + pause gap or first-final + absolute max-wait. V0 defaults: 1.3 s pause, 5 s maximum, six pieces and 800 characters, all configurable.
- `take_ready(now=monotonic_time)`: once due, atomically emits one `VoiceTurn` exactly once; repeated checks do not repeat the same model request.
- `flush(now=...)`: explicit endpoint / stopping capture.
- `interrupt()`: discards incomplete snippets and advances a generation token. `accepts_output(turn)` lets the caller reject stale prior model outputs. It **does not** cancel a cloud API request by itself.

The manager has no Qt dependencies, timers, callbacks, threads or LLM calls; it must be driven by a host timer/coroutine. The host must dispatch any turn returned from `offer_final` before scheduling the next deadline, otherwise the prior turn would be lost. It is intended to sit **after** ASR, activation and echo checks, not before them.

## V1 integration steps

1. Add an opt-in `voice_turn_mode=combined` flag to the desktop GUI, default to legacy.
2. After `ConversationGate.process`, feed authorized finalized ASR text to `VoiceTurnManager` rather than immediately invoking `begin_chat_turn`. Preserve one-shot voice shortcuts and barge-in handling.
3. Use Qt QTimer on the GUI thread to call `take_ready` at the deadline; submit only one logical `ChatWorker` per emitted turn.
4. At genuine barge-in, invalidate pending output, cancel or discard stale TTS/LLM tasks, and guard delayed completion by turn/generation token.
5. Measure sentence endpoint accuracy, perceived first-audio latency, partial waste and cloud Token consumption with real models before changing defaults.

**Important:** Doing partial ASR at word granularity is not yet supported, and merge errors are possible when a long pause actually indicates a complete sentence. The V1 mode should be configurable and easy to disable. This module does not add autonomous speech or background self-triggered conversation.
