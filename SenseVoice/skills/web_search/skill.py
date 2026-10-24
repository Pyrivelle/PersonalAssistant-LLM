"""Opt-in, read-only web_search.search Skill.

Enable it explicitly in skills_settings.json and configure a DeepSeek Search
API key through the administrator environment. This skill cannot browse a
model-selected URL or call arbitrary external hosts.
"""
from __future__ import annotations

import asyncio
import os

from zhuangfy.agent_v2.runtime import ToolBinding
from zhuangfy.agent_v2.web_search_provider import DeepSeekSearch


def setup(context):
    if context.skill_id != "web_search":
        raise ValueError("Unexpected Web Search skill identity")
    key = os.environ.get("ZHUANGFY_DEEPSEEK_API_KEY", "")
    if not key:
        raise ValueError("ZHUANGFY_DEEPSEEK_API_KEY is not configured")
    provider = DeepSeekSearch(key)

    async def search(arguments: dict) -> str:
        return await asyncio.to_thread(provider.search, arguments["query"])

    return [ToolBinding(
        name="web_search.search", handler=search,
        scopes=frozenset({"private"}), mutating=False,
        description=("仅用于查询最近更新的公开信息、新闻、官方资料或其他无法靠当前上下文回答的事实；"
                     "普通聊天无需调用。只提交公共搜索关键词，不要上传私人对话或账号数据。"
                     "返回来源仅是未经核实的网页摘要，回答必须附来源 URL。"),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string", "minLength": 2, "maxLength": 128,
                    "description": "简短的公开搜索关键词，禁止个人隐私或账户密钥",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    )]
