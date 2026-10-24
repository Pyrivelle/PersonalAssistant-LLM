"""Static wiring checks for PR53's QQ delivery and erasure paths.

Behavior tests for the repositories/storage live in their respective modules.
These checks guard integration points that are difficult to exercise without QQ.
"""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BRIDGE = ROOT / "SenseVoice" / "qq_bridge.py"


class QqEvidenceIntegrationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = BRIDGE.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_partial_reply_keeps_evidence_across_continue(self):
        source = self.source
        self.assertIn("visual_evidence: str", source)
        self.assertIn("tool_evidence: str", source)
        self.assertIn("evidence_scope: str", source)
        self.assertIn("delivery.visual_evidence", source)
        self.assertIn("delivery.tool_evidence", source)
        self.assertIn("await self._finish_reply(state, current, partial)", source)

    def test_forget_me_erases_new_derivative_stores(self):
        source = self.source
        start = source.index('message.strip() in {"/forget_me"')
        finish = source.index('await self.send_reply(current, "已清除', start)
        erase_section = source[start:finish]
        self.assertIn("EvidenceStore", erase_section)
        self.assertIn("ImageMeaningStore", erase_section)
        self.assertIn("VisionDescriptionStore", erase_section)
        self.assertIn("erase_conversation(erase_arg)", erase_section)

    def test_context_builder_reserves_room_for_recall(self):
        source = self.source
        self.assertIn("prior_budget = 750 if current.kind", source)
        self.assertIn("notes = notes[-prior_budget:]", source)


if __name__ == "__main__":
    unittest.main()
