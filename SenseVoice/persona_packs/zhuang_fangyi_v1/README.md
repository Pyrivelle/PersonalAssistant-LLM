# 庄方宜 · 场景驱动 v1

这是 Shimano 的第一套正式人格包。运行时由 `persona_packs.py` 读取 `prompt.md`，作为 LLM 的 system prompt；语音窗口、文字测试和 QQ 桥接共用本机保存的提示词。`prompt.md` 可以在 UI 中载入后再编辑，编辑内容只写入本机的 `llm_settings.json`。

设计参考 [MengPaul07/zhuang-fangyi-skill](https://github.com/MengPaul07/zhuang-fangyi-skill) 的 `SKILL.md`、`references/voice.md` 与 `references/epistemics.md`，尤其是她对人和事的判断、不同场景下的表达，以及对设定证据的区分。提示词只描写行为与说话方式，不包含项目名或自我介绍式指令；原仓库的大量剧情、对白、Wiki 整理和示例没有复制到本项目。其仓库采用 MIT 许可，署名及许可见 `../../third_party/Zhuang-Fangyi-Skill-LICENSE.txt`。

新增人格包时，在 `persona_packs/` 下建立独立目录和 `prompt.md`，再在 `persona_packs.py` 的 `BUILTIN_PACKS` 中登记。保留稳定的 ID；已保存的自定义提示词不会因新增或切换包而自动覆盖。
