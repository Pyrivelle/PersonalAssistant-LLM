"""Built-in, versioned persona prompts for voice, text and QQ conversations."""

from dataclasses import dataclass
from pathlib import Path

from zhuangfy.paths import Paths


PACK_ROOT = Paths.current().persona_root


@dataclass(frozen=True)
class PersonaPack:
    id: str
    name: str
    description: str
    prompt_path: Path

    def prompt(self) -> str:
        return self.prompt_path.read_text(encoding="utf-8").strip()


ZHUANG_FANGYI_V1 = PersonaPack(
    id="zhuang_fangyi_v1",
    name="庄方宜 · 场景驱动 v1",
    description="参考 MengPaul 的角色 Skill，适配日常陪伴与任务协助。",
    prompt_path=PACK_ROOT / "zhuang_fangyi_v1" / "prompt.md",
)

BUILTIN_PACKS = (ZHUANG_FANGYI_V1,)


def matching_pack(prompt: str) -> PersonaPack | None:
    """A changed prompt is a local customization, even when based on a pack."""
    return next((pack for pack in BUILTIN_PACKS if prompt.strip() == pack.prompt()), None)
