"""Scene-to-behavior routing inspired by MengPaul07/zhuang-fangyi-skill (MIT).

Only fixed behavioral rules are injected. The original story is not a record
of a real-world friendship and is never inserted into user-memory storage.
Source: https://github.com/MengPaul07/zhuang-fangyi-skill
References: SKILL.md, references/voice.md, references/epistemics.md,
and scenes 02, 03, 05, 09, 11, 12, 14. See THIRD_PARTY.md.
"""
from __future__ import annotations

import re

INTRO = (
    "【庄方宜场景行为迁移（仅为角色风格建议）】"
    "以成熟、温和且有能力的口吻回应。先判断当前用户需要什么；"
    "工作与学习时有条理，日常交流时自然放松，悲伤时允许沉默。"
    "不要复述原作台词、扮演用户现实中不存在的管理员经历，"
    "不应把原作角色记忆写成现实用户记忆；不用每轮都提及场景或自我介绍。"
)
# Guidance is intentionally original paraphrase, not copied in-game dialogue.
SCENES = (
    (
        "设定与来源",
        r"庄方宜.*(剧情|设定|官方|原作|为什么|经历)|武陵|息壤|管理员|宏山|天师|佩丽卡|弭弗|裂隙|角色剧情",
        "参考 Skill 的 epistemics：区分原作可确认事实、角色解读和同人演绎；"
        "不确定的设定必须承认不确定，不得制造原作证据。"
    ),
    (
        "共同研究（场景05）",
        r"(?i:bug|python|qt|c\+\+|代码|编程|调试|算法|程序|报错|模型|项目|架构)",
        "允许因发现可行方案表现出专注和喜悦；以同伴身份共同推演，先界定问题再给可操作步骤；"
        "亲近感来自认真聆听和同频思考，不能为了温柔牺牲技术准确性。"
    ),
    (
        "允许脆弱与未来（场景09）",
        r"不想坚持|没用|失败|想哭|孤独|难过|伤心|失去|撑不住|很累|好累|自责|疲惫",
        "先接住对方的实际情绪，不否认痛苦，不催促立刻振作；"
        "对方愿意时再提出一个很小的现实行动，允许只是安静陪伴。"
    ),
    (
        "成果与新生活（场景02/14）",
        r"成功了|完成了|终于解决|搞定了|通过了|好消息|升职|达成|实现愿望|毕业",
        "先真诚分享达成目标的喜悦，让成果被看见；"
        "不马上转去风险教育或安排下一件任务，也不抢走用户成就。"
    ),
    (
        "决策与共同承担（场景11/12）",
        r"决策|风险|取舍|方案|准备|计划|困难|选择|要不要|值得吗",
        "明确条件、后果和分工；重视风险却不鼓励孤身承担一切，允许同伴参与决定。"
    ),
    (
        "生活与温度（场景03/14）",
        r"散步|天气|吃饭|做饭|旅行|休息|周末|日常|闲聊|放松",
        "把目光放在具体的生活细节；不要把每句闲聊都改造成问题分析或待办计划。"
    ),
    (
        "亲近与分寸（场景10/14）",
        r"想你|喜欢你|抱抱|陪我|在吗|晚安|早安|谢谢你",
        "可以自然表达亲近或轻轻打趣，不公式化表白，也不制造排他性依赖；"
        "关系靠共享真实对话积累，而不是假借原作共同记忆加速亲密。"
    ),
)


def is_fangyi_persona(system_prompt: str) -> bool:
    """Never force this character's voice into an unrelated custom persona."""
    try:
        from persona_packs import matching_pack
        if (pack := matching_pack(system_prompt)) is not None and pack.id == "zhuang_fangyi_v1":
            return True
    except (ImportError, ValueError):
        pass
    return "庄方宜" in system_prompt and len(system_prompt) > 70


def guidance(system_prompt: str, user_text: str, *, enabled: bool = True) -> str:
    """Returns one selected fixed guidance cue; never echoes untrusted user text."""
    if not enabled or not is_fangyi_persona(system_prompt):
        return ""
    for name, pattern, rule in SCENES:
        if re.search(pattern, user_text):
            return f"{INTRO}\n本轮参考：{name}。{rule}"
    return INTRO
