"""The stream keeps interleaved chat turns distinct and hides background work."""

import json
import unittest

from chat_stream import (conversation_id, conversation_title, project_turns,
                         qq_display_names, thought_sections)


def event(turn, channel, source, phase, detail):
    return {"turn_id": turn, "channel": channel, "source": source,
            "phase": phase, "detail": detail, "time": "2026-10-22T18:48:48+08:00"}


class ChatStreamTests(unittest.TestCase):
    def test_each_model_input_appears_in_request_order(self):
        turn = project_turns([
            event("one", "QQ", "QQ", "消息", "private:0:1 · 你好"),
            event("one", "QQ", "模型", "输入原句", json.dumps({
                "stage": "思考", "model": "planner-a", "text": "你好",
                "history_count": 2, "has_image": False}, ensure_ascii=False)),
            event("one", "QQ", "模型", "输入原句", json.dumps({
                "stage": "回复", "model": "replyer-b", "text": "请自然回应",
                "history_count": 2, "has_image": False}, ensure_ascii=False)),
        ])[0]
        sections = thought_sections(turn["thinking"])
        self.assertEqual(len(sections), 2)
        self.assertIn("planner-a", sections[0][1])
        self.assertIn("原句：\n你好", sections[0][3])
        self.assertIn("replyer-b", sections[1][1])
        self.assertIn("请自然回应", sections[1][3])

    def test_system_prompt_card_shows_actual_role_text_and_fingerprint(self):
        turn = project_turns([
            event("one", "QQ", "QQ", "消息", "private:0:1 · 你好"),
            event("one", "QQ", "模型", "系统提示词", json.dumps({
                "stage": "思考", "model": "planner-a", "system_count": 1,
                "system_chars": 1500, "system_sha256": "abc123",
                "role_pack": "zhuang_fangyi_v1", "role_prompt": "你是庄方宜。",
            }, ensure_ascii=False)),
        ])[0]
        section = thought_sections(turn["thinking"])[0]
        self.assertIn("系统提示词", section[1])
        self.assertIn("SHA-256：abc123", section[3])
        self.assertIn("本次注入的庄方宜角色提示词：\n你是庄方宜。", section[3])

    def test_image_description_replaces_length_only_summary(self):
        turns = project_turns([
            event("picture", "QQ", "QQ", "消息", "private:0:1 · 这是什么？"),
            event("picture", "QQ", "QQ", "识图", "private:0:1 · 完成 · 116 字"),
            event("picture", "QQ", "QQ", "识图内容", json.dumps({
                "source": "缓存", "description": "图中是一只橘猫。",
                "context_note": "用户上次说是自家的猫。"}, ensure_ascii=False)),
        ])
        sections = thought_sections(turns[0]["thinking"])
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0][1], "图片理解")
        self.assertIn("来源：缓存\n图中是一只橘猫。", sections[0][3])
        self.assertIn("仅供参考", sections[0][3])

    def test_legacy_image_summary_is_labeled_as_summary(self):
        turn = project_turns([
            event("picture", "文字", "文字", "消息", "turn:picture · 这是什么？"),
            event("picture", "文字", "文字", "识图", "turn:picture · 完成 · 116 字"),
        ])[0]
        self.assertEqual(turn["thinking"][0], ("图片理解（旧记录仅摘要）", "完成 · 116 字"))

    def test_tool_and_uncertain_delivery_are_visible_without_a_reply(self):
        turn = project_turns([
            event("tool", "QQ", "QQ", "消息", "private:0:1 · 查一下"),
            event("tool", "QQ", "Agent", "工具开始", "private:0:1 · P1 · web_search.search"),
            event("tool", "QQ", "Agent", "工具结束", "private:0:1 · P1 · 成功"),
            event("tool", "QQ", "QQ", "投递状态不明", "private:0:1 · 第 1/2 段 · TimeoutError"),
            event("tool", "QQ", "QQ", "未完全投递", "turn:tool · 0/2 段已确认 · 某段的确认状态不明"),
        ])[0]
        self.assertEqual(turn["status"], "投递状态不明")
        self.assertEqual(turn["reply_parts"], [])
        self.assertEqual([label for label, _ in turn["thinking"]],
                         ["工具执行", "工具执行", "投递状态", "投递状态"])

    def test_acknowledged_part_is_not_a_completed_multi_part_reply(self):
        turn = project_turns([
            event("parts", "QQ", "QQ", "消息", "private:0:1 · 解释一下"),
            event("parts", "QQ", "QQ", "投递开始", "turn:parts · 2 段"),
            event("parts", "QQ", "QQ", "回复", "private:0:1 · 第一段"),
            event("parts", "QQ", "QQ", "未完全投递", "turn:parts · 1/2 段已确认 · 发送被限制"),
        ])[0]
        self.assertEqual(turn["reply_parts"], ["第一段"])
        self.assertEqual(turn["status"], "投递受限")

    def test_final_ack_completes_original_turn_after_continue(self):
        turns = project_turns([
            event("original", "QQ", "QQ", "消息", "private:0:1 · 解释一下"),
            event("original", "QQ", "QQ", "投递开始", "turn:original · 2 段"),
            event("original", "QQ", "QQ", "回复", "private:0:1 · 第一段"),
            event("original", "QQ", "QQ", "未完全投递", "turn:original · 1/2 段已确认 · 发送被限制"),
            event("continue", "QQ", "QQ", "消息", "private:0:1 · /continue"),
            event("original", "QQ", "QQ", "回复", "private:0:1 · 第二段"),
            event("original", "QQ", "QQ", "已投递", "turn:original · private:0:1 · 2 段"),
            event("continue", "QQ", "QQ", "控制完成", "补发请求已处理，进度见原对话轮次"),
        ])
        self.assertEqual(turns[0]["reply_parts"], ["第一段", "第二段"])
        self.assertEqual(turns[0]["status"], "已回复")
        self.assertEqual(turns[1]["status"], "指令已处理")

    def test_failure_reason_is_visible(self):
        turn = project_turns([
            event("fail", "文字", "文字", "消息", "turn:fail · 你好"),
            event("fail", "文字", "文字", "失败", "turn:fail · ValueError"),
        ])[0]
        self.assertEqual(turn["status"], "处理失败")
        self.assertEqual(turn["thinking"][-1], ("处理失败", "ValueError"))

    def test_search_result_appears_in_same_turn_with_full_source(self):
        turns = project_turns([
            event("qq-search", "QQ", "QQ", "消息",
                  "private:0:964692788 · 帮我查最新资料"),
            event("qq-search", "QQ", "QQ", "搜索结果",
                  "private:0:964692788 · 第1次搜索 · 成功\n"
                  "[1] 官方资料 | https://example.org/news | 摘要"),
            event("qq-search", "QQ", "QQ", "回复",
                  "private:0:964692788 · 查到了。"),
        ])
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["thinking"][0][0], "联网搜索结果")
        sections = thought_sections(turns[0]["thinking"])
        self.assertIn("https://example.org/news", sections[0][3])

    def test_verified_memory_name_labels_only_matching_bot(self):
        people = [{"display_name": "小庄的朋友", "identities": [{
            "platform": "qq", "channel_account_id": "3312627226",
            "external_user_id": "3436756920"}]},
            {"display_name": "别的机器人用户", "identities": [{
                "platform": "qq", "channel_account_id": "999",
                "external_user_id": "42"}]}]
        names = qq_display_names(people, 3312627226)
        self.assertEqual(conversation_title("person:3436756920", names),
                         "与小庄的朋友的对话")
        self.assertEqual(conversation_title("person:42", names),
                         "与用户 42 的对话")

    def test_linked_owner_groups_text_voice_and_private_qq_by_person(self):
        owner_qq = 964692788
        local = {"channel": "文字", "scope": ""}
        voice = {"channel": "语音", "scope": ""}
        qq_owner = {"channel": "QQ", "scope": "private:0:964692788"}
        qq_other = {"channel": "QQ", "scope": "private:0:123456"}
        group = {"channel": "QQ", "scope": "group:789:123456"}
        self.assertEqual(conversation_id(local, owner_qq), "owner")
        self.assertEqual(conversation_id(voice, owner_qq), "owner")
        self.assertEqual(conversation_id(qq_owner, owner_qq), "owner")
        self.assertEqual(conversation_id(qq_owner), "person:964692788")
        self.assertEqual(conversation_id(qq_other, owner_qq), "person:123456")
        self.assertEqual(conversation_id(group, owner_qq), "group:789")

    def test_planner_reasoning_and_decision_share_one_foldable_section(self):
        sections = thought_sections([
            ("第1轮思考 · 模型推理", "思考 · 1/1\n分析消息"),
            ("第1轮思考 · Planner 规划", '{"action":"wait","reply_act":"等他补充"}'),
            ("第1轮结论 · 等待补充", "行为：wait"),
            ("回复阶段推理", "回复 · 1/1\n自然回应"),
        ])
        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0][1], "Planner · 第1轮")
        self.assertIn("分析消息", sections[0][3])
        self.assertEqual(sections[1][1], "回复阶段推理")

    def test_interleaved_channels_keep_their_own_reasoning_and_reply(self):
        turns = project_turns([
            event("qq1", "QQ", "QQ", "消息", "private:0:964692788 · 晚上好"),
            event("pc1", "文字", "文字", "消息", "turn:pc1 · 你在吗"),
            event("qq1", "QQ", "模型", "推理内容", "回复 · 1/1\n先回一句晚安"),
            event("pc1", "文字", "模型", "规划结果", '{"tone":"自然"}'),
            event("qq1", "QQ", "QQ", "回复", "private:0:964692788 · 晚上好。"),
            event("qq1", "QQ", "QQ", "回复", "private:0:964692788 · 今天还顺利吗？"),
            event("pc1", "文字", "文字", "回复", "我在。"),
        ])
        self.assertEqual([turn["message"] for turn in turns], ["晚上好", "你在吗"])
        self.assertIn("先回一句晚安", turns[0]["thinking"][0][1])
        self.assertEqual(turns[0]["reply"], "晚上好。\n今天还顺利吗？")
        self.assertEqual(turns[1]["reply"], "我在。")
        self.assertNotIn("先回一句晚安", str(turns[1]))

    def test_background_reasoning_is_not_a_chat_turn(self):
        turns = project_turns([
            event("qq1", "QQ", "QQ", "消息", "private:0:964692788 · 你好"),
            event("qq1", "QQ", "模型", "推理内容", "成员记忆反思 · 1/1\n私人资料"),
            event("", "", "模型", "推理内容", "回复 · 1/1\n无关联"),
        ])
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["thinking"], [])

    def test_post_delivery_relationship_review_is_not_a_planner_round(self):
        turns = project_turns([
            event("qq1", "QQ", "QQ", "消息", "private:0:964692788 · 你好"),
            event("qq1", "QQ", "模型", "规划结果", '{"action":"reply"}'),
            event("qq1", "QQ", "QQ", "回复", "private:0:964692788 · 你好"),
            event("qq1", "QQ", "模型", "推理内容", "思考 · 1/1\n关系候选分析"),
            event("qq1", "QQ", "模型", "规划结果", '{"event_kind":"none"}'),
        ])
        self.assertEqual(turns[0]["round_count"], 1)
        self.assertEqual(len(turns[0]["thinking"]), 1)

    def test_planner_rounds_show_host_action_after_a_wait(self):
        turns = project_turns([
            event("qq1", "QQ", "QQ", "消息", "private:0:964692788 · 我还没说完"),
            event("qq1", "QQ", "模型", "推理内容", "思考 · 1/1\n用户可能还要补充"),
            event("qq1", "QQ", "模型", "规划结果", '{"action":"wait"}'),
            event("qq1", "QQ", "QQ", "思考V2", "private:0:964692788 · 行为：wait · 意图：继续说"),
            event("qq1", "QQ", "QQ", "等待V2", "private:0:964692788 · 最多等待 4 秒"),
            event("qq2", "QQ", "QQ", "消息", "private:0:964692788 · 我还没说完，接着说"),
            event("qq2", "QQ", "模型", "规划结果", '{"action":"reply"}'),
            event("qq2", "QQ", "QQ", "思考V2", "private:0:964692788 · 行为：reply · 意图：回答"),
            event("qq2", "QQ", "QQ", "回复", "private:0:964692788 · 好，接着说。"),
        ])
        self.assertEqual(turns[0]["status"], "等待补充")
        self.assertEqual(turns[0]["decision"], "等待补充")
        self.assertTrue(any("第1轮思考" in label for label, _ in turns[0]["thinking"]))
        self.assertEqual(turns[1]["round_count"], 2)
        self.assertEqual(turns[1]["decision"], "立即回复")
        self.assertTrue(any("第2轮结论 · 立即回复" == label for label, _ in turns[1]["thinking"]))

    def test_user_requested_delay_is_shown_as_timed_wait(self):
        turns = project_turns([
            event("first", "QQ", "QQ", "消息", "private:0:1 · 一分钟后再回复"),
            event("first", "QQ", "模型", "规划结果", '{"action":"wait"}'),
            event("first", "QQ", "QQ", "思考V2", "private:0:1 · 行为：wait · 意图：稍后回复"),
            event("first", "QQ", "QQ", "等待V2", "private:0:1 · 实际等待 60 秒 · 用户指定延迟"),
            event("second", "QQ", "QQ", "消息", "private:0:1 · 一分钟后再回复"),
            event("second", "QQ", "模型", "规划结果", '{"action":"reply"}'),
            event("second", "QQ", "QQ", "思考V2", "private:0:1 · 行为：reply · 意图：现在回应"),
            event("second", "QQ", "QQ", "回复", "private:0:1 · 现在回应。"),
        ])
        self.assertEqual(turns[0]["status"], "定时等待")
        self.assertEqual(turns[0]["decision"], "定时等待")
        self.assertTrue(any(label == "第1轮结论 · 定时等待" for label, _ in turns[0]["thinking"]))
        self.assertTrue(any("实际等待 60 秒" in body for _, body in turns[0]["thinking"]))
        self.assertEqual(turns[1]["round_count"], 2)

    def test_host_stop_is_visible_without_reply(self):
        turns = project_turns([
            event("qq1", "QQ", "QQ", "消息", "private:0:964692788 · 先不用回"),
            event("qq1", "QQ", "模型", "规划结果", '{"action":"stop"}'),
            event("qq1", "QQ", "QQ", "思考V2", "private:0:964692788 · 行为：stop · 意图：不打扰"),
            event("qq1", "QQ", "QQ", "静默V2", "private:0:964692788 · 已授权不回复本批消息"),
        ])
        self.assertEqual(turns[0]["decision"], "本轮不回复")
        self.assertEqual(turns[0]["status"], "本轮不回复")

    def test_reminder_creation_and_delivery_are_separate_from_wait(self):
        creation_events = [
            event("qq1", "QQ", "QQ", "消息", "private:0:42 · 一分钟后提醒我喝水"),
            event("qq1", "QQ", "QQ", "定时已创建",
                  "private:0:42 · 提醒 · task1 · 约 60 秒后到期"),
            event("qq1", "QQ", "QQ", "回复", "private:0:42 · 好，60 秒后提醒你：喝水"),
        ]
        self.assertEqual(project_turns(creation_events)[0]["status"], "定时已创建")
        turns = project_turns(creation_events + [
            event("qq1", "QQ", "QQ", "定时生成开始",
                  "private:0:42 · reminder · 创建时 2 条 · 近期 1 条"),
            event("qq1", "QQ", "QQ", "定时已生成",
                  "private:0:42 · reminder · 15 字"),
            event("qq1", "QQ", "QQ", "提醒已送达", "private:0:42 · 提醒你：喝水")])
        self.assertEqual(turns[0]["status"], "提醒已送达")
        self.assertEqual(turns[0]["decision"], "已创建定时任务")
        self.assertIn("提醒你：喝水", turns[0]["reply_parts"])
        self.assertFalse(any(label == "等待计时" for label, _ in turns[0]["thinking"]))
        self.assertTrue(any(label == "到期生成" for label, _ in turns[0]["thinking"]))

    def test_two_planner_calls_in_one_turn_keep_separate_rounds(self):
        turns = project_turns([
            event("qq1", "QQ", "QQ", "消息", "private:0:964692788 · 查一下这个"),
            event("qq1", "QQ", "模型", "推理内容", "思考 · 1/1\n先找资料"),
            event("qq1", "QQ", "模型", "规划结果", '{"action":"tool"}'),
            event("qq1", "QQ", "模型", "推理内容", "思考 · 1/1\n资料已返回"),
            event("qq1", "QQ", "模型", "规划结果", '{"action":"reply"}'),
            event("qq1", "QQ", "QQ", "思考V2", "private:0:964692788 · 行为：reply · 意图：说明"),
        ])
        self.assertEqual(turns[0]["round_count"], 2)
        self.assertTrue(any(label == "第2轮思考 · Planner 规划"
                            for label, _ in turns[0]["thinking"]))
        self.assertEqual(turns[0]["decision"], "立即回复")

    def test_confirmed_planner_rounds_show_model_reasoning_and_answer_together(self):
        import json
        turns = project_turns([
            event("qq1", "QQ", "QQ", "消息", "private:0:1 · 查一下"),
            event("qq1", "QQ", "模型", "推理内容", "思考 · 1/1\n先查资料"),
            event("qq1", "QQ", "模型", "规划结果", '{"action":"tool"}'),
            event("qq1", "QQ", "模型", "规划确认", json.dumps({
                "model": "planner-model-a", "answer": '{"action":"tool"}'})),
            event("qq1", "QQ", "模型", "规划尝试", "failed-model"),
            event("qq1", "QQ", "模型", "推理内容", "思考 · 1/1\n失败候选的推理"),
            event("qq1", "QQ", "模型", "规划尝试", "planner-model-b"),
            event("qq1", "QQ", "模型", "规划结果", "无效的中间输出"),
            event("qq1", "QQ", "模型", "规划确认", json.dumps({
                "model": "planner-model-b", "answer": '{"action":"reply"}'})),
            event("qq1", "QQ", "QQ", "思考V2", "private:0:1 · 行为：reply · 意图：说明"),
        ])
        sections = thought_sections(turns[0]["thinking"])
        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0][1], "Planner · 第1轮 · planner-model-a")
        self.assertEqual(sections[1][1], "Planner · 第2轮 · planner-model-b")
        self.assertIn("思考：\n先查资料\n\n回答：\n", sections[0][3])
        self.assertIn('{"action":"reply"}', sections[1][3])
        self.assertIn("模型未返回可展示的独立推理", sections[1][3])
        self.assertNotIn("无效的中间输出", str(sections))
        self.assertNotIn("失败候选的推理", str(sections))


if __name__ == "__main__":
    unittest.main()
