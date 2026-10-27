# 开发交接快照

## 2026-10-27 跨入口定时与桌面 Planner 工作区进展

本轮工作区尚未提交或推送。定时库从 QQ 专用 v2 原位升级为 v4，保留旧行 ID、状态和上下文；新增通道、会话引用、可选经 Memory Core 验证的 PersonID 与待投递文本。Dashboard 中持续运行的 Qt 定时循环在窗口收起到托盘后仍检查到期任务；QQ 消息先持久化为 `ready`，WebSocket 重连后由原 QQ 通道发送并凭 OneBot ACK 标记送达。桌面文字与语音任务只投递到各自创建入口，到期弹出对应窗口，界面显示后才标记送达。完全退出应用时不会运行定时循环，未到期或已过期的 `pending` 会在下次启动处理。

旧 QQ 任务若 `person_id` 为空，在新桥接连接时只对已准入账号向 Memory Service 查询已验证身份且有 Timeline 同意的 PersonID；匹配后补全所有历史任务的关联，原有任务状态不改。显式删除人物会清理该 PersonID 及其已知渠道身份的定时记录。

桌面 ChatWorker 已接 Planner V3 与受限工具循环：内置计算/时钟，以及 Memory Core 已确认本轮身份和 Timeline 权限时才可见的私人记忆工具、跨入口只读定时任务查询工具；未开放桌面主动问候、后台建议或关系自动演进。桌面图片复用视觉描述缓存、明确解释与投递后证据摘要。Memory HTTP 新增 `/v1/identity/resolve`；已核验同一 PersonID 的桌面入口可从任务记忆工具找回 QQ 私聊中来源明确的历史任务，QQ 查询仍按原 bot:user 隔离。需要重启 Memory Service 才能使用新 HTTP 路由；本轮未重启运行进程，也未向真实 QQ 发消息。运行中的进程仍是旧代码。

待实机复核：托盘收起后两个桌面入口的到期弹窗及语音播报、QQ WebSocket 断线后 `ready` 任务的重连发送、Memory Service 重启后的跨入口 PersonID 查询。QQ 未配置会话持久化时，到期生成仅使用创建快照；跨入口共用的是经核验身份下的记忆检索，不把不同渠道原始消息无条件拼成一段上下文。一次性提醒状态不等于关系自动演进。

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

2026-10-27 本地开发进展：离线测试已隔离 `chat_journal` 持久化和 Memory Service 环境连接；曾写入的 80 条可精确识别模拟聊天流记录已按进程及记录 ID 清理。已配置白名单群的本地 `qq_settings.json` 已加入 `passive_group_ids`；群消息独立落库，@ 必答，关键词触发 Planner 裁决，附带最多 60 条带昵称和 QQ 号的群上下文。群友按机器人/群/QQ 号自动建立独立群档；Memory Service 启用时同步建立独立 PersonID 与本地 Timeline，Curator 默认未批准。记忆管理页增加待提炼、未入队计数，以及 Curator 作业时间、来源范围和产出明细。这些改动仍在工作区，运行中的 QQ 桥接尚未重启，真实群端到端行为尚待验证。群档与私聊 PersonID 不自动合并；群消息保存期限与删除入口仍待决定。QQ 回复开头动作描写的格式器漏口已补回归测试。

本轮补充：QQ 与桌面 Replyer 提示显式禁止 `（）`/`()` 动作和心理旁白；角色知识开关开启时，会从相邻 `zhuang-fangyi-skill` 本地克隆只读 `references/knowledge` 限定文件，按武陵、人物、技术等问题追加带来源资料。可用 `ZHUANGFY_CHARACTER_SKILL_DIR` 指向其他克隆；缺失时跳过，不执行外部 `SKILL.md`。既有精简知识包未含武陵具体同事，原模型据空缺编造人名；此次对有来源的人名和分工加明确约束。离线定向回归 111 项通过；未对真实群发测试消息。

运行态更新：Memory Service 已由项目脚本重启；用户随后手动重启 QQ 桥接。只读检查确认新群档表已建立、10 个独立群友 PersonID、63 条群消息已写入记忆，0 人获 Curator 长期整理授权。没有用用户身份发测试消息。新代码的实际自然语言效果尚待真实对话观察。此前“运行中的 QQ 桥接尚未重启”的描述是重启前状态。

随后角色 Skill 链路再次调整：前一实现仍把原资料压成约 1,700 字摘要；现在按 Skill 知识路由实际读取最多两份原始 Markdown 文件，独立传给 QQ/桌面 Replyer，旧精简包只在未读到 Skill 时兜底。此更改发生在上一次手动重启之后；要加载新代码，需由用户按其最新要求手动重启。尚未做真实模型问答，不能把离线载荷验证当作内容质量验收。

最新调整覆盖前段“宿主知识路由”：现在由 Planner V3 在白名单中主动申请 `character_skill.read`，参数仅能是 7 个允许的文档 ID。宿主读取后把原文件送下一轮 Planner 和 Replyer；没有工具请求就不读。以隔离测试问题询问武陵共事者，真实 Planner 首轮请求 `relationships`，第二轮收到文件后选择 `reply`；没有使用真实用户身份或发送消息。此更改同样需要用户手动重启 QQ 和桌面进程后才进入运行态。

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
