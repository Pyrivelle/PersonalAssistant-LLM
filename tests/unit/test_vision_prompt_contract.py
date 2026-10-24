"""Guard the distinction between image subjects and the message sender.

These are prompt-contract tests, not substitutes for QQ/VLM behavioral tests.
"""
import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def vision_prompt() -> str:
    source = (ROOT / "SenseVoice" / "qq_bridge.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "VISION_PROMPT"
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("VISION_PROMPT not found")


class VisionPromptContractTests(unittest.TestCase):
    def test_image_observation_does_not_attribute_identity_or_emotion(self):
        prompt = vision_prompt()
        self.assertIn("第三人称", prompt)
        self.assertIn("不要假定图片人物就是用户", prompt)
        self.assertIn("不等同于用户", prompt)

    def test_replyer_context_preserves_sender_boundary(self):
        provider = (ROOT / "src" / "zhuangfy" / "llm" / "providers.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("视觉观察来自独立识图模块", provider)
        self.assertIn("不能自动归属于用户", provider)
        self.assertIn("只有用户亲自说出的情况", provider)
        self.assertIn("明确要求识别或翻译时回答图片内容", provider)


if __name__ == "__main__":
    unittest.main()
