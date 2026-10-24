"""Reusable numbered API definitions for conversation and memory stages."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .providers import ROOT, LlmSettings, load_settings
from .routes import ModelCandidate, ModelRoutes, StagePolicy, STAGES, load_routes


PROFILES_FILE = ROOT / "api_profiles.json"
MEMORY_RUNTIME_FILE = ROOT.parent / "data" / "memory_runtime.json"
MAX_PROFILES = 12


@dataclass(frozen=True)
class ApiProfile:
    id: str
    provider: str
    base_url: str
    model: str
    enabled: bool = True
    legacy_id: str = ""

    def validated(self) -> "ApiProfile":
        if not self.id.startswith("api_") or not self.id[4:].isdigit():
            raise ValueError("API 编号必须采用 api_1 等格式")
        if type(self.enabled) is not bool:
            raise ValueError("API 启用状态无效")
        LlmSettings(self.provider, self.base_url, self.model).validated()
        return self

    def candidate(self) -> ModelCandidate:
        return ModelCandidate(self.id, self.provider, self.base_url,
                              self.model, self.enabled).validated()


@dataclass(frozen=True)
class ApiProfiles:
    profiles: tuple[ApiProfile, ...]
    planner: tuple[str, ...]
    reply: tuple[str, ...]
    vision: tuple[str, ...]
    memory: str = ""

    def validated(self) -> "ApiProfiles":
        if not 1 <= len(self.profiles) <= MAX_PROFILES:
            raise ValueError("请配置 1–12 个 API")
        by_id = {item.id: item.validated() for item in self.profiles}
        if len(by_id) != len(self.profiles):
            raise ValueError("API 编号不能重复")
        for stage in STAGES:
            ids = getattr(self, stage)
            if len(ids) > 5 or len(ids) != len(set(ids)) or any(i not in by_id for i in ids):
                raise ValueError(f"{stage} 阶段引用了无效或重复的 API")
        if not self.planner or not self.reply:
            raise ValueError("思考和回复阶段都需要至少一个 API")
        if not any(by_id[name].enabled for name in self.planner):
            raise ValueError("思考阶段需要至少一个启用的 API")
        if not any(by_id[name].enabled for name in self.reply):
            raise ValueError("回复阶段需要至少一个启用的 API")
        if self.memory and self.memory not in by_id:
            raise ValueError("记忆整理 API 编号不存在")
        if self.memory and not by_id[self.memory].enabled:
            raise ValueError("记忆整理 API 必须启用")
        return self

    def materialize(self, previous: ModelRoutes) -> ModelRoutes:
        self.validated()
        by_id = {item.id: item for item in self.profiles}
        policies = {}
        for stage in STAGES:
            old = previous.for_stage(stage)
            policies[stage] = StagePolicy(
                tuple(by_id[name].candidate() for name in getattr(self, stage)),
                retries=old.retries, retry_delay=old.retry_delay,
                timeout=old.timeout, total_timeout=old.total_timeout)
        return ModelRoutes(**policies).validated()


def load_profiles(path: Path = PROFILES_FILE) -> ApiProfiles:
    if path.is_file():
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("version") != 1:
            raise ValueError("API 编号配置版本无效")
        return ApiProfiles(
            tuple(ApiProfile(**item) for item in raw["profiles"]),
            tuple(raw.get("planner", [])), tuple(raw.get("reply", [])),
            tuple(raw.get("vision", [])), str(raw.get("memory", "")),
        ).validated()
    # First opening presents the existing stage routes without changing them.
    routes = load_routes()
    profiles: list[ApiProfile] = []
    assigned: dict[str, tuple[str, ...]] = {}
    for stage in STAGES:
        ids = []
        for candidate in routes.for_stage(stage).candidates:
            ident = f"api_{len(profiles) + 1}"
            profiles.append(ApiProfile(ident, candidate.provider, candidate.base_url,
                                       candidate.model, candidate.enabled, candidate.id))
            ids.append(ident)
        assigned[stage] = tuple(ids)
    if not assigned["planner"] or not assigned["reply"]:
        fallback = load_settings()
        ident = f"api_{len(profiles) + 1}"
        profiles.append(ApiProfile(ident, fallback.provider, fallback.base_url,
                                   fallback.model))
        for stage in ("planner", "reply"):
            if not assigned[stage]:
                assigned[stage] = (ident,)
    memory = ""
    try:
        runtime = json.loads(MEMORY_RUNTIME_FILE.read_text(encoding="utf-8"))
        match = next((item for item in profiles if item.base_url == runtime.get("base_url")
                      and item.model == runtime.get("model")), None)
        if match:
            memory = match.id
        elif len(profiles) < MAX_PROFILES and runtime.get("model"):
            memory = f"api_{len(profiles) + 1}"
            profiles.append(ApiProfile(memory, runtime.get("provider", "siliconflow"), runtime["base_url"],
                                       runtime["model"]))
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return ApiProfiles(tuple(profiles), assigned["planner"], assigned["reply"],
                       assigned["vision"], memory).validated()


def save_profiles(value: ApiProfiles, path: Path = PROFILES_FILE) -> None:
    value.validated()
    raw = {"version": 1,
           "profiles": [item.__dict__ for item in value.profiles],
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
