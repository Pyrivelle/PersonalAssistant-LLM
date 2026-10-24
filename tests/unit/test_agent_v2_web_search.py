"""Offline Brave Web Search Skill tests. Never call a real search provider."""
from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import URLError

from zhuangfy.agent_v2.discovery import SkillDiscovery
from zhuangfy.agent_v2.runtime import AuthorizedToolRegistry, ToolBinding
from zhuangfy.agent_v2.skills import SkillManager, SkillPolicy, build_skill_runtime
from zhuangfy.agent_v2.skill_access import SkillPrincipal
from zhuangfy.agent_v2.web_search_access import restrict_web_search_for_qq
from zhuangfy.agent_v2.web_search_provider import (
    API_URL, MAX_OUTPUT, BraveSearch, SearchUnavailable, _NoRedirects,
)


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, n):
        return self.body[:n]


class FakeOpener:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        if self.error:
            raise self.error
        return FakeResponse(self.response)


def sample_json():
    return (b'{"web":{"results":['
            b'{"title":"Official docs","url":"https://docs.example.org/reference",'
            b'"description":"Reference and release notes"},'
            b'{"title":"Latest release","url":"https://example.org/releases",'
            b'"description":"Version history"},'
            b'{"title":"Bad redirect","url":"http://127.0.0.1/admin",'
            b'"description":"do not show"}]}}')


class BraveAdapterTests(unittest.TestCase):
    def test_fixed_api_sources_and_bounded_network(self):
        api = BraveSearch("mock-key-1234567890")
        mock = FakeOpener(response=sample_json())
        api._opener = mock
        text = api.search("OSG project latest release")
        self.assertIn("Official docs", text)
        self.assertIn("https://docs.example.org/reference", text)
        self.assertIn("https://example.org/releases", text)
        self.assertNotIn("127.0.0.1", text)
        self.assertLessEqual(len(text), MAX_OUTPUT)
        req, timeout = mock.calls[0]
        self.assertTrue(req.full_url.startswith(API_URL + "?"))
        self.assertEqual(timeout, 5.0)
        self.assertIn("q=OSG+project+latest+release", req.full_url)
        self.assertNotIn("mock-key-1234567890", req.full_url)
        self.assertEqual(req.get_header("X-subscription-token"), "mock-key-1234567890")
        self.assertIsNone(_NoRedirects().redirect_request(None, None, 302, "",
                                                           {}, "http://127.0.0.1"))

    def test_private_queries_never_go_to_provider(self):
        api = BraveSearch("mock-key-1234567890")
        mock = FakeOpener(response=sample_json())
        api._opener = mock
        for query in ("", "s", "x" * 221, "search\nhidden",
                      "call my phone 13800138000",
                      "alice@example.com", "Bearer secret-tok"):
            with self.subTest(query=query), self.assertRaises(ValueError):
                api.search(query)
        self.assertEqual(mock.calls, [])

    def test_rate_limits_failures_and_no_source(self):
        api = BraveSearch("mock-key-1234567890", max_requests_per_minute=1)
        mock = FakeOpener(response=sample_json())
        api._opener = mock
        api.search("test search")
        with self.assertRaises(SearchUnavailable):
            api.search("another query")
        self.assertEqual(len(mock.calls), 1)

        failing = BraveSearch("mock-key-1234567890")
        failing._opener = FakeOpener(error=URLError("secret-key-in-error"))
        with self.assertRaises(SearchUnavailable) as err:
            failing.search("public info")
        self.assertNotIn("secret-key-in-error", str(err.exception))
        no_results = BraveSearch("mock-key-1234567890")
        no_results._opener = FakeOpener(
            response=b'{"web":{"results":[{"title":"Bad","url":"http://127.0.0.1"}]}}')
        self.assertIn("没有返回可用", no_results.search("safe public info"))
        oversized = BraveSearch("mock-key-1234567890")
        oversized._opener = FakeOpener(response=b"x" * 65537)
        with self.assertRaises(SearchUnavailable):
            oversized.search("public knowledge")


class SearchActivationTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_skill_activation_key_and_deferred_discovery(self):
        root = Path(__file__).resolve().parents[2] / "SenseVoice" / "skills"
        self.assertTrue((root / "web_search" / "skill.json").is_file())
        with patch.dict(os.environ, {"ZHUANGFY_BRAVE_SEARCH_API_KEY": ""}):
            statuses = SkillManager(root, {"web_search": SkillPolicy(enabled=True)}).discover()
            entry = next(x for x in statuses if x.id == "web_search")
            self.assertTrue(entry.enabled)
            self.assertFalse(entry.loaded)
            registry = AuthorizedToolRegistry()
            mgr = SkillManager(root, {"web_search": SkillPolicy(enabled=True)})
            mgr.activate(registry)
            status = next(x for x in mgr.statuses if x.id == "web_search")
            self.assertFalse(status.loaded)
            self.assertNotIn("web_search.search", registry.public_names(scope="private"))
        with patch.dict(os.environ, {"ZHUANGFY_BRAVE_SEARCH_API_KEY": "mock-key-1234567890"}):
            registry = AuthorizedToolRegistry()
            mgr = SkillManager(root, {"web_search": SkillPolicy(enabled=True)})
            mgr.activate(registry)
        self.assertTrue(next(x for x in mgr.statuses if x.id == "web_search").loaded)
        self.assertNotIn("web_search.search",
                         SkillDiscovery(registry).select("你好，今天怎么样", scope="private").names)
        self.assertIn("web_search.search",
                      SkillDiscovery(registry).select("帮我联网搜索最新 OSG 发布", scope="private").names)
        self.assertNotIn("web_search.search",
                         registry.public_names(scope="group"))

    async def test_per_qq_user_grant_filters_visibility_and_execution(self):
        async def search(args):
            return "search ok"
        registry = AuthorizedToolRegistry()
        registry.register(ToolBinding(
            "web_search.search", search, frozenset({"private"}),
            description="public search", visibility="deferred",
            discovery_terms=("联网", "搜索"),
            parameters={"type":"object","properties":{"query":{"type":"string"}},
                        "required":["query"],"additionalProperties":False},
        ))
        a = SkillPrincipal.verified_qq_private(bot_id=88, user_id=42)
        b = SkillPrincipal.verified_qq_private(bot_id=88, user_id=43)
        c = SkillPrincipal.verified_qq_private(bot_id=89, user_id=42)
        raw = "88:42,89:100"
        for principal in (b, c, None):
            restricted = restrict_web_search_for_qq(
                registry, principal=principal, allowed_pairs=raw)
            self.assertNotIn("web_search.search", restricted.public_names(scope="private"))
            denied = await restricted.invoke("web_search.search", {"query":"OSG"},
                                             scope="private")
            self.assertFalse(denied.success)
        permitted = restrict_web_search_for_qq(
            registry, principal=a, allowed_pairs=raw)
        self.assertIn("web_search.search", permitted.public_names(scope="private"))
        self.assertTrue((await permitted.invoke(
            "web_search.search", {"query":"OSG"}, scope="private")).success)
        self.assertNotIn("web_search.search",
                         restrict_web_search_for_qq(
                             registry, principal=a, allowed_pairs="").public_names(
                                 scope="private"))
        self.assertNotIn("web_search.search",
                         restrict_web_search_for_qq(
                             registry, principal=a,
                             allowed_pairs="89:42").public_names(scope="private"))
