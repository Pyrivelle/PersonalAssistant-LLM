"""Cloud E1: explicit secrets, pinned service, and no unintended network calls."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from zhuangfy.persona.packs import ZHUANG_FANGYI_V1

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from character_e1_capture import capture


class E1CloudDispatchTests(unittest.TestCase):
    def test_cloud_capture_requires_network_optin_even_when_secret_exists(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(
            os.environ, {"ZHUANGFY_E1_API_KEY": "demo-secret"}):
            args = ["--variant", "ccv2", "--max-cases", "1",
                    "--cloud-provider", "deepseek", "--cloud-model", "test-model",
                    "--out", str(Path(d) / "capture.json")]
            with self.assertRaises(SystemExit) as ctx:
                capture(args)
            self.assertEqual(ctx.exception.code, 2)
            self.assertFalse((Path(d) / "capture.json").exists())

    def test_missing_actions_secret_fails_before_any_model_calls(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(
            os.environ, {"ZHUANGFY_E1_API_KEY": ""}):
            args = ["--variant", "ccv2", "--max-cases", "1",
                    "--cloud-provider", "deepseek", "--cloud-model", "test-model",
                    "--out", str(Path(d) / "capture.json"), "--allow-network"]
            with self.assertRaises(SystemExit) as ctx:
                capture(args)
            self.assertEqual(ctx.exception.code, 2)

    def test_cloud_capture_uses_exact_requested_model_and_builtin_persona(self):
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "model_capture.json"
            calls = []

            def stub(stage, settings, key, history, message, **kwargs):
                calls.append((stage, settings, key, history, message, kwargs))
                return "这是模拟服务的回复，不代表真实角色成绩。"

            with patch.dict(os.environ, {"ZHUANGFY_E1_API_KEY": "test-secret-value"}), \
                 patch.dict(sys.modules, {"model_routes": SimpleNamespace(invoke_stage=stub)}):
                self.assertEqual(capture([
                    "--variant", "ccv2_ckv1", "--max-cases", "3",
                    "--cloud-provider", "deepseek", "--cloud-model", "manual-test-model",
                    "--out", str(dest), "--allow-network",
                ]), 0)
            self.assertEqual(len(calls), 3)
            for stage, settings, key, history, msg, params in calls:
                self.assertEqual(stage, "reply")
                self.assertEqual(settings.provider, "deepseek")
                self.assertEqual(settings.model, "manual-test-model")
                self.assertEqual(settings.base_url, "https://api.deepseek.com")
                self.assertEqual(settings.system_prompt, ZHUANG_FANGYI_V1.prompt())
                self.assertFalse(settings.memory_enabled)
                self.assertEqual(key, "test-secret-value")
                self.assertEqual(history, [])
                self.assertIsNotNone(params["routes"])
                self.assertIn("Character Core V2", params["emotion_context"])
            result = json.loads(dest.read_text(encoding="utf-8"))
            self.assertEqual(result["source"], "model_capture")
            self.assertEqual(result["capture_info"]["configured_model"], "manual-test-model")
            self.assertEqual(len(result["responses"]), 3)
            self.assertNotIn("test-secret-value", dest.read_text(encoding="utf-8"))
            self.assertNotIn("api_key", dest.read_text(encoding="utf-8").lower())

    def test_invalid_provider_or_missing_model_never_reach_service(self):
        with tempfile.TemporaryDirectory() as d:
            args = ["--variant","v1","--cloud-provider","custom",
                    "--cloud-model","unknown","--out",str(Path(d)/"e1.json"),
                    "--allow-network"]
            with self.assertRaises(SystemExit) as ctx:
                capture(args)
            self.assertEqual(ctx.exception.code,2)
            args[args.index("custom")] = "deepseek"
            args.remove("--cloud-model")
            args.remove("unknown")
            with self.assertRaises(SystemExit) as ctx:
                capture(args)
            self.assertEqual(ctx.exception.code,2)


if __name__ == "__main__":
    unittest.main()
