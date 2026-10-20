"""Activity journal keeps useful records without writing obvious credentials."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import activity_log


class ActivityLogTests(unittest.TestCase):
    def test_cross_source_records_and_credential_redaction(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(activity_log, "LOG_DIR", Path(directory)):
                activity_log.log_event("QQ", "消息", "你好，sk-test-secret-12345678")
                activity_log.log_event("模型", "调用", "Bearer test-secret-12345678")
                day = activity_log.available_days()[0]
                entries = activity_log.read_day(day)
                self.assertEqual([entry["source"] for entry in entries], ["QQ", "模型"])
                raw = "\n".join(path.read_text(encoding="utf-8") for path in Path(directory).glob("*.jsonl"))
                self.assertNotIn("sk-test-secret-12345678", raw)
                self.assertNotIn("test-secret-12345678", raw)
                self.assertIn("[已隐藏]", raw)


if __name__ == "__main__":
    unittest.main()
