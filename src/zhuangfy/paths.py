"""Central path definitions, preserving existing local data until migration.

The legacy SenseVoice directory stays the default runtime location while it
exists. A caller can opt into a separate data directory explicitly. No files
are copied or silently relocated by this module.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class Paths:
    project_root: Path
    environment: Mapping[str, str]

    @classmethod
    def current(cls) -> "Paths":
        return cls(Path(__file__).resolve().parents[2], os.environ)

    @property
    def legacy_root(self) -> Path:
        return self.project_root / "SenseVoice"

    @property
    def data_root(self) -> Path:
        override = self.environment.get("ZHUANGFY_DATA_DIR", "").strip()
        if override:
            return Path(override).expanduser().resolve()
        if self.legacy_root.is_dir():
            return self.legacy_root
        return self.project_root / "data"

    @property
    def resources_root(self) -> Path:
        return self.project_root / "resources"

    @property
    def persona_root(self) -> Path:
        modern = self.resources_root / "personas"
        return modern if modern.is_dir() else self.legacy_root / "persona_packs"

    @property
    def images_root(self) -> Path:
        modern = self.resources_root / "images"
        return modern if modern.is_dir() else self.legacy_root / "assets"

    @property
    def models_root(self) -> Path:
        override = self.environment.get("ZHUANGFY_MODELS_DIR", "").strip()
        if override:
            return Path(override).expanduser().resolve()
        return self.data_root / "models"

    @property
    def conversation_root(self) -> Path:
        return self.data_root / "conversation_data"

    @property
    def memory_root(self) -> Path:
        return self.data_root / "memory_data"

    def config_file(self, filename: str) -> Path:
        if Path(filename).name != filename:
            raise ValueError("Configuration name must be a filename")
        return self.data_root / filename

    @property
    def gpt_sovits_root(self) -> Path:
        override = self.environment.get("ZHUANGFY_GPT_SOVITS_DIR", "").strip()
        return Path(override).expanduser().resolve() if override else self.project_root.parent / "GPT-SoVITS"

    @property
    def snowluma_root(self) -> Path:
        override = self.environment.get("ZHUANGFY_SNOWLUMA_DIR", "").strip()
        if override:
            return Path(override).expanduser().resolve()
        container = self.project_root.parent / "SnowLuma"
        if (container / "index.mjs").is_file():
            return container
        deployments = sorted(
            (path for path in container.glob("v*") if (path / "index.mjs").is_file()),
            reverse=True,
        )
        return deployments[0] if deployments else container

    @property
    def voice_training_root(self) -> Path:
        override = self.environment.get("ZHUANGFY_VOICE_TRAINING_DIR", "").strip()
        return Path(override).expanduser().resolve() if override else self.project_root.parent / "zhaungfyvoice"
