"""Small, local emotional-context model shared by text, voice, and QQ chat.

This is an approximate conversation cue, not a diagnosis or a claim that the
assistant experiences human emotions. It deliberately stores no raw utterances.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace


_SOLVED = re.compile(r"解决了|修好了|搞定了|成功了|完成了|通过了|终于弄好了|终于好了")
_JOY = re.compile(r"好开心|很开心|高兴|好消息|太棒了|真棒|惊喜|值得庆祝")
_TROUBLE = re.compile(r"(?i:bug)|报错|出错|卡住了|没进展|搞不定|找不到问题|失败了|怎么都不行|烦死了")
_EXHAUSTED = re.compile(r"好累|很累|累死|累了|疲惫|压力大|撑不住|心累|难受")
_SPACE = re.compile(r"不想聊|不想这些|换个话题|不提了|先不说|陪我聊聊天")
_CODING = re.compile(r"(?i:bug|代码|qt|c\+\+|python)|程序|编译|调试")
_STUDY = re.compile(r"考研|复习|备考|刷题|学习")


@dataclass(frozen=True)
class EmotionState:
    """Immutable, per-conversation state. Commit only after a successful reply."""

    mood: str = "平静"
    intensity: float = 0.15
    user_signal: str = "尚无明显线索"
    focus: str = ""
    unresolved: bool = False
    idle_turns: int = 0

    def observe(self, user_text: str) -> "EmotionState":
        """Propose a new state without changing the state already committed."""
        text = user_text.strip()
        if not text:
            return self

        topic = "编程或调试" if _CODING.search(text) else (
            "学习或备考" if _STUDY.search(text) else "当前提到的事情"
        )
        if _SPACE.search(text):
            # Preserve the unresolved event but respect a request not to pursue it.
            return replace(self, mood="平和", intensity=0.25,
                           user_signal="用户希望暂时转移话题，不宜追问",
                           idle_turns=0)
        if _SOLVED.search(text):
            return replace(self, mood="欣喜", intensity=0.65,
                           user_signal="用户报告了一个积极进展",
                           focus="用户刚报告问题得到解决或取得进展",
                           unresolved=False, idle_turns=0)
        if _JOY.search(text):
            return replace(self, mood="愉快", intensity=0.55,
                           user_signal="用户表达了积极情绪",
                           focus=topic if topic != "当前提到的事情" else self.focus,
                           idle_turns=0)
        if _TROUBLE.search(text):
            return replace(self, mood="关切", intensity=0.55,
                           user_signal="用户可能正因困难感到挫折",
                           focus=topic, unresolved=True, idle_turns=0)
        if _EXHAUSTED.search(text):
            return replace(self, mood="关切", intensity=0.45,
                           user_signal="用户可能感到疲惫或有压力",
                           focus=topic, unresolved=self.unresolved, idle_turns=0)

        # Gradually return to neutral and eventually release stale topics.
        idle = self.idle_turns + 1
        strength = max(0.15, round(self.intensity * 0.7, 2))
        return replace(self,
                       mood="平静" if strength <= 0.25 else self.mood,
                       intensity=strength,
                       user_signal="本轮没有明显的新情绪线索",
                       focus="" if idle >= 5 else self.focus,
                       unresolved=False if idle >= 5 else self.unresolved,
                       idle_turns=idle)

    def to_prompt(self) -> str:
        """Return only fixed, bounded labels, never original user messages."""
        progress = "有待跟进（仅限当前会话）" if self.unresolved else "无"
        return (
            "## 当前对话情绪上下文（程序的粗略推断，不是事实或诊断）\n"
            f"模拟角色心境：{self.mood}；强度：{self.intensity:.2f}\n"
            f"本轮用户信号：{self.user_signal}\n"
            f"目前关注主题：{self.focus or '无'}；尚未解决的问题：{progress}\n"
            "这些线索只用于调整语气和关注点。优先理解本轮用户的真实意图；"
            "不要机械附和情绪、重复安慰、强行提及旧话题、输出标签或强度数字。"
            "尊重用户转移话题或希望安静的选择。"
            "不得把推断说成亲历的共同记忆，不得声称自己具有真实的人类感受。"
        )
