# 庄方宜 · 场景驱动 v1

这是 Shimano 的第一套正式人格包。运行时由 `persona_packs.py` 读取 `prompt.md`，作为 LLM 的 system prompt；语音窗口、文字测试和 QQ 桥接共用本机保存的提示词。`prompt.md` 可以在 UI 中载入后再编辑，编辑内容只写入本机的 `llm_settings.json`。

设计参考 [MengPaul07/zhuang-fangyi-skill](https://github.com/MengPaul07/zhuang-fangyi-skill) 的 `SKILL.md`、`references/voice.md` 与 `references/epistemics.md`，尤其是她对人和事的判断、不同场景下的表达，以及对设定证据的区分。提示词只描写行为与说话方式，不包含项目名或自我介绍式指令；原仓库的大量剧情、对白、Wiki 整理和示例没有复制到本项目。其仓库采用 MIT 许可，署名及许可见 `../../third_party/Zhuang-Fangyi-Skill-LICENSE.txt`。

新增人格包时，在 `persona_packs/` 下建立独立目录和 `prompt.md`，再在 `persona_packs.py` 的 `BUILTIN_PACKS` 中登记。保留稳定的 ID；已保存的自定义提示词不会因新增或切换包而自动覆盖。

## 第二阶段：场景逻辑自动路由

从 `fangyi_skill.py` 进入本版角色逻辑：不复制十五幕原始对白，而是把上游 Skill 的“先识别问题意图，再迁移合适场景的判断方式”的逻辑改写成短规则。在 Qt 文字测试、语音窗口和 SnowLuma QQ 对话中，保持原来的 `prompt.md` 作为稳定人格设定，只在情境相关时附加简短指导。

目前覆盖共研协作、困境与成长、分享成果、群体风险、生活日常和亲近边界。原作经历只构成人格背景，不能与用户真实聊天记录混合；角色对剧情的判断还须尊重来源和推断边界。没有匹配庄方宜人格时，不自动套用此 Skill。

完整移植上游 `references/scenes/`、`references/knowledge/` 与原文核验路由是后续阶段；在记忆来源追踪、资料版本化和按需加载机制成熟前，不建议全量塞入 System Prompt。
