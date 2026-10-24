"""Versioned, opt-in, host-owned skill discovery and activation.

Discovery is DATA ONLY; importing executable Python happens exclusively for
explicitly enabled and trusted skill directories. This is an extension system,
NOT a sandbox. Never install or enable unknown third-party Python code.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import importlib.util
import json
from pathlib import Path
import re
from typing import Any

from .runtime import AuthorizedToolRegistry, ToolBinding

_SKILL_ID = re.compile(r"[a-z][a-z0-9_]{1,39}\Z")
_TOOL_NAME = re.compile(r"[a-z][a-z0-9_]{1,39}\.[a-z][a-z0-9_]{1,50}\Z")
_ALLOWED_SCOPES = frozenset({"private", "group"})
_MANIFEST_LIMIT = 16_384
_CONFIG_LIMIT = 65_536


@dataclass(frozen=True, slots=True)
class SkillManifest:
    id: str
    name: str
    version: str
    description: str
    root: Path
    entry: str = "skill.py"
    api_version: int = 1
    core_tool: bool = False
    keywords: tuple[str, ...] = ()

    @classmethod
    def read(cls, directory: Path) -> "SkillManifest":
        if directory.is_symlink() or not directory.is_dir() or not _SKILL_ID.fullmatch(directory.name):
            raise ValueError("invalid skill directory")
        if directory.name in {"memory", "task_memory"}:
            raise ValueError("host-owned memory tool namespace is reserved")
        path = directory / "skill.json"
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _MANIFEST_LIMIT:
            raise ValueError("missing or oversized skill.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("manifest must be a JSON object")
        if data.get("id") != directory.name or not _SKILL_ID.fullmatch(directory.name):
            raise ValueError("skill ID must match directory")
        entry = data.get("entry", "skill.py")
        if entry != "skill.py":
            raise ValueError("only local skill.py entry is supported")
        source = directory / entry
        if source.is_symlink() or not source.is_file():
            raise ValueError("skill.py must be an ordinary file")
        version = data.get("version")
        if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
            raise ValueError("version must use MAJOR.MINOR.PATCH")
        name = data.get("name")
        description = data.get("description", "")
        if not isinstance(name, str) or not name.strip() or len(name) > 70:
            raise ValueError("invalid skill name")
        if not isinstance(description, str) or len(description) > 250:
            raise ValueError("invalid skill description")
        if data.get("api_version", 1) != 1:
            raise ValueError("unsupported skill API version")
        core_tool = data.get("core_tool", False)
        keywords = data.get("keywords", [])
        if type(core_tool) is not bool:
            raise ValueError("core_tool must be boolean")
        if (not isinstance(keywords, list) or len(keywords) > 20 or
                any(not isinstance(x, str) or not 2 <= len(x) <= 48
                    or any(ch in x for ch in "\n\r\t") for x in keywords)):
            raise ValueError("invalid skill discovery keywords")
        return cls(directory.name, name, version, description, directory,
                   entry, 1, core_tool, tuple(keywords))


@dataclass(frozen=True, slots=True)
class SkillPolicy:
    enabled: bool = False
    scopes: frozenset[str] = frozenset({"private"})
    allow_mutation: bool = False

    @classmethod
    def parse(cls, raw: object) -> "SkillPolicy":
        if not isinstance(raw, dict) or set(raw) - {"enabled", "scopes", "allow_mutation"}:
            raise ValueError("invalid skill policy")
        enabled = raw.get("enabled", False)
        mutation = raw.get("allow_mutation", False)
        scopes = raw.get("scopes", ["private"])
        if type(enabled) is not bool or type(mutation) is not bool:
            raise ValueError("skill flags must be boolean")
        if not isinstance(scopes, list) or not scopes or any(
            not isinstance(v, str) for v in scopes
        ) or not set(scopes).issubset(_ALLOWED_SCOPES):
            raise ValueError("invalid skill scope")
        return cls(enabled, frozenset(scopes), mutation)


@dataclass(frozen=True, slots=True)
class SkillContext:
    """Minimal, secret-free context supplied to trusted skill setup code."""
    skill_id: str
    version: str


@dataclass(frozen=True, slots=True)
class SkillStatus:
    id: str
    name: str
    version: str
    enabled: bool
    loaded: bool
    tools: tuple[str, ...] = ()
    error: str = ""


def load_skill_policies(path: Path) -> dict[str, SkillPolicy]:
    """No config means no enabled skills; never silently enable directories."""
    if not path.is_file():
        return {}
    if path.is_symlink() or path.stat().st_size > _CONFIG_LIMIT:
        raise ValueError("invalid skills settings file")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"skills"} or not isinstance(data["skills"], dict):
        raise ValueError("settings require a 'skills' object")
    result = {}
    for name, value in data["skills"].items():
        if not isinstance(name, str) or not _SKILL_ID.fullmatch(name):
            raise ValueError("invalid configured skill ID")
        result[name] = SkillPolicy.parse(value)
    return result


class SkillManager:
    """Discover, inspect, and activate explicit local Python skills at startup."""

    def __init__(self, directory: Path, policies: dict[str, SkillPolicy] | None = None):
        self.directory = Path(directory)
        self.policies = dict(policies or {})
        self._statuses: dict[str, SkillStatus] = {}
        self._discovered: dict[str, SkillManifest] = {}

    @property
    def statuses(self) -> tuple[SkillStatus, ...]:
        return tuple(self._statuses[k] for k in sorted(self._statuses))

    def discover(self) -> tuple[SkillStatus, ...]:
        """Read manifests only. No Python module import or setup executed."""
        self._statuses.clear()
        self._discovered.clear()
        if not self.directory.exists():
            return ()
        if self.directory.is_symlink() or not self.directory.is_dir():
            raise ValueError("skill root must be a real directory")
        for directory in sorted(self.directory.iterdir()):
            if not directory.is_dir() and not directory.is_symlink():
                continue
            try:
                manifest = SkillManifest.read(directory)
                policy = self.policies.get(manifest.id, SkillPolicy())
                self._discovered[manifest.id] = manifest
                self._statuses[manifest.id] = SkillStatus(
                    manifest.id, manifest.name, manifest.version, policy.enabled, False)
            except (OSError, ValueError, json.JSONDecodeError, UnicodeError) as exc:
                self._statuses[directory.name] = SkillStatus(
                    directory.name, directory.name, "", False, False,
                    error=f"{type(exc).__name__}: {str(exc)[:120]}")
        for name, policy in self.policies.items():
            if policy.enabled and name not in self._statuses:
                self._statuses[name] = SkillStatus(name, name, "", True, False,
                                                    error="enabled skill was not found")
        return self.statuses

    def activate(self, registry: AuthorizedToolRegistry) -> tuple[SkillStatus, ...]:
        """Import only explicitly enabled skills; stage all bindings atomically.

        Skill code runs with the Python host's privileges; explicitly enabling
        a skill is a trust decision, not a sandboxed operation.
        """
        self.discover()
        for name, manifest in self._discovered.items():
            policy = self.policies.get(name, SkillPolicy())
            if not policy.enabled:
                continue
            try:
                path = manifest.root / manifest.entry
                if path.is_symlink() or not path.is_file():
                    raise ValueError("skill source changed after discovery")
                module_name = f"_zhuangfy_skill_{name}"
                spec = importlib.util.spec_from_file_location(module_name, str(path))
                if spec is None or spec.loader is None:
                    raise ValueError("cannot load skill")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                setup = getattr(module, "setup", None)
                if not callable(setup):
                    raise ValueError("skill must expose setup(context)")
                bindings = setup(SkillContext(manifest.id, manifest.version))
                if not isinstance(bindings, (tuple, list)) or not 1 <= len(bindings) <= 16:
                    raise ValueError("setup must return 1–16 tool bindings")
                selected: list[ToolBinding] = []
                for binding in bindings:
                    if not isinstance(binding, ToolBinding) or not callable(binding.handler):
                        raise ValueError("invalid skill tool binding")
                    if not _TOOL_NAME.fullmatch(binding.name) or not binding.name.startswith(name + "."):
                        raise ValueError("tool name must be namespaced to skill ID")
                    allowed = binding.scopes.intersection(policy.scopes)
                    if not allowed or (binding.mutating and not policy.allow_mutation):
                        continue
                    if not binding.description or not isinstance(binding.parameters, dict):
                        raise ValueError("missing skill tool description/schema")
                    selected.append(replace(
                        binding, scopes=frozenset(allowed),
                        visibility="core" if manifest.core_tool else "deferred",
                        discovery_terms=manifest.keywords))
                if not selected:
                    raise ValueError("no tools permitted by skill policy")
                registry.register_many(selected)
                self._statuses[name] = SkillStatus(
                    manifest.id, manifest.name, manifest.version, True, True,
                    tuple(b.name for b in selected))
            except Exception as exc:
                # Failure never prevents built-ins or other skills loading.
                self._statuses[name] = SkillStatus(
                    manifest.id, manifest.name, manifest.version, True, False,
                    error=f"{type(exc).__name__}: {str(exc)[:140]}")
        return self.statuses


def inspect_skills(root: Path, config: Path) -> tuple[SkillStatus, ...]:
    """Management-only view; safe to use without importing enabled skill code."""
    return SkillManager(root, load_skill_policies(config)).discover()


def build_skill_runtime(directory: Path, config_path: Path, *,
                        activate: bool = False) -> tuple[AuthorizedToolRegistry, SkillManager]:
    """Cross-channel startup entry; built-in tools remain available unchanged.

    Newly installed skill Python executes ONLY if host tool mode is on and the
    corresponding skills_settings.json policy explicitly enables that skill.
    """
    from .tools import build_builtin_registry

    registry = build_builtin_registry()
    manager = SkillManager(directory, load_skill_policies(config_path))
    if activate:
        manager.activate(registry)
    else:
        manager.discover()
    return registry, manager
