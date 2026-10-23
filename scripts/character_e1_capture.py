"""E1.1 opt-in real-model capture: failure isolation, resume, provenance.

This entrypoint evaluates synthetic inputs only; it cannot send QQ messages,
run tools, curate memories, or modify the production model route settings.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "SenseVoice"))

from zhuangfy.evaluation.character_e1 import load_cases
from zhuangfy.persona.character_core_v2 import character_behavior
from zhuangfy.persona.knowledge_v1 import retrieve_character_knowledge, knowledge_context

CLOUD_PROVIDERS = ("siliconflow", "deepseek", "kimi_cn", "kimi_global", "hachimi")


def _persist(path: Path, output: dict) -> None:
    """Save each case result atomically, including an all-failed run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    try:
        temp.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def _failure_code(error: Exception) -> str:
    """Never store provider exception bodies (which might contain credentials)."""
    messages = [reason for _, reason in getattr(error, "attempts", ()) if isinstance(reason, str)]
    if any("超时" in reason or "时限" in reason for reason in messages):
        return "timeout"
    if any("网络" in reason for reason in messages):
        return "network"
    if any("429" in reason for reason in messages):
        return "rate_limited"
    if any("401" in reason or "403" in reason for reason in messages):
        return "auth"
    return "service_unavailable"


def _load_resume(path: Path, expected: dict, allowed_ids: set[str]) -> list[dict]:
    """Refuse old captures with different model/persona/dataset/fallback settings."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or data.get("schema") != 1
            or data.get("source") != "model_capture"
            or data.get("variant") != expected["variant"]):
        raise ValueError("Resume file has incompatible E1 schema/variant")
    previous = data.get("capture_info")
    current = expected["capture_info"]
    if not isinstance(previous, dict):
        raise ValueError("Resume capture_info missing")
    for field in ("provider", "configured_model", "persona_sha256",
                  "dataset_sha256", "backup_provider", "backup_model"):
        if previous.get(field, "") != current.get(field, ""):
            raise ValueError(f"Resume blocked: changed capture setting {field}")
    rows, seen = [], set()
    for row in data.get("responses", []):
        if (not isinstance(row, dict) or not isinstance(row.get("case_id"), str)
                or row["case_id"] not in allowed_ids or row["case_id"] in seen
                or not isinstance(row.get("response"), str)
                or not 1 <= len(row["response"]) <= 12000):
            raise ValueError("Invalid, duplicate or out-of-scope resumed response")
        origin = row.get("origin", "primary")
        if origin not in ("primary", "backup"):
            raise ValueError("Invalid resumed model origin")
        if origin == "backup" and not current.get("backup_provider"):
            raise ValueError("Cannot resume backup output without backup configuration")
        seen.add(row["case_id"])
        rows.append({"case_id": row["case_id"], "response": row["response"],
                     "origin": origin,
                     "provider": row.get("provider", current["provider"] if origin == "primary"
                                        else current["backup_provider"]),
                     "model": row.get("model", current["configured_model"] if origin == "primary"
                                     else current["backup_model"])})
    return rows


def capture(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="E1.1 real-model synthetic quality capture")
    parser.add_argument("--variant", choices=("v1", "ccv2", "ccv2_ckv1"), required=True)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--cases", type=Path,
                        default=ROOT / "resources" / "evaluation" / "character_e1_cases.json")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-cases", type=int, default=300)
    parser.add_argument("--reply-timeout", type=int, default=75,
                        help="E1-only per request timeout in seconds (30..120)")
    parser.add_argument("--resume-from", type=Path,
                        help="Previous model-capture.json; reuse only matching successful rows")
    parser.add_argument("--cloud-provider", choices=CLOUD_PROVIDERS)
    parser.add_argument("--cloud-model")
    parser.add_argument("--backup-provider", choices=CLOUD_PROVIDERS)
    parser.add_argument("--backup-model")
    args = parser.parse_args(argv)
    if not args.allow_network:
        parser.error("Model capture requires explicit --allow-network")
    if type(args.max_cases) is not int or not 1 <= args.max_cases <= 300:
        parser.error("--max-cases must be in 1..300")
    if not 30 <= args.reply_timeout <= 120:
        parser.error("--reply-timeout must be in 30..120 seconds")
    if args.out.resolve().is_relative_to((ROOT / "resources").resolve()):
        parser.error("Do not overwrite versioned evaluation fixtures")
    if bool(args.cloud_provider) != bool(args.cloud_model):
        parser.error("--cloud-provider and --cloud-model are both required")
    if bool(args.backup_provider) != bool(args.backup_model):
        parser.error("--backup-provider and --backup-model must be paired")
    if args.backup_provider and not args.cloud_provider:
        parser.error("Backup requires a primary --cloud-provider")
    for name, value in (("cloud model", args.cloud_model), ("backup model", args.backup_model)):
        if value is not None and (not value.strip() or len(value) > 128
                                  or any(ord(c) < 32 for c in value)):
            parser.error(f"Invalid {name} ID")

    # Import after no-network checks: offline CI cannot call external models.
    from llm import LlmSettings, PROVIDER_BASE_URLS, load_key, load_settings
    from model_routes import invoke_stage
    from fangyi_skill import guidance as fangyi_guidance
    from zhuangfy.persona.packs import ZHUANG_FANGYI_V1
    from zhuangfy.llm.routes import ModelRoutes, ModelRoutesFailed, StagePolicy

    if args.cloud_provider:
        key = os.environ.get("ZHUANGFY_E1_API_KEY", "").strip()
        if not key or "\n" in key or "\r" in key:
            parser.error("Missing/invalid ZHUANGFY_E1_API_KEY Actions Secret")
        settings = LlmSettings(
            provider=args.cloud_provider, base_url=PROVIDER_BASE_URLS[args.cloud_provider],
            model=args.cloud_model, system_prompt=ZHUANG_FANGYI_V1.prompt(),
            memory_enabled=False).validated()
        # Each model attempt may have a longer evaluation-only timeout.
        # The production QQ reply settings are untouched.
        limit = min(240, 2 * args.reply_timeout + 12)
        route_policy = StagePolicy(retries=1, timeout=args.reply_timeout,
                                   total_timeout=limit)
        fixed_routes = ModelRoutes(reply=route_policy)
    else:
        settings, fixed_routes = load_settings(), None
        key = load_key(settings.provider)

    backup = None
    if args.backup_provider:
        backup_key = os.environ.get("ZHUANGFY_E1_BACKUP_API_KEY", "").strip()
        if not backup_key or "\n" in backup_key or "\r" in backup_key:
            parser.error("Missing/invalid ZHUANGFY_E1_BACKUP_API_KEY Actions Secret")
        backup = (LlmSettings(
            provider=args.backup_provider,
            base_url=PROVIDER_BASE_URLS[args.backup_provider],
            model=args.backup_model, system_prompt=settings.system_prompt,
            memory_enabled=False).validated(), backup_key)

    cases = load_cases(args.cases)[:args.max_cases]
    info = {
        "configured_model": settings.model, "provider": settings.provider,
        "persona_sha256": hashlib.sha256(settings.system_prompt.encode("utf-8")).hexdigest(),
        "dataset_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
        "selected_cases": str(len(cases)),
        "reply_timeout_seconds": str(args.reply_timeout),
        "backup_provider": backup[0].provider if backup else "",
        "backup_model": backup[0].model if backup else "",
        "note": ("E1.1 real capture; backup outputs must be evaluated separately"
                 if backup else "E1.1 real capture, primary only"),
    }
    output = {
        "schema": 1, "source": "model_capture", "variant": args.variant,
        "capture_info": info, "responses": [], "failures": [],
    }
    if args.resume_from:
        output["responses"] = _load_resume(
            args.resume_from, output, {case.id for case in cases})
    seen = {row["case_id"] for row in output["responses"]}
    _persist(args.out, output)
    print(f"E1.1 start: {len(seen)} responses restored; {len(cases) - len(seen)} pending", flush=True)

    for idx, case in enumerate(cases, 1):
        if case.id in seen:
            print(f"[{idx}/{len(cases)}] resume: skip {case.id}", flush=True)
            continue
        context = ("以下是专门编造的离线 E1 测试情境，而非现实用户个人资料；"
                   "不要把它当作已经真实发生的共同记忆。\n"
                   f"本轮模拟熟络级别：{case.relation}；沟通通道：{case.channel}。")
        if case.background:
            context += "\n明确的合成上下文：\n" + "\n".join(case.background)
        if args.variant == "v1":
            prompt = fangyi_guidance(settings.system_prompt, case.message)
        else:
            channel = case.channel if case.channel in ("qq_private", "qq_group", "initiative") else "chat"
            prompt = character_behavior(
                settings.system_prompt, case.message, channel=channel).guidance
        if args.variant == "ccv2_ckv1":
            matches = retrieve_character_knowledge(
                settings.system_prompt, case.message, enabled=True)
            extra = knowledge_context(matches) if matches else ""
            if extra and len(context) + len(extra) < 2500:
                context += "\n" + extra

        error_codes = []
        reply = None
        origin = None
        candidates = [("primary", settings, key)]
        # A backup may be attempted only on temporary failures, not invalid keys.
        for origin_name, active_settings, active_key in candidates:
            try:
                reply = invoke_stage(
                    "reply", active_settings, active_key, [], case.message,
                    routes=fixed_routes, emotion_context=prompt, context_notes=context,
                    trace_id=f"e1-{args.variant}-{case.id}-{origin_name}")
                if not isinstance(reply, str) or not reply.strip():
                    raise ValueError("Empty reply")
                origin = origin_name
                break
            except ModelRoutesFailed as error:
                code = _failure_code(error)
                error_codes.append(code)
                if origin_name == "primary" and backup and code in (
                    "timeout", "network", "rate_limited"):
                    candidates.append(("backup", backup[0], backup[1]))
            except (ValueError, TypeError):
                error_codes.append("invalid_reply")
        if reply is not None and origin is not None:
            active = settings if origin == "primary" else backup[0]
            output["responses"].append({
                "case_id": case.id, "response": reply, "origin": origin,
                "provider": active.provider, "model": active.model,
            })
            print(f"[{idx}/{len(cases)}] captured {case.id} [{origin}]", flush=True)
        else:
            output["failures"].append({
                "case_id": case.id,
                "reason": error_codes[-1] if error_codes else "service_unavailable",
                "attempted": "primary,backup" if len(error_codes) > 1 else "primary",
            })
            print(f"[{idx}/{len(cases)}] failed {case.id}: {output['failures'][-1]['reason']}",
                  flush=True)
        _persist(args.out, output)

    if output["failures"]:
        print(f"E1 incomplete: {len(output['failures'])} failed API cases; see report",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(capture())
