"""Explicit opt-in E1 model capture: synthetic inputs, reply route only.

This script makes real billable model calls ONLY when --allow-network is supplied.
It never invokes QQ, writes real memories, schedules reminders or sends messages.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "SenseVoice"))

from zhuangfy.evaluation.character_e1 import load_cases
from zhuangfy.persona.character_core_v2 import character_behavior
from zhuangfy.persona.knowledge_v1 import retrieve_character_knowledge, knowledge_context


def capture(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Call reply-stage LLM for synthetic E1 scenarios")
    parser.add_argument("--variant", choices=("v1", "ccv2", "ccv2_ckv1"), required=True)
    parser.add_argument("--allow-network", action="store_true",
                        help="Acknowledge that real model/API calls may incur costs")
    parser.add_argument("--cases", type=Path,
                        default=ROOT / "resources" / "evaluation" / "character_e1_cases.json")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--max-cases", type=int, default=300)
    args = parser.parse_args(argv)
    if not args.allow_network:
        parser.error("Model capture requires explicit --allow-network")
    if not 1 <= args.max_cases <= 300:
        parser.error("--max-cases must be in 1..300")
    if args.out.resolve().is_relative_to((ROOT / "resources").resolve()):
        parser.error("Do not overwrite reviewed evaluation fixtures in resources/")
    # Imports are deliberately deferred: offline CI never contacts models.
    from llm import load_key, load_settings
    from model_routes import invoke_stage
    from fangyi_skill import guidance as fangyi_guidance
    settings = load_settings()
    key = load_key(settings.provider)
    examples = load_cases(args.cases)[:args.max_cases]
    output = {
        "schema": 1, "source": "model_capture", "variant": args.variant,
        "capture_info": {
            "configured_model": settings.model, "provider": settings.provider,
            "persona_sha256": hashlib.sha256(settings.system_prompt.encode("utf-8")).hexdigest(),
            "dataset_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
            "note": "User-initiated model capture; model routes may contain fallback candidates."
        },
        "responses": []
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    for idx, case in enumerate(examples, 1):
        context = (
            "以下是专门编造的离线 E1 测试情境，而非现实用户个人资料；"
            "不要把它当作已经真实发生的共同记忆。\n"
            f"本轮模拟熟络级别：{case.relation}；沟通通道：{case.channel}。"
        )
        if case.background:
            context += "\n明确的合成上下文：\n" + "\n".join(case.background)
        prompt = ""
        if args.variant == "v1":
            prompt = fangyi_guidance(settings.system_prompt, case.message)
        else:
            channel = case.channel if case.channel in ("qq_private","qq_group","initiative") else "chat"
            prompt = character_behavior(settings.system_prompt, case.message,
                                        channel=channel).guidance
        if args.variant == "ccv2_ckv1":
            matches = retrieve_character_knowledge(
                settings.system_prompt, case.message, enabled=True)
            if matches and len(context) + len(knowledge_context(matches)) < 2500:
                context += "\n" + knowledge_context(matches)
        # This captures JUST the same reply stage; it does not pretend to
        # simulate real-time focus, tool actions, memory retrieval or B2 gates.
        # Especially initiative_silence tests require a later action-trace E2.
        reply = invoke_stage(
            "reply", settings, key, [], case.message,
            emotion_context=prompt, context_notes=context,
            trace_id=f"e1-{args.variant}-{case.id}")
        output["responses"].append({"case_id": case.id, "response": reply})
        # Crash-tolerant capture; write only to caller-specified local directory.
        temporary = args.out.with_suffix(args.out.suffix + ".tmp")
        temporary.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        temporary.replace(args.out)
        print(f"[{idx}/{len(examples)}] captured {case.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(capture())
