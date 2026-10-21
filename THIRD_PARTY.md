# 第三方来源与发布边界

本仓库保存 ZhuangFY 源码与说明，不提交本地模型权重、词表和音频样本。引用或参考的第三方资源如下；对应许可文本保留在 `SenseVoice/third_party/`。

| 项目或资源 | 在本项目中的用途 | 来源和许可线索 |
| --- | --- | --- |
| MengPaul 的庄方宜 Skill | 第一套人格包的场景判断、语气与设定事实边界参考 | [zhuang-fangyi-skill](https://github.com/MengPaul07/zhuang-fangyi-skill)，MIT；许可文本保留于 `SenseVoice/third_party/Zhuang-Fangyi-Skill-LICENSE.txt`。本项目重新编写日常助手提示词，未复制原仓库的剧情对白与 Wiki 整理。 |
| sherpa-onnx | SenseVoice、Silero VAD 的本地推理运行时 | [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)，Apache-2.0 |
| SenseVoiceSmall INT8 与 `tokens.txt` | 本地语音识别模型 | [固定版本的 ONNX 转换文件](https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/tree/2365baeacb507f821a0c8120fcee3d484dba7a07)；模型权重另受 [FunASR 模型许可](https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE) 约束 |
| Silero VAD | 检测语音片段 | [Silero VAD](https://github.com/snakers4/silero-vad)，MIT；ONNX 文件由 [sherpa-onnx release](https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx) 提供 |
| `zh.wav` 示例 | 可选的本地识别测试音频 | [固定版本的测试音频目录](https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/tree/2365baeacb507f821a0c8120fcee3d484dba7a07/test_wavs)；不随仓库发布 |
| Mem0 OSS | 长期记忆提取、检索与操作 | [Mem0 仓库](https://github.com/mem0ai/mem0)，Apache-2.0；通过 PyPI 安装，不复制其源码 |
| Qdrant Client | Mem0 的本地向量存储 | [Qdrant Python 客户端](https://github.com/qdrant/qdrant-client)，Apache-2.0；作为 Mem0 依赖安装 |
| SnowLuma | 本机 QQ / OneBot v11 协议层，独立部署，不放入仓库 | [SnowLuma 仓库与发行包](https://github.com/SnowLuma/SnowLuma)；源码可见、非商业许可及发行包 EULA，使用前核对其条款 |
| websockets | ZhuangFY 的 OneBot WebSocket 客户端 | [websockets](https://pypi.org/project/websockets/)，BSD-3-Clause；通过 PyPI 安装 |

运行依赖版本见 `SenseVoice/requirements.txt`。这些软件和资源各有自己的许可；此处保留来源供协作者核对，尤其是在打包分发模型文件之前。
