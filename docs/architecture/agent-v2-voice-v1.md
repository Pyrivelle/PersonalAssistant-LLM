# Voice V1 — Qt/SenseVoice live conversation turn scheduling

V1 integrates the V0 `VoiceTurnManager` into the **actual** continuous-SenseVoice UI behind an opt-in setting. No API/provider change, and the default remains `legacy`.

## Turn-taking contract

- On finalized, authorized ASR results, run the existing wake/echo/one-shot checks first.
- `voice_turn_mode=combined`: buffer accepted text in `VoiceTurnManager`, update a single-shot Qt timer on the GUI thread. **Partial ASR hypotheses never enter Planner.**
- Additional finalized utterances within the short pause (1.3 seconds default) are combined. An absolute 5-second window, six-segment cap and character cap prevent indefinite waiting.
- If a new VAD speech event begins while text is pending, suspend timer dispatch until the speech has a final ASR result; this avoids responding mid-sentence. A genuine barge-in preserves existing speaker/headphone rules and invalidates previous buffered generations.
- When the turn ends, invoke the existing `begin_chat_turn` once using the concatenated text; the existing `ChatWorker`, Planner, TTS and memory paths remain unchanged.
- Toggling `legacy` in the UI restores the previous behavior after restart. Stopping continuous listening flushes the buffered state **without sending**.

## Enable

In Qt window choose **对话轮次 → 连续对话：等待补充后合并回复（实验）**. The choice saves to ignored `SenseVoice/settings.json`:

```json
{"voice_turn_mode": "combined"}
```

A new install uses `legacy`. Unlike microphone VAD's short silence threshold, the additional turn buffer is for whole conversational turns and may add ~1.3s to first-response latency. Actual UX must be evaluated with your ASR model and TTS latency before changing defaults.

## Verification

Pure state-machine tests remain in Agent V2 auto-discovery. A new Windows offscreen GUI job executes `SenseVoice/test_voice_loop.py` including combined segment buffering, paused dispatch while user speaks, stop/reset and setting persistence. Tests use fake Qt workers and no real device, cloud account or microphone; this is **not yet a live ASR quality/latency measurement**.

## Limitations

This step does not give the Planner proactive speech permissions, and it does not join the Qt GUI into QQ's live Focus coordinator; UI/worker thread boundaries remain explicit. A late running cloud request may still consume API tokens even when its result is ignored. Echo handling still uses existing heuristics rather than dedicated acoustic echo cancellation. Follow-up work should measure false endpoint rate, barge-in quality and tune pause windows from real user recordings.
