"""The stream keeps interleaved chat turns distinct and hides background work."""

import unittest

from chat_stream import (conversation_id, conversation_title, project_turns,
                         qq_display_names, thought_sections)


def event(turn, channel, source, phase, detail):
    return {"turn_id": turn, "channel": channel, "source": source,
            "phase": phase, "detail": detail, "time": "2026-10-22T18:48:48+08:00"}


class ChatStreamTests(unittest.TestCase):
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

    def test_host_stop_is_visible_without_reply(self):
        turns = project_turns([
            event("qq1", "QQ", "QQ", "消息", "private:0:964692788 · 先不用回"),
            event("qq1", "QQ", "模型", "规划结果", '{"action":"stop"}'),
            event("qq1", "QQ", "QQ", "思考V2", "private:0:964692788 · 行为：stop · 意图：不打扰"),
            event("qq1", "QQ", "QQ", "静默V2", "private:0:964692788 · 已授权不回复本批消息"),
        ])
        self.assertEqual(turns[0]["decision"], "本轮不回复")
        self.assertEqual(turns[0]["status"], "本轮不回复")

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


if __name__ == "__main__":
    unittest.main()
