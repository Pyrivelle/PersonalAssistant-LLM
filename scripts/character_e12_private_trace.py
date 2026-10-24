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
            ("传给 Replyer 的合并约束（最多 2000 字符）", r.get("all_cues", "")),
            ("Replyer 原始回答", r["raw"]),
            ("R1 最终回答", r["final"]),
            ("自动提示", ", ".join(r["warnings"])),
            ("关系快照（合成测试）", r.get("relation_hint", "")),
            ("关系事件候选（非持久化）", json.dumps(r.get("relation_candidate"), ensure_ascii=False, indent=2)),
            ("候选证据检查", r.get("relation_verdict", "")),
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
    from zhuangfy.llm.routes import ModelRoutes, StagePolicy
    from zhuangfy.agent_v2.planner import plan_turn_v2, choose_action
    from zhuangfy.persona.character_core_v2 import character_behavior
    from zhuangfy.persona.relationship_behavior_r1 import behavior_r1, tidy_r1_reply
    from zhuangfy.relationship import RelationshipSnapshot, RelationshipCandidate, PROMPT

    token = os.environ.get("ZHUANGFY_E1_API_KEY", "").strip()
    if not token or "\n" in token or "\r" in token:
        raise ValueError("Configure ZHUANGFY_E1_API_KEY before manual model tests")
    settings = LlmSettings(provider=args.provider, base_url=PROVIDER_BASE_URLS[args.provider],
                           model=args.model, system_prompt=ZHUANG_FANGYI_V1.prompt(),
                           memory_enabled=False).validated()
    stage_policy = StagePolicy(retries=1, timeout=75, total_timeout=162)
    fixed_routes = ModelRoutes(planner=stage_policy, reply=stage_policy)
    data = json.loads((ROOT / "resources/evaluation/character_e12_private_cases.json").read_text(encoding="utf-8"))
    cases = data["cases"][:args.max_cases]
    rows = []
    args.out.mkdir(parents=True, exist_ok=True)
    # Synthetic starting profile only. Never saved to the production Memory Core.
    relation = None
    if args.relationship_tier != "unassigned":
        seeds = {"L0": (10, 10, 0), "L1": (35, 30, 15),
                 "L2": (65, 65, 35), "L3": (85, 80, 80), "L4": (95, 90, 95)}
        f, t, closeness = seeds[args.relationship_tier]
        relation = RelationshipSnapshot(
            "synthetic-agent", "synthetic-person", 1, f, t, closeness,
            args.relationship_tier, "L4",
            args.relationship_tier in ("L3", "L4"), args.relationship_tier != "L4",
            "natural", True, False,
            False, False, False)
    rel_tier = relation.tier if relation else "unassigned"
    rel_hint = relation.prompt_hint() if relation else ""
    for case in cases:
        r = dict(case_id=case["id"], message=case["message"], background=case["background"],
                 planner=None, action="", guidance="", all_cues="", raw="", final="", warnings=[], error="",
                 relation_hint=rel_hint, relation_candidate=None, relation_verdict="not_run")
        rows.append(r)
        try:
            plan = plan_turn_v2(settings, token, [], case["message"],
                                character_core_v2=True, character_channel="qq_private",
                                relationship_behavior_r1=True,
                                available_tools=(), trace_id="e12-p-" + case["id"],
                                routes=fixed_routes,
                                trusted_context_notes="\n".join(case["background"]),
                                relationship_tier=rel_tier, relationship_hint=rel_hint)
            r["planner"] = asdict(plan)
            r["action"] = choose_action(plan, allow_silence=True, tools_enabled=False)
            if r["action"] == "reply":
                cue = behavior_r1(settings.system_prompt, case["message"],
                                  channel="qq_private", tier=rel_tier)
                scene = character_behavior(settings.system_prompt, case["message"], channel="qq_private")
                r["guidance"] = plan.prompt()
                emotion = cue.guidance + "\n" + rel_hint + "\n" + r["guidance"] + "\n" + scene.guidance
                r["all_cues"] = emotion[:2000]
                notes = "以下仅为虚构测试场景，不是现实用户记忆。"
                if case["background"]:
                    notes += "\n" + "\n".join(case["background"])
                r["raw"] = invoke_stage("reply", settings, token, [], case["message"],
                                         routes=fixed_routes,
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
            if args.evaluate_events:
                # Capture independent model judgement for analysis only;
                # no relationship database, tools or persistence is updated.
                proposal = invoke_stage(
                    "planner", settings, token, [], case["message"][:1200],
                    routes=fixed_routes, system_prompt=PROMPT,
                    trace_id="e13-event-" + case["id"])
                candidate = RelationshipCandidate.parse(proposal, "synthetic-" + case["id"])
                r["relation_candidate"] = asdict(candidate)
                quote = candidate.evidence_quote.strip()
                valid = candidate.event_kind == "none" or (
                    candidate.confidence >= .9 and len(quote) >= 4
                    and quote in case["message"])
                r["relation_verdict"] = (
                    "none" if candidate.event_kind == "none" else
                    "quoted_candidate_for_review" if valid else "rejected_by_host_checks")
                if not valid:
                    r["warnings"].append("relationship_candidate_rejected")
        except Exception as exc:
            r["error"] = type(exc).__name__  # Never persist provider exception text.
        finally:
            (args.out / "trace.json").write_text(
                json.dumps({"schema": 1, "kind": "private_pr_trace",
                            "provider": args.provider, "model": args.model,
                            "synthetic_relationship_tier": rel_tier,
                            "evaluate_events": args.evaluate_events, "rows": rows},
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
    parser.add_argument("--relationship-tier", choices=("unassigned","L0","L1","L2","L3","L4"), default="unassigned")
    parser.add_argument("--evaluate-events", action="store_true")
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
