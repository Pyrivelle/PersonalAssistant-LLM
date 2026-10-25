import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zhuangfy.core.recent_context import RecentContextStore, desktop_history
from zhuangfy.engine_fusion.shared_context import SharedContextProvider, VerifiedPrivateQQ


class RecentContextTests(unittest.TestCase):
    def test_desktop_and_qq_share_only_one_verified_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RecentContextStore(Path(directory) / "recent.sqlite3")
            owner = "qq:456:private:123"
            other = "qq:456:private:999"
            store.append(owner, "voice", "user", "我刚到家", "voice-u")
            store.append(owner, "voice", "assistant", "欢迎回来", "voice-a")
            store.append(owner, "qq", "user", "刚才说了什么？", "qq-u")
            store.append(owner, "qq", "assistant", "你说刚到家", "qq-a")
            store.append(other, "qq", "user", "测试人员的私聊", "other-u")
            store.append(owner, "qq", "assistant", "你说刚到家", "qq-a")
            self.assertEqual(len(store.history(owner)), 4)
            self.assertEqual(len(store.history(other)), 1)
            excerpt = store.external_excerpt(owner)
            self.assertIn("我刚到家", excerpt)
            self.assertNotIn("刚才说了什么", excerpt)
            self.assertNotIn("测试人员", excerpt)

    def test_desktop_uses_shared_history_only_when_owner_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RecentContextStore(Path(directory) / "recent.sqlite3")
            owner = "qq:456:private:123"
            store.append(owner, "qq", "user", "今天聊三维渲染", "qq-u")
            store.append(owner, "qq", "assistant", "好，我们接着聊", "qq-a")
            with patch("zhuangfy.core.recent_context.owner_scope", return_value=owner), patch(
                "zhuangfy.core.recent_context.RecentContextStore", return_value=store
            ):
                result = desktop_history([{"role": "user", "content": "旧的本机记录"}])
            self.assertEqual(result[-1]["content"], "好，我们接着聊")
            self.assertNotIn("旧的本机记录", str(result))

    def test_shared_provider_never_projects_other_qq_history(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RecentContextStore(Path(directory) / "recent.sqlite3")
            owner = "qq:456:private:123"
            store.append(owner, "text", "user", "我在写 Qt 程序", "text-u")

            class Identity:
                def resolve(self, _identity): return "person-owner"
                def has_consent(self, *_args): return True

            class Settings:
                allowed_private_ids = frozenset({123, 999})
                allow_all_private = False

            provider = SharedContextProvider(qq_settings=Settings(),
                                             identity_repository=Identity())
            with patch("zhuangfy.core.recent_context.owner_scope", return_value=owner), patch(
                "zhuangfy.core.recent_context.RecentContextStore", return_value=store
            ):
                own = provider.prepare(VerifiedPrivateQQ(456, 123), persona="庄方宜")
                other = provider.prepare(VerifiedPrivateQQ(456, 999), persona="庄方宜")
            self.assertIn("Qt 程序", own.recent_context)
            self.assertEqual(other.recent_context, "")


if __name__ == "__main__":
    unittest.main()
