# 开发交接快照

## 2026-10-27 更新

主项目仓库已改为 `https://github.com/Pyrivelle/SpringsReturn.git`。当前 `main` 的 QQ 桥接已将 Planner `wait` 限定为短暂等待续发；一次性提醒与延迟回复使用独立 SQLite 定时任务。提醒到期后由 Reply 模型结合创建时快照和同一私聊的近期上下文生成内容，再按 OneBot ACK 记录投递。当前定时任务的创建和投递仅接入 QQ 私聊，桌面文字及语音入口尚未接入；桌面入口与 QQ 入口仍各自编排对话，后续应把通道无关的定时与工具执行上移到共享运行时。

本次离线核查：`tests/unit` 共 299 项、桌面与 QQ 相关测试共 139 项、记忆 HTTP 集成测试共 18 项通过。测试中的模拟服务异常与资源警告不代表真实发送失败。下面保留 2026-10-25 的历史快照，仅作当时状态记录，不覆盖此更新或当前代码。

## 2026-10-25 历史快照

记录于 2026-10-25（Asia/Hong_Kong）。这是一次**文档整理**，没有修改功能、重启进程、提交、推送或清理工作区。路径和 Git/测试结论为本次检查事实；此前实机对话与设计意图单独标记。新会话先核对实时状态，不把本页当作运行保证。

## 已核实的本地状态

| 工作树 | 本次检查结果 | 注意 |
| --- | --- | --- |
| 主检出目录 | `main`，HEAD `2189c03`，当时 remote 为旧项目地址 | 已有 9 个 tracked 修改和 `src/zhuangfy/memory/self_profile.py` 未跟踪文件；本次只新增文档，**不得丢弃这些改动**。本页不声称其已推送。 |
| PR #54 实验检出目录 | `test/pr54-hybrid`，HEAD `6102b48`；检查开始时无未提交改动 | 对应 GitHub Draft PR #54 的 `experiment/maisaka-full-runtime`。`third_party/MaiBot` 子模块已检出固定 SHA。此处的 Hybrid 代码尚未进 main。 |

main 的既有未提交变更涉及 `SenseVoice/chat_worker.py`、`qq_bridge.py`，记忆客户端/协调器/HTTP、关系核心、管理员脚本及相关测试：新增用户自述档案读取/设置、onboard 的 `person_id`、关系 `bond_style`/`bond_stage`，并尝试给桌面/QQ 注入档案与关系。它们仍是**进行中代码**，不是合并完成的特性。不要为更新文档顺手覆盖、提交或向 PR #54 复制这些改动。

主分支已具 Qt 控制台与语音窗、SenseVoice/VAD、GPT-SoVITS、SnowLuma/OneBot QQ、模型管理/路由、自有人格、Agent V2/V3、聊天流、Memory Service/PersonID/Timeline/整理/R1 关系等代码。具体是否启用取决于被忽略的本地配置。PR #54 增加上游原生 Maisaka Hybrid 单人私聊预览，代码已打通共享身份/关系/记忆上下文、IPC v2、真实 QQ ACK 后记忆提交及 Planner/Reply/Wait/Stop 观测；**不等于通用 QQ 生产发布**。

本次仅运行了主目录针对性测试：`tests.unit.test_relationship_core_r1b` 13 项通过。`tests.integration.test_memory_http` 与关系单测合跑共 31 项时有 1 项失败；单独跑集成测试 18 项时仍有相同 1 项失败：`test_relationship_http_opt_in_correction_event_and_atomic_erasure` 预期未授权 401，得到 503；只运行该测试方法则通过。另有任务进展提取被跳过和 socket `ResourceWarning` 输出，Timeline 测试仍继续。**推断**：与测试顺序或共享服务状态有关，尚未定位，不能称整组通过。前一开发回合的 PR #54 Hybrid 相关 49 项定向单测通过；本次未重跑它们。此前已由已授权 QQ 私聊实测收发、PersonID/L4 上下文及回复 ACK→Timeline，但进程是否仍运行、Wait 超时唤醒、图片 VLM 和删除联动的端到端结果本次未复验。

## 历史意图与目前边界

- 用户要的是能长期相处的庄方宜：语音、文字、QQ 共用可信用户身份和记忆；自然表达，避免机械式客服和凭图片/只言片语断言情绪。原作设定是行为参考，不等于现实共同经历。
- 主人的确认称呼、身份自述和关系定义要绑定**正确 PersonID**；主人希望比“朋友”更亲近、接近恋人但尚未正式确认的自然表达。L4 只代表层级，不能单独推出具体关系；其他测试用户从保守关系开始。未经核验不能因 QQ 昵称相似合并身份。
- 已否决仅修改 Maisaka 人格 Prompt 来修复“我是谁/我们是什么关系”；应由 ZhuangFY 宿主提供可信身份和关系快照，Planner/Replyer 共同使用。也不允许 Hybrid 私自另开 QQ 通道、共享上游原始数据库、把请求受理算回复投递。
- 聊天流应看到每轮 Planner 的实际输入历史、确有返回的推理、规划正文、工具/`wait`/`stop` 决策和 QQ 送达，按角色会话分组。不能模拟不存在的模型思考；控制台刷新/定位问题与当次运行日志是独立 UI 工作。
- 主动消息、群聊、联网搜索和远程记忆整理必须服从白名单、账号身份、授权与配置。不能因为某项代码存在就默认开启。

## 当前优先事项与未验证点

1. 先核对两个工作树的 `git status`、最新远端 PR/head、当前服务/QQ 进程与被忽略的本地配置。PR #54 与 main 的差异、main 未提交自述档案/关系字段尤其不能混淆。
2. 若继续 Hybrid：验证“你是谁 / 我是谁 / 我们关系 / 最近聊到哪 / Wait 后追问”五类对话，核对 Planner 与 Replyer 使用同一 PersonID/关系资料，观察实际 QQ ACK 后 Timeline。检查图片 VLM、等待计时器、失败/重连、撤销同意和删除联动；目前没有充分端到端证据。
3. 复核主目录整组记忆 HTTP 的偶发 503，定位测试服务/连接关闭时序，勿在文档整理任务里改功能。评估 main 未提交档案/关系实现与 PR #54 的 `social_confirmations.json` 是否会产生双权威，明确迁移/归一方案后再合并。
4. 整理过时的 README、`docs/architecture/README.md`、`docs/maisaka-native-transplant.md` 状态描述；根 README 当前为待写模板，不能依它判断最新功能。此项是**后续文档任务**，本次未重写历史资料。

## 新会话恢复步骤（PowerShell）

从实际要工作的检出目录开始；不要直接把 `main` 和 PR #54 的未提交内容混合。

```powershell
Set-Location <项目主检出目录>
git status --short
git branch --show-current
git log -1 --oneline
```

若在 PR #54 检出工作，先切换到该检出目录，再检查 `git submodule status third_party/MaiBot`。子模块未初始化时可运行 `git submodule update --init third_party/MaiBot`。仅查看三引擎状态可在主检出目录运行 `& .\SenseVoice\.venv\Scripts\python.exe scripts/chat_engine.py status`；**不要用状态命令替代运行态/QQ 连接验证**。Hybrid 启动必须使用该脚本 `--help` 核实显式预览、独占通道及授权测试用户参数，确认另一 QQ 桥接已停止后才运行。

本机已存在主目录 `SenseVoice/.venv/Scripts/python.exe`。以下命令只做本地测试，不会调用真实云端模型；在主目录执行：

```powershell
$env:PYTHONPATH = "$PWD\src;$PWD\SenseVoice"
& .\SenseVoice\.venv\Scripts\python.exe -m unittest tests.unit.test_relationship_core_r1b -q
& .\SenseVoice\.venv\Scripts\python.exe -m unittest tests.integration.test_memory_http -q
```

桌面启动路径是 `SenseVoice/启动界面.cmd`；带记忆服务的明确脚本是 `scripts/windows/start-memory-enabled.ps1`，需要本地 `data/memory_runtime.json`（或所选存储位置）及 `SenseVoice/.env.local`（或所选 legacy 位置），**不要在公开输出打印文件内容或令牌**。若只需检查脚本定义，先读文件；启动/切换 QQ 引擎前核对端口、单实例和用户授权。项目导读顺序：根 `AGENTS.md` → `docs/ARCHITECTURE.md` → 本页 → 要改的源码/对应测试 → 专题文档。
