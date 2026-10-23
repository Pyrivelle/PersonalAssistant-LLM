"""E1 evaluates declared constraints, NOT subjective character quality by regex."""
from __future__ import annotations

from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

from zhuangfy.evaluation.character_e1 import (
    WEIGHTS, audit, compare_reports, html_report, load_cases,
    load_responses, load_reviews, markdown_report, review_template,
    write_reports
)

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "resources" / "evaluation" / "character_e1_cases.json"
DEMO = ROOT / "resources" / "evaluation" / "character_e1_demo_responses.json"


class CharacterE1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = load_cases(CASES)
        cls.responses, cls.meta = load_responses(DEMO, cls.cases)

    def test_dataset_is_complete_synthetic_and_ready_for_r1(self):
        self.assertEqual(len(self.cases), 32)
        self.assertEqual(len({c.category for c in self.cases}), 10)
        self.assertEqual(len(self.responses), 32)
        self.assertEqual(self.meta["source"], "handcrafted_demo")
        close = [c for c in self.cases if c.id.startswith("closeness_l")]
        self.assertEqual({c.relation for c in close}, {"L0", "L1", "L2", "L3"})
        self.assertEqual({c.message for c in close}, {"晚安。"})

    def test_handcrafted_examples_produce_complete_but_unrated_report(self):
        report = audit(self.cases, self.responses, self.meta)
        self.assertEqual(report["summary"]["cases"], 32)
        self.assertEqual(report["summary"]["captured"], 32)
        self.assertEqual(report["summary"]["missing"], 0)
        self.assertEqual(report["summary"]["hard_failures"], 0)
        self.assertIsNone(report["summary"]["human_average_100"])
        self.assertEqual(report["summary"]["human_reviewed"], 0)
        self.assertTrue(all(r["status"] == "awaiting_human_review"
                            for r in report["results"]))
        self.assertIn("HANDCRAFTED DEMO", markdown_report(report))

    def test_explicit_forbidden_phrase_is_a_real_hard_failure(self):
        altered = dict(self.responses)
        altered["memory_no_evidence"] = "去年我们一起去了东京，当然记得！"
        report = audit(self.cases, altered, self.meta)
        item = next(x for x in report["results"] if x["id"]=="memory_no_evidence")
        self.assertEqual(item["status"], "hard_fail")
        self.assertIn("forbidden_literal:去年我们一起去了东京", item["hard_failures"])
        self.assertEqual(report["summary"]["hard_failures"], 1)

    def test_missing_responses_are_missing_not_automatically_passed(self):
        incomplete = dict(self.responses)
        del incomplete["closeness_l3"]
        report = audit(self.cases, incomplete, self.meta)
        self.assertEqual(report["summary"]["missing"], 1)
        self.assertEqual(report["summary"]["captured"], 31)
        self.assertEqual(report["summary"]["human_reviewed"], 0)

    def test_review_template_is_explicitly_unscored_until_humans_fill_it(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/"reviews.json"
            template = review_template(self.cases)
            path.write_text(json.dumps(template), encoding="utf-8")
            self.assertEqual(load_reviews(path,self.cases), {})
            row = template["reviews"][0]
            row["scores"] = {k:4 for k in WEIGHTS}
            row["notes"] = "人工示意评分"
            path.write_text(json.dumps(template,ensure_ascii=False),encoding="utf-8")
            reviews = load_reviews(path,self.cases)
            report = audit(self.cases, self.responses,self.meta,reviews)
            self.assertEqual(report["summary"]["human_reviewed"],1)
            self.assertEqual(report["summary"]["human_average_100"],80)
            template["reviews"][0]["scores"]["naturalness"] = "5"
            path.write_text(json.dumps(template),encoding="utf-8")
            with self.assertRaises(ValueError):load_reviews(path,self.cases)

    def test_reject_bad_response_bundle_and_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/"responses.json"
            data = json.loads(DEMO.read_text(encoding="utf-8"))
            data["responses"].append(dict(data["responses"][0]))
            path.write_text(json.dumps(data),encoding="utf-8")
            with self.assertRaises(ValueError):
                load_responses(path,self.cases)
            data["responses"][-1]["case_id"]="made_up_case"
            path.write_text(json.dumps(data),encoding="utf-8")
            with self.assertRaises(ValueError):
                load_responses(path,self.cases)

    def test_reports_are_written_and_html_escapes_model_text(self):
        alternate = dict(self.responses)
        alternate["everyday_meal"] = "<script>alert(1)</script>"
        report = audit(self.cases,alternate,self.meta)
        self.assertNotIn("<script>",html_report(report))
        self.assertIn("&lt;script&gt;",html_report(report))
        with tempfile.TemporaryDirectory() as d:
            target = Path(d)/"e1"
            write_reports(report,self.cases,target)
            self.assertEqual(set(p.name for p in target.iterdir()),
                             {"summary.md","report.html","details.json","review-template.json"})
            source = json.loads((target/"details.json").read_text(encoding="utf-8"))
            self.assertEqual(source["summary"]["cases"],32)
            self.assertIn("人工", (target/"summary.md").read_text(encoding="utf-8"))

    def test_comparison_only_uses_real_scores_if_both_reviewed(self):
        report=audit(self.cases,self.responses,self.meta)
        newer=dict(self.responses)
        newer["memory_no_evidence"]="去年我们一起去了东京"
        test=audit(self.cases,newer,{"source":"model_capture","variant":"ccv2"})
        cmp=compare_reports(test,report)
        self.assertEqual(cmp["changed_cases"][0]["case_id"],"memory_no_evidence")
        self.assertIsNone(cmp["changed_cases"][0]["current_human_score"])
        with self.assertRaises(ValueError):
            compare_reports({"results":test["results"][:-1],"variant":"bad"},report)

    def test_compares_real_human_scores_when_both_variants_reviewed(self):
        one = {self.cases[0].id: {"scores":{k:3 for k in WEIGHTS}, "notes":"旧版盲评"}}
        two = {self.cases[0].id: {"scores":{k:4 for k in WEIGHTS}, "notes":"新版盲评"}}
        prev = audit(self.cases,self.responses,{"source":"model_capture","variant":"v1"},one)
        newer = audit(self.cases,self.responses,{"source":"model_capture","variant":"ccv2"},two)
        delta = compare_reports(newer,prev)
        self.assertEqual(delta["changed_cases"][0]["baseline_human_score"],60)
        self.assertEqual(delta["changed_cases"][0]["current_human_score"],80)
        self.assertIn("v1",markdown_report({**newer,"comparison":delta}))

    def test_capture_metadata_is_bounded_and_visible(self):
        with tempfile.TemporaryDirectory() as d:
            data=json.loads(DEMO.read_text(encoding="utf-8"))
            data["capture_info"]={"provider":"local_test","configured_model":"example"}
            path=Path(d)/"source.json"
            path.write_text(json.dumps(data,ensure_ascii=False),encoding="utf-8")
            responses,meta=load_responses(path,self.cases)
            report=audit(self.cases,responses,meta)
            self.assertEqual(report["capture_info"]["provider"],"local_test")
            self.assertIn("local_test",markdown_report(report))
            data["capture_info"]["secret"] = "not permitted"
            path.write_text(json.dumps(data),encoding="utf-8")
            with self.assertRaises(ValueError): load_responses(path,self.cases)

    def test_cli_creates_reports_and_blocks_unconsented_real_model_capture(self):
        with tempfile.TemporaryDirectory() as d:
            output=Path(d)/"reports"
            runner=subprocess.run(
                [sys.executable,str(ROOT/"scripts"/"character_e1_report.py"),
                 "--out",str(output),"--require-full","--fail-on-hard"],
                capture_output=True,text=True,check=False)
            self.assertEqual(runner.returncode,0,runner.stdout+runner.stderr)
            self.assertTrue((output/"report.html").exists())
            capture=subprocess.run(
                [sys.executable,str(ROOT/"scripts"/"character_e1_capture.py"),
                 "--variant","ccv2","--out",str(Path(d)/"capture.json")],
                capture_output=True,text=True,check=False)
            self.assertEqual(capture.returncode,2)
            self.assertIn("--allow-network",capture.stderr)
            self.assertFalse((Path(d)/"capture.json").exists())


if __name__=="__main__":
    unittest.main()
