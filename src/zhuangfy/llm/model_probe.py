"""One explicit, bounded model-management test request.

No persona, conversation history, memory, or fallback is involved: the result
belongs to exactly the model selected in the management UI.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from .model_registry import ProviderConnection, RegisteredModel
from .providers import LlmSettings, ModelRequestError, chat
from .routes import load_route_key

PROBE_PROMPT = "这是连接测试。请只回复：连接正常"


@dataclass(frozen=True)
class ModelProbeResult:
    answer: str
    elapsed_seconds: float


def probe_model(connection: ProviderConnection, model: RegisteredModel,
                *, timeout: float = 45.0) -> ModelProbeResult:
    connection = connection.validated()
    model = model.validated()
    if model.provider_id != connection.id:
        raise ValueError("模型与供应商不匹配")
    key = load_route_key(model.id, connection.kind, connection.base_url)
    if not key and connection.kind != "custom":
        raise ValueError("这个供应商尚未配置 API Key；请先在“编辑厂商”中保存密钥")
    settings = LlmSettings(
        provider=connection.kind, base_url=connection.base_url,
        model=model.model_id,
        system_prompt="这是一次模型连通性测试。请直接回答用户的测试语句。",
    ).validated()
    started = time.monotonic()
    answer = chat(settings, key, [], PROBE_PROMPT, timeout=timeout,
                  stage="模型测试", thinking=model.thinking)
    return ModelProbeResult(answer, time.monotonic() - started)


def describe_probe_error(error: Exception) -> str:
    """A useful diagnosis without response bodies, URLs, or credentials."""
    if isinstance(error, ModelRequestError):
        if error.status == 401:
            return "HTTP 401：密钥无效、已过期，或与当前供应商不匹配。请在“编辑厂商”中更新 API Key。"
        if error.status == 403:
            return "HTTP 403：当前密钥没有使用这个模型的权限。"
        if error.status == 404:
            return "HTTP 404：供应商找不到该模型。请核对 API 模型 ID。"
        if error.status == 429:
            return "HTTP 429：请求受限；请稍后再测，并检查额度或并发限制。"
        if error.status == 402:
            return "HTTP 402：供应商账户余额或额度不足。"
        if error.kind == "timeout":
            return "请求超时：模型在 45 秒内没有完成回复；请检查服务状态，或稍后重试。"
        if error.kind == "network":
            return "网络连接失败：无法连接模型供应商，请检查网络及本地服务状态。"
        if error.kind == "empty":
            return "模型请求成功，但没有返回可显示的文字。"
        if error.kind == "protocol":
            return "模型返回格式不符合当前 chat/completions 接口要求。"
        if error.status is not None:
            return f"HTTP {error.status}：模型请求失败；请检查模型 ID、账户状态和服务状态。"
        return "模型请求失败；请查看运行监控中的模型测试记录。"
    if isinstance(error, ValueError):
        return str(error)
    return "模型测试未完成；请查看运行监控中的模型测试记录。"
