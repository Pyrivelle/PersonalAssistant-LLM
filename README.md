<img src="resources/images/zhuangfy.png" alt="ZhuangFY 图标" width="100" align="left">

# ZhuangFY

以所念为种，待理想成花。

<br clear="left">

---

## 项目介绍


## 功能与开发状态

## 快速开始


## 技术架构

## 开发文档


## 贡献与许可证

## 正在开发：独立向量记忆库（实验阶段）

新的 `src/zhuangfy/memory/` 已开始提供 SQLite + Qdrant Local 的人物隔离向量检索基础。**它还没有连接 QQ、语音或自动记忆提炼，也不会修改已有 Mem0 个人资料。**设计、安装与 Codex 后续开发目标见 [Memory Vector MVP](docs/architecture/memory-vector-mvp.md)。

### 长期记忆研发路线（暂未接入现有聊天流程）

- [M0：SQLite + Qdrant 向量记忆基础](docs/architecture/memory-vector-mvp.md)（PR #4）
- [M1：统一人物身份与私聊 Timeline](docs/architecture/memory-identity-timeline.md)（PR #5）
- [M2：可验证的 MemoryCurator 与后台反思](docs/architecture/memory-curator.md)（PR #6）

M1/M2 目前只是独立运行的记忆内核，不会自动采集 QQ/桌面聊天，也尚未把提炼结果投影到 Qdrant；实际接入须另行完成身份验证、授权、模型路由和跨进程协调。
