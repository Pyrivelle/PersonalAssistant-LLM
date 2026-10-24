# MaiBot / Maisaka 原生引擎实验（完整上游源码）

> **状态：独立原版实例基线已接入仓库，ZhuangFY 通讯和记忆接管未完成。默认关闭，不替换线上 QQ Bridge。**
>
> Upstream: [Mai-with-u/MaiBot](https://github.com/Mai-with-u/MaiBot)  
> Pin: `062d45cf8bd4272ac57d7ec03fcf405e5a458211`  
> License: GPL-3.0（上游作者归属和完整许可证随 Git 子模块保留）

## 目标

不再仅仅借鉴一个 Prompt 或单独复制 wait/stop 函数。完整上游代码以
`third_party/MaiBot` 子模块形式固定，使用 **真正的**
`src/maisaka/reasoning_engine.py`、`src/maisaka/runtime.py`、
`src/maisaka/chat_loop_service.py`、`src/maisaka/builtin_tool/{reply,wait,stop}.py`
以及 `src/chat/image_system/image_manager.py`。

MaiBot 的模型→工具→模型循环及图片识别与缓存是一套完整子系统，
包含自己的依赖、配置、数据库、消息历史和角色 Prompt。仅复制 Planner 文件
无法复现“reply 后独立多次 wait、超时唤醒再规划”的行为。

## 运行原生基线（Windows PowerShell）

从 `main` 切换到 **实验分支**（不可直接合并）：

```powershell
git fetch origin
git switch experiment/maisaka-full-runtime
git submodule update --init third_party/MaiBot
```

为 MaiBot 配置独立 Python 3.12+ 环境，推荐使用其上游锁文件：

```powershell
uv sync --project third_party/MaiBot --locked
$maiPy = "./third_party/MaiBot/.venv/Scripts/python.exe"
python scripts/maisaka_native_experiment.py check
python scripts/maisaka_native_experiment.py init --python $maiPy
```

在 `.maisaka-experiment/config` 配好 `bot_config.toml` 和
`model_config.toml`，按照 MaiBot 上游文档选择模型与 QQ 通道。

**不要同时让两个程序连接同一 QQ 账号的 OneBot 适配器。**
停止 ZhuangFY 的 QQ Bridge 后，才允许运行原版：

```powershell
python scripts/maisaka_native_experiment.py run --python $maiPy --confirm-exclusive-channel
```

此时运行的是完整的 **MaiBot 原生 runtime**，包括真正的 wait 定时器与
Vision Manager，而不是 ZhuangFY 自行仿写的循环。启动器要求不同的数据目录；
不会读取或写入 ZhuangFY 的长期记忆。检查脚本要求本地子模块精确位于上述
revision，防止实验结果受上游更新影响。

## 集成路线与不可逾越的边界

| 阶段 | 范围 | 状态 |
| --- | --- | --- |
| A. 固定完整上游 + 独立原生实例 | 保留完整 GPL 源码、原生图片缓存、Planner/Reply/Wait/Stop | 已提交启动器与离线测试；真实模型/QQ 未验收 |
| B. 上游进程到 ZhuangFY 事件边界 | QQ MessageComponent / Observation / ToolResult 投影、真实投递 ACK 回传 | 未实现 |
| C. 合并 ZhuangFY Persona / PersonID / Memory | 双向读取映射、撤回/删除、会话隔离、权限一致 | 未实现 |
| D. 真正的实验主路径切换 | `experimental_maisaka_enabled` 开关、超时/错误回退、退出清理 | 未实现 |
| E. 原生图片处理接管 | 完整 hash/cache/async VLM 及跨模型失效；兼容 QQ 图片 | 仅原版实例可试验，ZhuangFY 尚未接管 |

这里 **不宣称 B–E 已经实现**。即使子模块已经包含全套源码，
不能把本机 MaiBot 独立进程等同于 ZhuangFY 已支持这套循环。
直接将 `src.maisaka` import 到现有 ZhuangFY 进程还会遇到顶层 `src`
包名、数据库连接和全局配置冲突，因此应隔离为子进程，通过窄协议桥接。

## 基线复现实验

在原版实例依次测试：“猜一猜这四个多少钱”→ Replyer 回复→
用户沉默→ Planner 选择 `wait(60)` 等→计时器再次触发 Planner→
等待或 stop。记录每次 Planner 原始动作与最终动作，区分
“模型选择继续判断”与“程序强制调度”。相同照片重复发送，
记录缓存命中、VLM 请求数、描述和用户解释的隔离行为。

**不要**在上游还未接入 ZhuangFY 时，用其运行结果声称庄方宜已经具备这些行为。
