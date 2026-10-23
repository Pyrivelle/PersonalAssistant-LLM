"""R1 evaluation variant must apply synthetic tier without real user memory."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from character_e1_capture import capture


class R1ModelCaptureTests(unittest.TestCase):
    def test_synthetic_relationship_tiers_used_only_with_r1_variant(self):
        with tempfile.TemporaryDirectory() as d:
            output = Path(d) / "r1.json"
            calls = []
            def reply(*args, **kwargs):
                calls.append((args[4], kwargs["emotion_context"]))
                return "用于测试的模拟回答"
            with patch.dict(os.environ, {"ZHUANGFY_E1_API_KEY": "testing-secret"}), \
                 patch.dict(sys.modules, {"model_routes": SimpleNamespace(invoke_stage=reply)}):
                result = capture(["--variant", "ccv2_ckv1_r1", "--cloud-provider",
                                  "siliconflow", "--cloud-model", "test-model",
                                  "--max-cases", "3", "--out", str(output), "--allow-network"])
            self.assertEqual(result, 0)
            self.assertEqual(len(calls), 3)
            self.assertIn("R1-A", calls[0][1])
            self.assertIn("熟悉：", calls[0][1])
            self.assertIn("信任：", calls[1][1])
            self.assertIn("初识：", calls[2][1])
            self.assertIn("实际沟通，不写括号动作", calls[1][1])
            data = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(data["variant"], "ccv2_ckv1_r1")
            self.assertNotIn("testing-secret", output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
