"""Provider connections, reusable models, and their task assignments.

The registry is local metadata. Secrets remain in .env.local; the existing
model_routes.json remains the runtime route format for chat/QQ/voice workers.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from .providers import ENV_FILE, ROOT, LlmSettings, load_settings
from zhuangfy.paths import Paths
from .routes import (IDENTIFIER, MAX_ROUTES, STAGES, ModelCandidate,
                     ModelRoutes, StagePolicy, load_routes, save_routes)

REGISTRY_FILE = ROOT / "model_registry.json"
MEMORY_RUNTIME_FILE = Paths.current().project_data_root / "memory_runtime.json"
MAX_PROVIDERS = 12
MAX_MODELS = 40


@dataclass(frozen=True)
class ProviderConnection:
    id: str
    name: str
    kind: str
    base_url: str

    def validated(self) -> "ProviderConnection":
        if not IDENTIFIER.fullmatch(self.id):
            raise ValueError("供应商标识符只能使用小写字母、数字和下划线")
        if not self.name.strip() or len(self.name) > 60:
            raise ValueError("请填写供应商名称")
        checked = LlmSettings(self.kind, self.base_url, "validation-model").validated()
        return ProviderConnection(self.id, self.name.strip(), self.kind, checked.base_url)


@dataclass(frozen=True)
class RegisteredModel:
    id: str                 # Stable, unique local identifier used by routes.
    name: str               # Display name in the UI.
    provider_id: str
    model_id: str           # Actual ID sent to the API.
    enabled: bool = True
    thinking: str = "auto"  # auto preserves the provider's current behavior.

    def validated(self) -> "RegisteredModel":
        if not IDENTIFIER.fullmatch(self.id):
            raise ValueError("模型标识符只能使用小写字母、数字和下划线，最多 40 字符")
        if not self.name.strip() or len(self.name) > 80:
            raise ValueError("请填写模型名称")
        if not IDENTIFIER.fullmatch(self.provider_id):
            raise ValueError("模型引用了无效供应商")
        if not self.model_id.strip() or len(self.model_id) > 200:
            raise ValueError("请填写 API 模型 ID")
        if type(self.enabled) is not bool:
            raise ValueError("模型启用状态无效")
        if self.thinking not in {"auto", "enabled", "disabled"}:
            raise ValueError("模型思考模式无效")
        return RegisteredModel(self.id, self.name.strip(), self.provider_id,
                               self.model_id.strip(), self.enabled, self.thinking)


@dataclass(frozen=True)
class ModelRegistry:
    providers: tuple[ProviderConnection, ...]
    models: tuple[RegisteredModel, ...]
    planner: tuple[str, ...]
    reply: tuple[str, ...]
    vision: tuple[str, ...] = ()
    memory: str = ""

    def validated(self) -> "ModelRegistry":
        if len(self.providers) > MAX_PROVIDERS or len(self.models) > MAX_MODELS:
            raise ValueError("供应商或模型数量超过上限")
        providers = {p.id: p.validated() for p in self.providers}
        models = {m.id: m.validated() for m in self.models}
        if len(providers) != len(self.providers) or len(models) != len(self.models):
            raise ValueError("供应商或模型标识符不能重复")
        for model in models.values():
            if model.provider_id not in providers:
                raise ValueError(f"模型 {model.id} 引用了不存在的供应商")
            provider = providers[model.provider_id]
            LlmSettings(provider.kind, provider.base_url, model.model_id).validated()
            if model.thinking != "auto" and provider.kind not in {"deepseek", "siliconflow"}:
                raise ValueError(f"{model.id} 的供应商尚不支持手动思考开关")
        for stage in STAGES:
            ids = getattr(self, stage)
            if len(ids) > MAX_ROUTES or len(set(ids)) != len(ids):
                raise ValueError(f"{stage} 最多分配 {MAX_ROUTES} 个不同模型")
            if any(name not in models or not models[name].enabled for name in ids):
                raise ValueError(f"{stage} 引用了不存在或未启用的模型")
        if not self.planner or not self.reply:
            raise ValueError("Planner 和回复至少各需要一个启用模型")
        if self.memory and (self.memory not in models or not models[self.memory].enabled):
            raise ValueError("记忆整理引用了不存在或未启用的模型")
        return self

    def materialize(self, previous: ModelRoutes) -> ModelRoutes:
        self.validated()
        providers = {item.id: item for item in self.providers}
        models = {item.id: item for item in self.models}
        policies = {}
        for stage in STAGES:
            old = previous.for_stage(stage)
            candidates = []
            for name in getattr(self, stage):
                model = models[name]
                provider = providers[model.provider_id]
                candidates.append(ModelCandidate(model.id, provider.kind,
                                                 provider.base_url, model.model_id,
                                                 thinking=model.thinking))
            policies[stage] = StagePolicy(
                tuple(candidates), old.retries, old.retry_delay,
                old.timeout, old.total_timeout)
        return ModelRoutes(**policies).validated()


def _initial_registry() -> ModelRegistry:
    """Import existing routes without changing either legacy settings file."""
    routes = load_routes()
    providers: list[ProviderConnection] = []
    models: list[RegisteredModel] = []
    by_endpoint: dict[tuple[str, str], str] = {}
    assignments: dict[str, tuple[str, ...]] = {}

    def add_model(identifier: str, kind: str, url: str, model_id: str,
                  enabled: bool = True, thinking: str = "auto") -> str:
        endpoint = (kind, url)
        provider_id = by_endpoint.get(endpoint)
        if provider_id is None:
            provider_id = f"provider_{len(providers) + 1}"
            from .providers import PROVIDERS
            providers.append(ProviderConnection(provider_id, PROVIDERS.get(kind, kind),
                                                kind, url))
            by_endpoint[endpoint] = provider_id
        existing = next((m for m in models if m.id == identifier), None)
        if existing:
            if (existing.provider_id, existing.model_id, existing.enabled, existing.thinking) != (
                    provider_id, model_id, enabled, thinking):
                raise ValueError("同一模型标识符在旧路由中指向不同模型")
            return identifier
        models.append(RegisteredModel(identifier, model_id.rsplit("/", 1)[-1],
                                      provider_id, model_id,
                                      enabled, thinking))
        return identifier

    for stage in STAGES:
        names = []
        for candidate in routes.for_stage(stage).candidates:
            identifier = add_model(candidate.id, candidate.provider,
                                   candidate.base_url, candidate.model,
                                   candidate.enabled, candidate.thinking)
            if candidate.enabled:
                names.append(identifier)
        assignments[stage] = tuple(names)
    if not assignments["planner"] or not assignments["reply"]:
        fallback = load_settings()
        identifier = "default_chat"
        add_model(identifier, fallback.provider, fallback.base_url, fallback.model)
        for stage in ("planner", "reply"):
            if not assignments[stage]:
                assignments[stage] = (identifier,)
    memory = ""
    try:
        runtime = json.loads(MEMORY_RUNTIME_FILE.read_text(encoding="utf-8"))
        match = next((m for m in models if m.model_id == runtime.get("model") and
                      next(p for p in providers if p.id == m.provider_id).base_url ==
                      runtime.get("base_url")), None)
        if match:
            memory = match.id
        elif runtime.get("model") and runtime.get("base_url"):
            identifier = "memory_model"
            memory = add_model(identifier, runtime.get("provider", "siliconflow"),
                               runtime["base_url"], runtime["model"])
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return ModelRegistry(tuple(providers), tuple(models), assignments["planner"],
                         assignments["reply"], assignments["vision"], memory).validated()


def load_registry(path: Path | None = None) -> ModelRegistry:
    path = path or REGISTRY_FILE
    if not path.is_file():
        return _initial_registry()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ValueError("模型管理配置版本无效")
    return ModelRegistry(
        tuple(ProviderConnection(**p) for p in raw["providers"]),
        tuple(RegisteredModel(**m) for m in raw["models"]),
        tuple(raw.get("planner", ())), tuple(raw.get("reply", ())),
        tuple(raw.get("vision", ())), str(raw.get("memory", "")),
    ).validated()


def save_registry(value: ModelRegistry, path: Path | None = None) -> None:
    path = path or REGISTRY_FILE
    value.validated()
    raw = {"version": 1, "providers": [asdict(p) for p in value.providers],
           "models": [asdict(m) for m in value.models],
           **{stage: list(getattr(value, stage)) for stage in STAGES},
           "memory": value.memory}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def provider_key_name(identifier: str) -> str:
    if not IDENTIFIER.fullmatch(identifier):
        raise ValueError("无效供应商标识符")
    return "ZHUANGFY_PROVIDER_" + identifier.upper() + "_API_KEY"


def _secret_target(kind: str, base_url: str) -> str:
    return hashlib.sha256((kind + "\0" + base_url.rstrip("/")).encode("utf-8")).hexdigest()[:32]


def load_provider_key(identifier: str, kind: str, base_url: str) -> str:
    name = provider_key_name(identifier)
    values = {}
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator:
                values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        return ""
    return values.get(name, "") if values.get(name + "_TARGET") == _secret_target(kind, base_url) else ""


def save_provider_key(identifier: str, secret: str, kind: str, base_url: str) -> None:
    name = provider_key_name(identifier)
    value = secret.strip()
    if not value or "\n" in value or "\r" in value:
        raise ValueError("API Key 不能为空且不能包含换行")
    target = _secret_target(kind, base_url)
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    lines = [line for line in lines if line.partition("=")[0].strip() not in
             {name, name + "_TARGET"}]
    lines.extend((name + "=" + value, name + "_TARGET=" + target))
    temporary = ENV_FILE.with_suffix(".tmp")
    try:
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(temporary, ENV_FILE)
    finally:
        temporary.unlink(missing_ok=True)


def save_assignments(value: ModelRegistry) -> None:
    """Persist catalog and runtime routes; remember memory choice for next service start."""
    routes = value.materialize(load_routes())
    save_registry(value)
    save_routes(routes)
    if value.memory and MEMORY_RUNTIME_FILE.is_file():
        runtime = json.loads(MEMORY_RUNTIME_FILE.read_text(encoding="utf-8"))
        model = next(m for m in value.models if m.id == value.memory)
        provider = next(p for p in value.providers if p.id == model.provider_id)
        runtime.update(profile_id=model.id, provider_id=provider.id,
                       provider=provider.kind, base_url=provider.base_url,
                       model=model.model_id)
        temporary = MEMORY_RUNTIME_FILE.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps(runtime, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")
            os.replace(temporary, MEMORY_RUNTIME_FILE)
        finally:
            temporary.unlink(missing_ok=True)
