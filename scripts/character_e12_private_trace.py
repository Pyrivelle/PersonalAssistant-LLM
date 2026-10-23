"""Run a synthetic private-chat E1.2 Planner/Replyer diagnostic; no sends or tools."""
import argparse
from dataclasses import asdict
from html import escape
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "SenseVoice")]

def render(rows, provider, model):
    chunks = []
    for r in rows:
        parts = [
            ("输入", r["message"]),
            ("合成上下文", "\n".join(r["background"])),
            ("Planner 决策（非隐藏推理）", json.dumps(r["planner"], ensure_ascii=False, indent=2)),
            ("宿主动作", r["action"]),
            ("Replyer 指导", r["guidance"]),
            ("Replyer 原始回答", r["raw"]),
            ("R1 最终回答", r["final"]),
            ("自动提示", ", ".join(r["warnings"])),
            ("阶段错误", r["error"]),
        ]
        chunks.append("<details><summary>" + escape(r["case_id"]) + "</summary>" +
                      "".join("<h3>" + escape(k) + "</h3><pre>" + escape(str(v or "")) +
                              "</pre>" for k, v in parts) + "</details>")
    return ("<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
            "<style>body{font:16px system-ui;max-width:920px;margin:auto;padding:20px}"
            "details{padding:12px;margin:12px 0;border:1px solid #ddd}"
            "pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f5f5;padding:12px}</style>"
            "<h1>E1.2 私聊 P+R 诊断</h1><p>" + escape(provider + " / " + model) +
            "。合成消息；记录可观察决策，不是模型内部思考。"
            "不执行 QQ 发送、设备工具或真实记忆读取。</p>" + "".join(chunks) + "</html>")

def run(args):
    from llm import LlmSettings, PROVIDER_BASE_URLS
    from model_routes import invoke_stage
    from zhuangfy.persona.packs import ZHUANG_FANGYI_V1
    from zhuangfy.agent_v2.planner import plan_turn_v2, choose_action
    from zhuangfy.persona.character_core_v2 import character_behavior
    from zhuangfy.persona.relationship_behavior_r1 import behavior_r1, tidy_r1_reply

    token = os.environ.get("ZHUANGFY_E1_API_KEY", "").strip()
    if not token or "\n" in token or "\r" in token:
        raise ValueError("Configure ZHUANGFY_E1_API_KEY before manual model tests")
    settings = LlmSettings(provider=args.provider, base_url=PROVIDER_BASE_URLS[args.provider],
                           model=args.model, system_prompt=ZHUANG_FANGYI_V1.prompt(),
                           memory_enabled=False).validated()
    data = json.loads((ROOT / "resources/evaluation/character_e12_private_cases.json").read_text(encoding="utf-8"))
    cases = data["cases"][:args.max_cases]
    rows = []
    args.out.mkdir(parents=True, exist_ok=True)
    for case in cases:
        r = dict(case_id=case["id"], message=case["message"], background=case["background"],
                 planner=None, action="", guidance="", raw="", final="", warnings=[], error="")
        rows.append(r)
        try:
            plan = plan_turn_v2(settings, token, [], case["message"],
                                character_core_v2=True, character_channel="qq_private",
                                relationship_behavior_r1=True,
                                available_tools=(), trace_id="e12-p-" + case["id"])
            r["planner"] = asdict(plan)
            r["action"] = choose_action(plan, allow_silence=True, tools_enabled=False)
            if r["action"] == "reply":
                cue = behavior_r1(settings.system_prompt, case["message"], channel="qq_private")
                scene = character_behavior(settings.system_prompt, case["message"], channel="qq_private")
                r["guidance"] = plan.prompt()
                emotion = cue.guidance + "\n" + r["guidance"] + "\n" + scene.guidance
                notes = "以下仅为虚构测试场景，不是现实用户记忆。"
                if case["background"]:
                    notes += "\n" + "\n".join(case["background"])
                r["raw"] = invoke_stage("reply", settings, token, [], case["message"],
                                         emotion_context=emotion, context_notes=notes,
                                         trace_id="e12-r-" + case["id"])
                r["final"] = tidy_r1_reply(settings.system_prompt, case["message"],
                                            r["raw"], channel="qq_private")
                if "（" in r["raw"] or "(" in r["raw"]:
                    r["warnings"].append("raw_parentheses_review")
                if "（" in r["final"] or "(" in r["final"]:
                    r["warnings"].append("final_parentheses_review")
                if r["final"] != r["raw"]:
                    r["warnings"].append("r1_cleanup_changed")
            else:
                r["warnings"].append("replyer_skipped_" + r["action"])
        except Exception as exc:
            r["error"] = type(exc).__name__  # Never persist provider exception text.
        finally:
            (args.out / "trace.json").write_text(
                json.dumps({"schema": 1, "kind": "private_pr_trace",
                            "provider": args.provider, "model": args.model, "rows": rows},
                           ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out / "report.html").write_text(render(rows, args.provider, args.model), encoding="utf-8")
    errors = sum(bool(r["error"]) for r in rows)
    (args.out / "summary.md").write_text(
        "E1.2: " + str(len(rows)) + " cases, " + str(errors) + " stage errors\n", encoding="utf-8")
    return 2 if errors else 0

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", required=True, choices=("siliconflow", "deepseek", "kimi_cn", "kimi_global", "hachimi"))
    parser.add_argument("--model", required=True)
    parser.add_argument("--max-cases", type=int, choices=(3, 7, 14), default=3)
    parser.add_argument("--out", type=Path, default=ROOT / "build/e12-private")
    parser.add_argument("--allow-network", action="store_true")
    args = parser.parse_args()
    if not args.allow_network:
        parser.error("Live model requests require --allow-network")
    if not args.model.strip() or len(args.model) > 128 or any(ord(c) < 32 for c in args.model):
        parser.error("Invalid model")
    if args.out.resolve().is_relative_to((ROOT / "resources").resolve()):
        parser.error("Do not overwrite versioned fixtures")
    return run(args)

if __name__ == "__main__":
    raise SystemExit(main())
