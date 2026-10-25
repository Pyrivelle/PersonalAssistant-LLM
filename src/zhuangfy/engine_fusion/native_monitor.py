"""Read bounded, local Maisaka planning details for the chat stream."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import parse_qs, urlparse


def monitor_rows(path: Path, after: int, session_id: str = "", limit: int = 50) -> list[tuple[int, str, dict]]:
    if not path.is_file():
        return []
    with closing(sqlite3.connect(path, timeout=1)) as db:
        if session_id:
            rows = db.execute(
                "SELECT event_id,event_type,payload_json FROM maisaka_monitor_events "
                "WHERE event_id>? AND session_id=? ORDER BY event_id LIMIT ?",
                (after, session_id, limit)).fetchall()
        else:
            rows = db.execute(
                "SELECT event_id,event_type,payload_json FROM maisaka_monitor_events "
                "WHERE event_id>? ORDER BY event_id LIMIT ?",
                (after, limit)).fetchall()
    result = []
    for event_id, kind, raw in rows:
        try:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                result.append((int(event_id), str(kind), payload))
        except (TypeError, ValueError):
            continue
    return result


def latest_event_id(path: Path) -> int:
    if not path.is_file():
        return 0
    with closing(sqlite3.connect(path, timeout=1)) as db:
        row = db.execute("SELECT COALESCE(MAX(event_id),0) FROM maisaka_monitor_events").fetchone()
    return int(row[0])


def summary(kind: str, payload: dict) -> tuple[str, str] | None:
    """Never copy raw prompt, messages, model prose, tool arguments or URLs."""
    cycle = payload.get("cycle_id")
    cycle_label = f"第{cycle}轮" if type(cycle) is int and cycle > 0 else "当前轮"
    if kind == "flow.step":
        step = payload.get("step")
        if step == "cycle.started":
            return "阶段", f"{cycle_label} · 开始规划"
        if step == "planner.requested":
            return "阶段", f"{cycle_label} · 正在请求 Planner"
        return None
    if kind != "planner.finalized":
        return None
    request = payload.get("request") or {}
    planner = payload.get("planner") or {}
    final = payload.get("final_state") or {}
    if not all(isinstance(part, dict) for part in (request, planner, final)):
        return None
    actions = [str(call.get("name")) for call in (planner.get("tool_calls") or [])
               if isinstance(call, dict) and call.get("name") in {"reply", "wait", "stop"}]
    action = "、".join(actions) if actions else str(final.get("agent_state") or "未选择")[:30]
    selected = request.get("selected_history_count")
    count = selected if type(selected) is int and selected >= 0 else "未知"
    section_names = {"system_prompt": "角色与规则", "messages": "历史消息",
                     "instant_notice": "本轮提示", "other_tools": "工具说明",
                     "memory": "记忆"}
    sections = []
    for part in request.get("context_sections") or []:
        if not isinstance(part, dict) or part.get("key") not in section_names:
            continue
        chars = part.get("chars")
        if type(chars) is int and chars >= 0:
            sections.append(f"{section_names[part['key']]} {chars}字")
    model = str(planner.get("model_name") or "未知模型")[:90]
    tokens = planner.get("total_tokens")
    token_text = f" · {tokens} Token" if type(tokens) is int and tokens >= 0 else ""
    detail = (f"{cycle_label} · 历史 {count} 条 · 上下文：{', '.join(sections) or '未报告'}"
              f" · {model}{token_text} · 决策：{action}")
    return "规划", detail[:1000]


def _excerpt(value: str, limit: int = 1000) -> str:
    value = value.strip()
    return value if len(value) <= limit else value[:limit] + "\n[内容过长，以上为节选]"


def planner_details(payload: dict, prompt_root: Path) -> list[tuple[str, str]]:
    """Project selected input and output, never the large system prompt.

    The monitor stores a URL to a prompt artifact. Resolve only JSON files
    beneath the pinned local planner directory; monitor payloads are data.
    """
    planner = payload.get("planner")
    if not isinstance(planner, dict):
        return []
    cycle = payload.get("cycle_id")
    prefix = f"第{cycle}轮 · " if type(cycle) is int and cycle > 0 else "当前轮 · "
    result: list[tuple[str, str]] = []
    has_reasoning = False
    artifact_loaded = False
    uri = planner.get("prompt_html_uri")
    if isinstance(uri, str):
        parsed = urlparse(uri)
        names = parse_qs(parsed.query).get("path", [])
        if (not parsed.scheme and not parsed.netloc and len(names) == 1
                and names[0].startswith("planner/")):
            root = prompt_root.resolve()
            candidate = (root / names[0]).resolve()
            if candidate.is_relative_to(root / "planner") and candidate.suffix == ".json":
                try:
                    if candidate.stat().st_size <= 256_000:
                        artifact = json.loads(candidate.read_text(encoding="utf-8"))
                        if isinstance(artifact, dict):
                            artifact_loaded = True
                            items = artifact.get("request_items") or []
                            if isinstance(items, list):
                                for item in items[-8:]:
                                    if not isinstance(item, dict) or item.get("item_type") == "SystemMessageItem":
                                        continue
                                    parts = item.get("parts") or []
                                    if isinstance(parts, list):
                                        body = "\n".join(str(p.get("text")) for p in parts
                                                         if isinstance(p, dict) and isinstance(p.get("text"), str))
                                        if body.strip():
                                            result.append(("输入历史", prefix + _excerpt(body)))
                            outputs = artifact.get("output_items") or []
                            if isinstance(outputs, list):
                                for item in outputs:
                                    if isinstance(item, dict) and item.get("item_type") == "ReasoningItem":
                                        parts = item.get("text_parts") or []
                                        if isinstance(parts, list):
                                            body = "\n".join(p for p in parts if isinstance(p, str))
                                            if body.strip():
                                                has_reasoning = True
                                                result.append(("模型推理", prefix + _excerpt(body)))
                except (OSError, ValueError, UnicodeError):
                    pass
    body = planner.get("content")
    if not has_reasoning:
        reason = ("本轮模型未返回独立的推理文本；下方正文和工具调用是实际返回内容。"
                  if artifact_loaded else "本轮未取得可展示的推理记录；下方正文和工具调用来自 Maisaka 监控。")
        result.append(("模型推理", prefix + reason))
    if isinstance(body, str) and body.strip():
        result.append(("规划正文", prefix + _excerpt(body)))
    for tool in payload.get("tools") or []:
        if not isinstance(tool, dict) or tool.get("tool_name") not in {"reply", "wait", "stop"}:
            continue
        name = tool["tool_name"]
        arguments = tool.get("tool_args") or {}
        argument_text = ""
        if name == "wait" and isinstance(arguments, dict) and type(arguments.get("seconds")) is int:
            argument_text = f"{arguments['seconds']} 秒"
        source = str(tool.get("tool_call_source_label") or "").strip()[:40]
        result.append(("工具执行", prefix + f"调用 {name}({argument_text})"
                       + (f"（{source}）" if source else "") + "；"
                       + ("成功" if tool.get("success") is True else "失败")
                       + f"；{_excerpt(str(tool.get('summary') or ''), 700)}"))
    final = payload.get("final_state")
    if isinstance(final, dict):
        state = str(final.get("agent_state") or "未报告")[:40]
        explanation = str(final.get("end_detail") or "").strip().rstrip("。；; ")
        conflict = ("；模型同时给出正文和 stop，本轮正文未作为回复发送"
                    if state == "stop" and isinstance(body, str) and body.strip() else "")
        result.append(("行为结论", prefix + f"状态：{state}"
                       + (f"；{_excerpt(explanation, 700)}" if explanation else "")
                       + conflict))
    return result
