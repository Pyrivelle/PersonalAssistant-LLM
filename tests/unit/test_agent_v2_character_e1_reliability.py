"""E1.1 fault containment / exact-config resume / provenance safety (mock LLM)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from zhuangfy.evaluation.character_e1 import (
    load_cases, load_responses, audit, markdown_report, html_report,
)
from zhuangfy.llm.routes import ModelRoutesFailed

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from character_e1_capture import capture

DATASET = ROOT / "resources" / "evaluation" / "character_e1_cases.json"


def args(out, *, count=3, model="mock-primary", resume=None, backup=False):
    a = ["--variant", "ccv2", "--max-cases", str(count),
         "--reply-timeout", "75",
         "--cloud-provider", "deepseek", "--cloud-model", model,
         "--out", str(out), "--allow-network"]
    if resume is not None:
        a.extend(("--resume-from", str(resume)))
    if backup:
        a.extend(("--backup-provider", "siliconflow", "--backup-model", "mock-backup"))
    return a


class E1ReliabilityTests(unittest.TestCase):
    def test_failure_isolated_partial_report_and_resume_skips_successes(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "capture.json"
            seen = []

            def simulate(stage, settings, key, history, message, **kwargs):
                seen.append(message)
                if message == "下雨了，外面很安静。":
                    raise ModelRoutesFailed("reply", [("reply_legacy","超时"), ("reply_legacy","超时")])
                return "合成回答，不代表真实模型效果。"

            env = {"ZHUANGFY_E1_API_KEY":"test-secret", "ZHUANGFY_E1_BACKUP_API_KEY":""}
            with patch.dict(os.environ, env), patch.dict(
                sys.modules, {"model_routes":SimpleNamespace(invoke_stage=simulate)}):
                self.assertEqual(capture(args(path)), 2)
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(raw["responses"]), 2)
            self.assertEqual(raw["failures"][0]["case_id"], "everyday_rain")
            self.assertEqual(raw["failures"][0]["reason"], "timeout")
            self.assertEqual(raw["capture_info"]["reply_timeout_seconds"], "75")
            self.assertEqual(len(seen), 3)
            self.assertNotIn("test-secret", path.read_text(encoding="utf-8"))

            cases = load_cases(DATASET)
            answers, meta = load_responses(path, cases)
            report = audit(cases, answers, meta)
            self.assertEqual(report["summary"]["api_failed"], 1)
            self.assertEqual(report["summary"]["captured"], 2)
            self.assertEqual(report["summary"]["missing"], 29)
            row = next(x for x in report["results"] if x["id"] == "everyday_rain")
            self.assertEqual(row["status"], "api_failed")
            self.assertIn("timeout", html_report(report))
            self.assertIn("API 失败", markdown_report(report))

            retried = []
            def succeed(stage, settings, key, history, message, **kwargs):
                retried.append(message)
                return "新补测答案。"
            output = Path(d) / "resumed.json"
            with patch.dict(os.environ, env), patch.dict(
                sys.modules, {"model_routes":SimpleNamespace(invoke_stage=succeed)}):
                self.assertEqual(capture(args(output,resume=path)), 0)
            self.assertEqual(retried, ["下雨了，外面很安静。"])
            resumed = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(len(resumed["responses"]), 3)
            self.assertEqual(resumed["failures"], [])

    def test_resume_blocks_different_model_and_dataset_or_variant(self):
        with tempfile.TemporaryDirectory() as d:
            first = Path(d) / "first.json"
            with patch.dict(os.environ, {"ZHUANGFY_E1_API_KEY": "test-secret"}), patch.dict(
                sys.modules, {"model_routes":SimpleNamespace(invoke_stage=lambda *a, **k: "ok")}):
                self.assertEqual(capture(args(first, count=2)), 0)
                with self.assertRaises(ValueError):
                    capture(args(Path(d)/"bad-model.json",model="changed",resume=first))
                with self.assertRaises(ValueError):
                    capture(args(Path(d)/"bad-backup.json",backup=True,resume=first))
                mutated = json.loads(first.read_text(encoding="utf-8"))
                mutated["capture_info"]["dataset_sha256"] = "different"
                bad = Path(d)/"bad.json"
                bad.write_text(json.dumps(mutated), encoding="utf-8")
                with self.assertRaises(ValueError):
                    capture(args(Path(d)/"bad-dataset.json",resume=bad))

    def test_backup_on_timeout_is_explicit_and_origin_attributed(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/"capture.json"
            called=[]
            def simulated(stage,settings,key,history,message,**kwargs):
                called.append((settings.provider, settings.model, key))
                if settings.provider == "deepseek" and message == "下雨了，外面很安静。":
                    raise ModelRoutesFailed("reply",[("reply_legacy","超时")])
                return "测试回答。"
            with patch.dict(os.environ, {
                "ZHUANGFY_E1_API_KEY":"primary-secret",
                "ZHUANGFY_E1_BACKUP_API_KEY":"backup-secret",
            }), patch.dict(sys.modules, {
                "model_routes":SimpleNamespace(invoke_stage=simulated),
            }):
                self.assertEqual(capture(args(path, backup=True)),0)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(data["responses"]),3)
            backup_row = next(x for x in data["responses"] if x["case_id"] == "everyday_rain")
            self.assertEqual(backup_row["origin"],"backup")
            self.assertEqual(backup_row["provider"],"siliconflow")
            self.assertEqual(backup_row["model"],"mock-backup")
            self.assertTrue(any(p=="siliconflow" and k=="backup-secret" for p,m,k in called))
            self.assertNotIn("primary-secret",path.read_text(encoding="utf-8"))
            self.assertNotIn("backup-secret",path.read_text(encoding="utf-8"))
            replies, meta = load_responses(path,load_cases(DATASET))
            report = audit(load_cases(DATASET),replies,meta)
            self.assertTrue(report["summary"]["mixed_models"])
            self.assertEqual(report["summary"]["backup_captured"],1)
            self.assertIn("备用",markdown_report(report))

    def test_auth_failure_does_not_trigger_backup(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"capture.json"
            seen=[]
            def authfail(stage,settings,key,history,message,**kwargs):
                seen.append(settings.provider)
                raise ModelRoutesFailed("reply",[("reply_legacy","HTTP 401")])
            with patch.dict(os.environ,{
                "ZHUANGFY_E1_API_KEY":"test","ZHUANGFY_E1_BACKUP_API_KEY":"second",
            }), patch.dict(sys.modules,{"model_routes":SimpleNamespace(invoke_stage=authfail)}):
                self.assertEqual(capture(args(path, count=1, backup=True)),2)
            self.assertEqual(seen,["deepseek"])
            data=json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["failures"][0]["reason"],"auth")
            self.assertEqual(data["failures"][0]["attempted"],"primary")

    def test_stage_direction_warning_for_actual_observed_phrase(self):
        cases=load_cases(DATASET)
        answer={"everyday_rain":"（看着窗外出了一会儿神，才慢慢敲键盘）\n下雨了。"}
        report=audit(cases,answer,{"source":"model_capture","variant":"ccv2"})
        row=next(r for r in report["results"] if r["id"]=="everyday_rain")
        self.assertIn("stage_direction_opening",row["warnings"])


if __name__ == "__main__":
    unittest.main()
