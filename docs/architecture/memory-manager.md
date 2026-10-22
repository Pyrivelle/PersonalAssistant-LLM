# M5 — 记忆管理、纠错、删除、事件关联与离线评测

M5 新增独立的 **PySide6 管理员记忆对话框**，和原有 `SenseVoice/memory_gui.py` 管理的老 Mem0 不是同一个数据库。新 UI 通过 M4 Memory Service 管理权限和权威 SQLite，**不允许 Qt 进程直接操作同一个 Qdrant Local 目录**。

## 打开新界面

1. 在一个独立终端启动 M4 Memory Service；保证服务端至少有 32 字符的 `ZHUANGFY_MEMORY_ADMIN_TOKEN`。
2. 在本机 **管理程序进程** 环境启用 `ZHUANGFY_MEMORY_ADMIN_UI=1`，并设置相同的管理员令牌。
3. 打开 ZhuangFY Dashboard → **新记忆管理 ↗**。

默认没有授权时管理界面只给出配置提示，不会自动采集聊天或发送 LLM 请求。**管理员令牌应仅用于可信本机控制台**；不要与 QQ 客户端共用、写进网页或提交到 GitHub。

功能：
- 浏览所有已明确建立的 Person ID 和对应有效长期记忆。
- 创建 Person、人工核实并关联 QQ/桌面账号、分别授权 Timeline 与 Curation。
- 查看每条记忆的用户原话证据（`entry_id`、发生时间、完整原文引用）和修改记录。
- 人工更正：记录原值、修改理由、操作时间，标记 `manual_override=1`。**修改后不能宣称新的内容已被旧原话自动证明**，这是管理者主动修正。
- 选择遗忘一条记忆及其旧版本；或者删除整个人物在新记忆内核中的身份、对话与索引。
- 检查反思队列及失败次数，重试已经失败的后台任务；按需重新同步 Qdrant。
- 将两段有证据的个人 Episode 显式链接为 `related_to/follow_up/resolved_by`。只允许同一 Person 的活跃 Episode 关联。

## 一致性规则

- 编辑通过 SQLite FTS5 Trigger 同步短语索引；向量依照 SHA-256 摘要识别旧内容、重建当前版本。即使 Qdrant 删除失败，RAG 仍会在最终读取时检查当前内容摘要并拒绝旧向量。
- 删除一条记忆会同时清除其较早被替代的版本，避免通过历史版本继续展示已遗忘的信息。**对应原始 Timeline 仍然存在**，需要删除人物全部历史时使用人物擦除。
- 删除人物依靠 SQLite 外键级联清除私聊、反思队列、人物画像、FTS 索引和事件关联；Qdrant 派生索引可通过 `sync_index()` 重试清除。
- 已存于旧 Mem0、QQ 成员记忆、额外备份、外部模型服务和其他副本的私人资料**不会因 M5 的新库删除操作自动消失**。跨库“彻底遗忘”需要后续单独审核与实现。
- 当前 SQLite 数据库是本机明文，需要磁盘权限管理、加密备份或密钥存储时后续引入；管理 UI 本身不等于完整认证系统。
- 当前普通用户只能通过已验证 QQ 身份主动 `/forget_me` 清除新服务中本人数据，未来可增加自助更正与授权视图。
- 人物关系链接由管理员明确建立，不根据向量相似度自动合并不同人物或不同事件。

## 评测

`zhuangfy.memory.evaluation.evaluate_memory_retrieval` 提供固定查询集的 hit@k、recall@k、跨人泄露次数与过时记忆返回次数。集成测试使用虚构人物、离线假模型及假 Embedding，不调用真实 LLM/QQ：

```bash
PYTHONPATH=src python -m unittest -v tests.integration.test_memory_admin
```

Windows GUI 无网络启动烟测：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
$env:PYTHONPATH = "src"
python -m unittest -v tests.unit.test_memory_manager_gui
```

下一阶段验收还应采用真实中文模型和经过匿名化的数据，对“相似度误召回、人物身份切换、共同经历误判、手动纠错后的回答、真实权限泄漏”进行质量测试。模拟测试通过不等于真实私聊已经验证安全。
