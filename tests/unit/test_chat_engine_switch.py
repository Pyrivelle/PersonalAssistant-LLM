"""The two-engine switch is explicit, reversible and preserves original QQ."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[2] / "scripts" / "chat_engine.py"
SPEC = importlib.util.spec_from_file_location("chat_engine_launcher", SOURCE)
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


class ChatEngineSwitchTests(unittest.TestCase):
    def test_default_is_existing_zhuangfy(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(launcher.read_config(Path(folder) / "none.json")["engine"],
                             "zhuangfy")

    def test_config_switch_is_reversible_without_touching_memory(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = Path(folder) / "selected.json"
            launcher.save_config("maibot", maibot_python="own-venv/python", path=cfg)
            self.assertEqual(launcher.read_config(cfg)["engine"], "maibot")
            self.assertEqual(launcher.read_config(cfg)["maibot_python"], "own-venv/python")
            launcher.save_config("zhuangfy", path=cfg)
            self.assertEqual(launcher.read_config(cfg)["engine"], "zhuangfy")
            self.assertEqual(json.loads(cfg.read_text(encoding="utf-8"))["engine"], "zhuangfy")
            with self.assertRaises(ValueError):
                launcher.save_config("invalid", path=cfg)

    def test_invalid_config_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = Path(folder) / "engines.json"
            cfg.write_text('{"engine":"unknown"}', encoding="utf-8")
            with self.assertRaises(ValueError):
                launcher.read_config(cfg)

    def test_launch_original_qq_bridge_not_modified(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "SenseVoice").mkdir()
            (root / "SenseVoice" / "qq_bridge.py").write_text("# original", encoding="utf-8")
            cmd = launcher.build_command("zhuangfy", root=root,
                                         python=Path(sys.executable))
            self.assertEqual(cmd, [sys.executable, str(root / "SenseVoice" / "qq_bridge.py")])

    def test_hybrid_is_reserved_but_cannot_silently_run_legacy(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(RuntimeError, "尚未通过真实 QQ 验收"):
                launcher.build_command("hybrid", root=Path(folder))
            config = Path(folder) / "engine.json"
            with patch.object(launcher, "CONFIG", config):
                with self.assertRaises(SystemExit) as raised:
                    launcher.main(["set-default", "--engine", "hybrid"])
                self.assertEqual(raised.exception.code, 2)
                self.assertFalse(config.exists())

    def test_hybrid_requires_both_explicit_preview_and_channel_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "SenseVoice").mkdir()
            (root / "SenseVoice" / "hybrid_qq_bridge.py").write_text("# test", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                launcher.build_command("hybrid", root=root, confirmed=True)
            with self.assertRaises(RuntimeError):
                launcher.build_command("hybrid", root=root, hybrid_preview=True)
            command = launcher.build_command(
                "hybrid", root=root, confirmed=True,
                hybrid_preview=True, python=Path(sys.executable))
            self.assertEqual(command[-1], "--confirm-experimental-hybrid")
            self.assertEqual(Path(command[1]), root / "SenseVoice" / "hybrid_qq_bridge.py")

    def test_maibot_launch_requires_separate_python(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaises(FileNotFoundError):
                launcher.build_command("maibot", root=root, maibot_python="missing/python")
            venv = root / "mai_venv" / "python"
            venv.parent.mkdir()
            venv.touch()
            cmd = launcher.build_command("maibot", root=root, python=Path(sys.executable),
                                         maibot_python="mai_venv/python", confirmed=True)
            self.assertEqual(cmd[0], sys.executable)
            self.assertIn("maisaka_native_experiment.py", cmd[1])
            self.assertIn("--confirm-exclusive-channel", cmd)

    def test_second_launcher_cannot_acquire_held_lock(self):
        if sys.platform == "win32":
            self.skipTest("Windows file-lock reentrancy semantics vary by process")
        with tempfile.TemporaryDirectory() as folder:
            lock = Path(folder) / "engine.lock"
            with launcher.exclusive_launcher(lock):
                with self.assertRaises(RuntimeError):
                    with launcher.exclusive_launcher(lock):
                        pass
            with launcher.exclusive_launcher(lock):
                pass

    def test_direct_maibot_run_requires_explicit_exclusive_channel(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(launcher, "CONFIG", Path(folder) / "empty.json"):
                with self.assertRaises(SystemExit) as raised:
                    launcher.main(["run", "--engine", "maibot"])
                self.assertEqual(raised.exception.code, 2)

    def test_set_default_does_not_launch_child_process(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "engine.json"
            with patch.object(launcher, "CONFIG", config):
                with patch.object(launcher.subprocess, "call") as send:
                    self.assertEqual(launcher.main(["set-default", "--engine", "maibot"]), 0)
                    send.assert_not_called()
                    self.assertEqual(launcher.read_config(config)["engine"], "maibot")


if __name__ == "__main__":
    unittest.main()
