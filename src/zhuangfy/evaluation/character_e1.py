"""Character Evaluation E1: reproducible response audits and reviewable reports.

Deterministic checks only prove *listed* constraints. Character authenticity and
warmth cannot be decided by a regex: those require blind human evaluation.
This module never reads private conversation files, invokes models or sends QQ.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html import escape
from pathlib import Path
import json
import re

WEIGHTS = {
    "character_consistency": 25,
    "naturalness": 20,
    "situational_fit": 15,
    "lore_grounding": 15,
    "relationship_continuity": 15,
    "social_boundaries": 10,
}
CATEGORIES = frozenset({
    "everyday", "research", "distress", "disagreement", "achievement",
    "closeness", "lore", "group", "memory", "initiative",
})
RELATIONS = frozenset({"L0", "L1", "L2", "L3"})
CHANNELS = frozenset({"qq_private", "qq_group", "desktop", "initiative"})


@dataclass(frozen=True)
class Case:
    id: str
    category: str
    relation: str
    channel: str
    message: str
    background: tuple[str, ...]
    evaluator_note: str
    hard_forbid: tuple[str, ...]
    max_chars: int = 500

    @classmethod
    def parse(cls, raw: object) -> "Case":
        if not isinstance(raw, dict) or set(raw) != set(cls.__dataclass_fields__):
            raise ValueError("E1 case keys must match schema exactly")
        if not all(isinstance(raw[k], list) and
                   all(isinstance(s, str) and len(s) <= 300 for s in raw[k])
                   for k in ("background", "hard_forbid")):
            raise ValueError("E1 case arrays must be bounded strings")
        case = cls(**{**raw, "background": tuple(raw["background"]),
                      "hard_forbid": tuple(raw["hard_forbid"])})
        if (not re.fullmatch(r"[a-z][a-z0-9_]{3,50}", case.id)
            or case.category not in CATEGORIES
            or case.relation not in RELATIONS or case.channel not in CHANNELS
            or not isinstance(case.message, str) or not 1 <= len(case.message) <= 400
            or not isinstance(case.evaluator_note, str)
            or not 1 <= len(case.evaluator_note) <= 400
            or type(case.max_chars) is not int or not 80 <= case.max_chars <= 1000
            or len(case.background) > 4 or len(case.hard_forbid) > 8
            or any(not word for word in case.hard_forbid)):
            raise ValueError("Invalid bounded E1 case")
        return case


def _read_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("E1 file must be a JSON object")
    return data


def load_cases(path: Path) -> tuple[Case, ...]:
    data = _read_json(path)
    if data.get("schema") != 1 or not isinstance(data.get("cases"), list):
        raise ValueError("Invalid E1 dataset schema")
    cases = tuple(Case.parse(c) for c in data["cases"])
    if not 20 <= len(cases) <= 300 or len({x.id for x in cases}) != len(cases):
        raise ValueError("Need 20-300 unique E1 cases")
    return cases


def load_responses(path: Path, cases: tuple[Case, ...]) -> tuple[dict[str, str], dict]:
    data = _read_json(path)
    if (data.get("schema") != 1 or data.get("source") not in
            ("handcrafted_demo", "model_capture")
            or not isinstance(data.get("variant"), str)
            or not 1 <= len(data["variant"]) <= 100
            or not isinstance(data.get("responses"), list)):
        raise ValueError("Invalid E1 response bundle")
    allowed = {case.id for case in cases}
    mapping = {}
    for row in data["responses"]:
        if (not isinstance(row, dict) or set(row) != {"case_id", "response"}
                or row["case_id"] not in allowed
                or row["case_id"] in mapping
                or not isinstance(row["response"], str)
                or not 1 <= len(row["response"]) <= 12000):
            raise ValueError("Unknown, duplicated or malformed E1 response")
        mapping[row["case_id"]] = row["response"]
    return mapping, {"source": data["source"], "variant": data["variant"]}


def load_reviews(path: Path | None, cases: tuple[Case, ...]) -> dict[str, dict]:
    if path is None:
        return {}
    data = _read_json(path)
    if data.get("schema") != 1 or not isinstance(data.get("reviews"), list):
        raise ValueError("Invalid E1 review file")
    ids = {case.id for case in cases}
    out = {}
    for row in data["reviews"]:
        if not isinstance(row, dict) or set(row) != {"case_id", "scores", "notes"}:
            raise ValueError("Invalid E1 review row")
        cid, scores, notes = row["case_id"], row["scores"], row["notes"]
        if (cid not in ids or cid in out or
                not isinstance(notes, str) or len(notes) > 1000):
            raise ValueError("Unknown or duplicated review")
        if scores is None:
            continue   # template's unreviewed entry
        if (not isinstance(scores, dict) or set(scores) != set(WEIGHTS)
                or any(type(v) is not int or not 0 <= v <= 5 for v in scores.values())):
            raise ValueError("Scores must be 0-5 integers for all six dimensions")
        out[cid] = {"scores": scores, "notes": notes}
    return out


def audit(cases: tuple[Case, ...], responses: dict[str, str],
          metadata: dict, reviews: dict[str, dict] | None = None) -> dict:
    reviews = reviews or {}
    results = []
    for case in cases:
        reply = responses.get(case.id)
        errors = []
        warnings = []
        if reply is not None:
            # These are *exact* restricted phrases for controlled synthetic
            # situations, not a general-purpose hallucination detector.
            for phrase in case.hard_forbid:
                if phrase.casefold() in reply.casefold():
                    errors.append(f"forbidden_literal:{phrase}")
            if len(reply) > case.max_chars:
                warnings.append("over_case_length_budget")
            if re.match(r"^\s*[（(](?:轻声|微笑|动作|低头|想了想)", reply):
                warnings.append("stage_direction_opening")
        review = reviews.get(case.id) if reply is not None else None
        rating = (round(sum(review["scores"][k] * w for k, w in WEIGHTS.items()) / 5, 2)
                  if review else None)
        results.append({
            "id": case.id, "category": case.category, "relation": case.relation,
            "channel": case.channel, "message": case.message,
            "background": list(case.background), "evaluator_note": case.evaluator_note,
            "response": reply, "hard_failures": errors, "warnings": warnings,
            "review": review, "human_score_100": rating,
            "status": ("missing" if reply is None else
                       "hard_fail" if errors else "awaiting_human_review" if not review else
                       "reviewed"),
        })
    scores = [r["human_score_100"] for r in results if r["human_score_100"] is not None]
    summary = {
        "cases": len(cases),
        "captured": sum(r["response"] is not None for r in results),
        "missing": sum(r["response"] is None for r in results),
        "hard_failures": sum(bool(r["hard_failures"]) for r in results),
        "warnings": sum(len(r["warnings"]) for r in results),
        "human_reviewed": len(scores),
        "human_average_100": round(sum(scores) / len(scores), 2) if scores else None,
    }
    return {
        "schema": 1, "evaluation": "Character Evaluation E1",
        "source": metadata["source"], "variant": metadata["variant"],
        "human_scoring": "manual_only", "summary": summary, "results": results,
        "warning": (
            "Deterministic pass only checks declared test constraints; it does NOT "
            "certify fidelity to the fictional character, factual correctness, "
            "naturalness or suitability for R1. Human review is required."
        ),
    }


def compare_reports(current: dict, baseline: dict) -> dict:
    if [r["id"] for r in current["results"]] != [r["id"] for r in baseline["results"]]:
        raise ValueError("E1 comparison requires identical case IDs and order")
    changes = []
    for now, before in zip(current["results"], baseline["results"]):
        if now["hard_failures"] != before["hard_failures"] or (
            now["human_score_100"] is not None and before["human_score_100"] is not None
            and now["human_score_100"] != before["human_score_100"]
        ):
            changes.append({
                "case_id": now["id"], "baseline_hard": before["hard_failures"],
                "current_hard": now["hard_failures"],
                "baseline_human_score": before["human_score_100"],
                "current_human_score": now["human_score_100"],
            })
    return {"baseline_variant": baseline["variant"], "changed_cases": changes,
            "note": "Human scores are comparable only if both variants were actually reviewed."}


def review_template(cases: tuple[Case, ...]) -> dict:
    return {"schema": 1, "rubric": WEIGHTS,
            "instructions": "Fill all six 0-5 scores, or leave scores:null; never infer scores from demo responses.",
            "reviews": [{"case_id": c.id, "scores": None, "notes": ""} for c in cases]}


def markdown_report(report: dict) -> str:
    summary = report["summary"]
    provenance = ("HANDCRAFTED DEMO — NOT REAL MODEL PERFORMANCE"
                  if report["source"] == "handcrafted_demo" else
                  "MODEL CAPTURE — UNVERIFIED UNTIL MANUAL REVIEW")
    lines = [
        "# Character Evaluation E1 — 报告", "",
        f"**数据来源：{provenance}**", "",
        f"版本标签：\`{report['variant']}\`",
        "评估方法：固定场景 + 明确字符串禁令 + 人工六维评分（可选）。",
        "**通过字符串检查，不等于角色还原度通过。**", "",
        "## 概览", "",
        "| 场景数 | 有回答 | 缺失 | 硬性检查失败 | 提示 | 人工已评 | 人工均分 |",
        "|---:|---:|---:|---:|---:|---:|---:|",
        f"| {summary['cases']} | {summary['captured']} | {summary['missing']} | "
        f"{summary['hard_failures']} | {summary['warnings']} | "
        f"{summary['human_reviewed']} | "
        f"{summary['human_average_100'] if summary['human_average_100'] is not None else '未评'} |", "",
        "## 逐项结果", "",
        "| Case | 场景 | 关系 | 通道 | 状态 | 人工分 |",
        "|---|---|---|---|---|---:|",
    ]
    for row in report["results"]:
        score = row["human_score_100"]
        lines.append(f"| {row['id']} | {row['category']} | {row['relation']} | "
                     f"{row['channel']} | {row['status']} | {score if score is not None else '待评'} |")
    if report.get("comparison"):
        lines.extend(["", "## 与基线比较", "",
                      f"基线：{report['comparison']['baseline_variant']}",
                      f"自动标记变化：{len(report['comparison']['changed_cases'])} 个案例；"
                      "仅有人工复核的维度才能用于角色质量判断。"])
    lines.extend([
        "", "## 必须人工复核的项目", "",
        "- 庄方宜是否有独立判断、适当温度与角色特征。",
        "- 是否无依据假装认识用户、引用不存在的共同经历。",
        "- 武陵与人物关系是否区分官方事实、社区整理与演绎。",
        "- L0/L1/L2/L3 表达能否形成自然差异，且不自动增加工具权限。",
        "- 群聊是否避免泄漏私聊内容，主动发言是否经过宿主授权。",
        "", "JSON 和 HTML 报告包含完整测试文本、回答与告警，"
        "不要提交含有真实用户隐私的模型捕获数据到公开仓库。",
        "",
    ])
    return "\n".join(lines)


def html_report(report: dict) -> str:
    # No external JS/fonts/CDN; full content is escaped, safe to open locally.
    mdsummary = report["summary"]
    blocks = []
    for result in report["results"]:
        answer = result["response"] if result["response"] is not None else "(未提供回答)"
        background = "；".join(result["background"]) or "无"
        problems = "; ".join(result["hard_failures"] + result["warnings"]) or "未命中声明的自动规则"
        blocks.append(
            "<details><summary><strong>" + escape(result["id"]) + "</strong> — "
            + escape(result["category"]) + " / " + escape(result["relation"]) + " / "
            + escape(result["status"]) + "</summary>"
            + "<p><b>输入：</b>" + escape(result["message"]) + "</p>"
            + "<p><b>合成历史：</b>" + escape(background) + "</p>"
            + "<p><b>需要人工观察：</b>" + escape(result["evaluator_note"]) + "</p>"
            + "<p><b>回答：</b></p><pre>" + escape(answer) + "</pre>"
            + "<p><b>自动告警：</b>" + escape(problems) + "</p></details>"
        )
    provenance = ("仅为人工编写演示回答，未请求真实模型！"
                  if report["source"] == "handcrafted_demo" else
                  "模型输出捕获，尚不代表经过人工验证的角色表现。")
    return (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Character Evaluation E1</title>"
        "<style>body{font:16px/1.65 system-ui,sans-serif;margin:2rem auto;"
        "padding:0 1.2rem;max-width:940px;color:#242629;background:#fafafa}"
        "h1{font-size:1.65rem}section,details{background:white;border:1px solid #ddd;"
        "border-radius:10px;padding:1rem;margin:0.7rem 0}summary{cursor:pointer}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f5f7;padding:0.8rem}"
        ".warn{font-weight:600}small{color:#62676c}</style></head><body>"
        "<h1>Character Evaluation E1</h1><section>"
        "<p class='warn'>" + escape(provenance) + "</p><p>版本：" +
        escape(report["variant"]) + "</p><p>场景数 " + str(mdsummary["cases"]) +
        " · 有回答 " + str(mdsummary["captured"]) + " · 缺失 " +
        str(mdsummary["missing"]) + " · 自动硬性失败 " +
        str(mdsummary["hard_failures"]) + " · 人工评阅 " +
        str(mdsummary["human_reviewed"]) + "</p>"
        "<small>自动检查只验证显式配置的字符串禁令；不能证明角色行为自然或知识准确。</small>"
        "</section><h2>逐条审阅</h2>" + "".join(blocks) + "</body></html>"
    )


def write_reports(report: dict, cases: tuple[Case, ...], outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "summary.md").write_text(markdown_report(report), encoding="utf-8")
    (outdir / "details.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (outdir / "report.html").write_text(html_report(report), encoding="utf-8")
    (outdir / "review-template.json").write_text(
        json.dumps(review_template(cases), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
