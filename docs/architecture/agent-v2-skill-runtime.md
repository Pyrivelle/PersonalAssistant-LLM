# Agent Runtime V2 — Tool Runtime 插件化（下一阶段）

**状态：实验阶段，明确授权后启用。** 本模块是在 GPL-3.0 的 ZhuangFY 上建立的通用技能管理层，不是完整的 Maisaka 插件系统；仍可继续借鉴 MaiBot 的工具接口、生命周期与权限管理。

## 架构

```text
渠道 (QQ / 后续微信、桌面语音)
   -> UnifiedTurnScheduler
   -> Planner V2
      -> Host-owned AuthorizedToolRegistry
         ├── 内置工具 (calculator.evaluate / clock.utc_now)
         └── SkillManager
             ├── 只读取 skill.json 发现技能，不运行代码
             ├── skills_settings.json 显式启用
             ├── 导入受信任的 skill.py 并调用 setup(context)
             ├── 按宿主授权限制 private/group 与 mutating
             ├── 整批注册工具（冲突时不留下半注册的工具）
             └── 错误隔离、向管理员提供状态
      -> ToolReceipt -> Planner 再规划（现有 AgentCycleEngine）
      -> Replyer -> 渠道 Dispatcher
```

`src/zhuangfy/agent_v2/skills.py` 提供可在各渠道共用的 `build_skill_runtime()`；目前 **QQ Bridge 已使用这一统一入口**，无需为每个技能修改 QQ 适配器。

## 设计约束

1. **不能自动运行“发现”的技能。** 扫描 `skill.json` 不会导入模块。只有宿主开启 V2 工具功能，且 `skills_settings.json` 中对具体技能显式 `enabled=true` 时，才会执行该技能 `skill.py` 的模块代码及 `setup(context)`。
2. **Python 插件不是沙盒。** 一旦管理员启用不可信的 Python 技能，代码仍拥有宿主进程权限；工具权限列表无法阻止恶意 Python 模块在导入时读取数据或联网。仅安装/启用经过审查的代码，不从聊天消息或网络自动下载安装技能。本阶段不提供一键安装外部包。
3. **两层权限：** 插件政策限制它能注册哪些工具（private/group，是否允许变更操作），执行器仍单独检查 `allow_mutation`。**当前 QQ 运行器将 `allow_mutation` 固定为 false**，因此即使管理员误开某个插件的 mutating 授权，也不会真正执行设备写入。
4. 模型只能看到**已经注册且有权使用**的工具名称、描述和 JSON 参数 Schema。执行时，宿主校验参数，拒绝未知工具；LLM 无法自行注册/加载技能，也无法突破权限。
5. 单个插件异常不会阻止其他插件或两个内置工具运行。插件同名冲突使该插件整批失败。运行时由 `AgentCycleEngine` 提供多轮次数、时长、重复调用、回执和失败终止保护。
6. 私聊/群聊是**渠道粗粒度的作用域**，不是跨账号身份授权；记忆检索和智能家居控制等高权限技能，必须在未来插件服务层加入平台、用户、设备级授权、确认/审计与独立进程隔离之后才可以启用。此版本不包含这些控制技能。
7. 目前采用启动时加载；调整 `skills_settings.json` 后需重启 QQ Bridge。没有在线热重载、插件商店、UI 或多渠道统一部署服务。

## 项目示例

`SenseVoice/skills/text_stats/` 是代码仓库随附的安全示例：

- `skill.json` 是静态元数据：ID、显示名称、版本、说明、入口文件 `skill.py` 和 API 版本。
- `skill.py` 提供 `setup(context)`，返回一组 `ToolBinding`，每个工具包括唯一的 `skill_id.tool_name`、`handler`、作用域、是否写入、说明、参数 Schema。
- 工具 `text_stats.length` 只统计 **显式传入的文字** 长度，不会读取聊天记录、个人记忆或文件系统。默认不开启。

启用步骤：

1. 保留已存在的 `SenseVoice/cognition_settings.json`，设置 `agent_v2_enabled=true` 与 `agent_v2_tools_enabled=true`。
2. 把 `SenseVoice/skills_settings.example.json` 复制为 `SenseVoice/skills_settings.json`（该实际配置已加入 `.gitignore`）。
3. 在配置中明确启用技能：

```json
{
  "skills": {
    "text_stats": {
      "enabled": true,
      "scopes": ["private"],
      "allow_mutation": false
    }
  }
}
```

4. 重启 QQ Bridge，才能完成动态注册。Planner 只在适当时机调用已注册工具；对于文字统计、简单算术等任务可验证“调用工具 → 获得回执 → Planner 继续规划 → Replyer 回答”。

## 命令行管理（只读）

从项目根目录运行：

```bash
PYTHONPATH=src python -m zhuangfy.agent_v2.skills_cli \
  --root SenseVoice/skills \
  --config SenseVoice/skills_settings.json --json
```

它只展示已发现技能的 ID、版本、启用状态和静态清单错误，**不导入任何技能 Python 文件**。运行期的加载成功/失败还会记录在 QQ 桥接的活动日志中。

## 后续能力边界

下一批可增加：统一 UI 插件中心、安装包签名/来源锁定、隔离执行进程、真实的 `start/stop` 生命周期与安全热更新、按平台/使用者/群及设备的权限、安装/更新审批、工具审计与机密存储接口，以及微信/桌面/语音使用同一工具宿主的适配。Focus 共享注意力系统仍单独开发。

对于米家和其他具有现实副作用的工具，应优先实现账户与设备许可映射、确认、幂等/未知结果处理，而不是直接开放 `allow_mutation=True`。
