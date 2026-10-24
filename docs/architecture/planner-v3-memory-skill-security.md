# Planner V3 — Memory Skill 与未来米家权限边界

状态：实验阶段，默认 V3 仍未启用。本轮不连接、控制任何真实米家设备；Skill 开关 UI 另行迭代。

## 只读 Memory Skill 运行路径

配置要求：agent_v2_enabled=true、agent_v2_tools_enabled=true、agent_v3_enabled=true；Memory Service 和 QQ Bridge 已启用，并成功保存了当轮 QQ 私聊用户的原始文本。

- QQ Bridge 先使用可信入站事件中的 Bot QQ ID 与用户 QQ ID 建立当前轮 SkillPrincipal。只有已经成功获得 Memory Service 写入回执才临时注册 task_memory.search。
- 本轮独立注册表在已启用工具基础上追加临时 Memory 工具；全局共享 Registry 不持有当前人物，也不会因为其他 QQ 用户进入同一进程导致身份串线。
- 新工具是 deferred：只有用户文本与上次、之前、记得、继续、项目进展等明显指向历史的关键词相关时才向 Planner 展示完整 Schema。即便展示也不代表必须调用，Planner 可以直接回复。
- Skill 模型参数仅 query（2..240 字符）和可选 limit（1..2），不暴露可模型编辑的 person_id、sender_id、bot_id、device_id。
- 调用端执行前做参数校验；Memory Service 再校验渠道身份与授权。实际读历史共同行动使用已有 task_recall，只找可信建立来源关联的任务，返回经过约束的起点、用户报告进展和候选历史片段。若尚没有任务关联，正常返回未发现，禁止编造。
- V3 的 QQ Replyer 不再主动无条件查询 curated memory 和 task recall；只有 Planner 真正选择 Memory Skill 才执行查询。V2 沿用之前自动召回逻辑。
- 由于仍然是旧的 ToolReceipt，输出截断为 450 字以内，供下一轮 Planner/Replyer 参考；不能把其内容当成新的系统指令，也不能凭用户自述的完成情况声称已经独立核实。

## 未来米家能力的四道权限边界

1. 安装/全局启用权限：只有管理员审查并启用受信 Skill；第三方 skill.py 能在宿主进程执行任意 Python，不构成沙盒。
2. 身份权限：宿主使用可信 QQ/微信/桌面渠道入站身份。身份不从 LLM 参数取得，同名不同账号不可自动合并；跨平台统一 Person 需要证明与绑定。
3. 设备/操作范围：SkillAccessPolicy 原型使用 principal_key + capability + resource_id + expires_at 精确匹配。默认没有任何 grant。独立米家控制器未来还必须从已认证米家账号核验设备 ID 和资源归属，不可以仅使用模型给出的设备名或主人姓名。
4. 实际动作授权：物理写操作需要明确的当次用户意图、必要的逐次确认（短期有效的一次性确认 nonce），独立执行检查与审计日志。本轮原型**强制拒绝一切 mutating=True**，即便 grant 写了 allow_mutation=True，也不能开灯。QQ 现有的 AgentCycleEngine 同样不允许 mutating Tool。

未来家庭共享设备要分别给可信人物明确授予设备与操作能力、支持撤销和时间限制；对门锁、摄像头、安防设备需要更高保护。不能把“关系亲密度提升”映射成设备权限提升。

## 测试与后续

- V3 在聊天里实际申请检索才能访问 TaskContinuity；普通情绪社交消息不应读取 Memory Core。
- 公开/群聊或没有成功写入私聊的当前轮，不出现这项私有 Memory Skill。
- 同名但不同 QQ ID/不同 Bot ID 不能共享该轮工具身份；原 Registry 不被污染；模型参数中传入他人的身份被 Schema 拒绝。
- 已撤销授权的 Memory Service 返回拒绝，工具记录失败不伪造成已读。
- 设备策略同一人物但不同设备、不同操作、授权过期均拒绝，变更型请求一律拒绝。
- 当前缺少大规模真实数据，不能验证 Planner 在自然语言表达下的召回覆盖率；以后增加 BM25、语义检索、多轮 tool_search 与工具决策审计。
- 米家对接独立于本次提交规划，实际采用何种 API/认证方式需要另行验证后决定。
