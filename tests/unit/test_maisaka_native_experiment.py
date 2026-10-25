"""Offline contract tests for the pinned, isolated upstream MaiBot experiment."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "maisaka_native_experiment.py"
SPEC = importlib.util.spec_from_file_location("maisaka_native_experiment", SCRIPT)
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


class UpstreamNativeLauncherTests(unittest.TestCase):
    def test_missing_source_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(RuntimeError, "尚未初始化"):
                launcher.verify_source(Path(td))

    def test_no_overlap_with_production_data(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            source = root / "third_party" / "MaiBot"
            for forbidden in (root, root / "private_state", root / "conversation_data",
                              root / "project_data" / "evidence", source / "data"):
                with self.assertRaises(ValueError):
                    launcher.safe_instance_path(root, source, forbidden)
            isolated = root.parent / "maisaka-outside"
            self.assertEqual(launcher.safe_instance_path(root, source, isolated),
                             isolated.resolve())
            # Default experiment data can live in repository root, unlike
            # credential/production database subtrees.
            default = root / ".maisaka-experiment"
            self.assertEqual(launcher.safe_instance_path(root, source, default),
                             default.resolve())

    def test_requires_channel_confirmation_before_run(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(launcher, "verify_source"), patch.object(
                launcher, "SOURCE", Path(td) / "MaiBot"
            ):
                with self.assertRaises(SystemExit) as raised:
                    launcher.main(["run", "--instance", str(Path(td) / "instance")])
                self.assertEqual(raised.exception.code, 2)

    def test_isolated_init_runs_upstream_cli_not_zhuangfy(self):
        with tempfile.TemporaryDirectory() as td:
            instance = Path(td) / "test-instance"
            upstream = Path(td) / "upstream"
            with patch.object(launcher, "verify_source"), patch.object(
                launcher, "SOURCE", upstream
            ), patch.object(launcher.subprocess, "call", return_value=0) as launch:
                self.assertEqual(launcher.main(
                    ["init", "--instance", str(instance), "--python", "python"]), 0)
                args, kwargs = launch.call_args
                self.assertEqual(args[0][-4:], ["src.cli.tool", "init", "--directory", str(instance.resolve())])
                self.assertEqual(kwargs["cwd"], upstream)
                self.assertEqual(kwargs["env"]["MAIBOT_INSTANCE_DIR"], str(instance.resolve()))

    def test_upstream_pin_is_immutable_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for p in ("src/maisaka/reasoning_engine.py", "src/maisaka/runtime.py",
                      "src/maisaka/chat_loop_service.py",
                      "src/maisaka/builtin_tool/wait.py",
                      "src/maisaka/builtin_tool/stop.py",
                      "src/maisaka/builtin_tool/reply.py",
                      "src/chat/image_system/image_manager.py",
                      "prompts/zh-CN/image_description.prompt"):
                target = root / p
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("# stub", encoding="utf-8")
            completed = subprocess.CompletedProcess(["git"], 0, stdout="f" * 40 + "\n")
            with patch.object(launcher.subprocess, "run", return_value=completed):
                with self.assertRaisesRegex(RuntimeError, "上游版本漂移"):
                    launcher.verify_source(root)


if __name__ == "__main__":
    unittest.main()
