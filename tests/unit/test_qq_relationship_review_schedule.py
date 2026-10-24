from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from zhuangfy.channels.qq.relationship_review import DailyRelationshipReview


class DailyRelationshipReviewTests(unittest.TestCase):
    def test_one_attempt_per_local_day_survives_restart_and_is_per_identity(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "relationship_reviews.sqlite3"
            first = DailyRelationshipReview(path)
            self.assertTrue(first.claim(11, 22, day=date(2026, 10, 23)))
            self.assertFalse(DailyRelationshipReview(path).claim(
                11, 22, day=date(2026, 10, 23)))
            self.assertTrue(first.claim(11, 23, day=date(2026, 10, 23)))
            self.assertTrue(first.claim(12, 22, day=date(2026, 10, 23)))
            self.assertTrue(first.claim(11, 22, day=date(2026, 10, 24)))

    def test_erase_removes_user_schedule_only(self):
        with TemporaryDirectory() as folder:
            gate = DailyRelationshipReview(Path(folder) / "review.sqlite3")
            day = date(2026, 10, 23)
            gate.claim(11, 22, day=day)
            gate.claim(11, 23, day=day)
            gate.erase(11, 22)
            self.assertTrue(gate.claim(11, 22, day=day))
            self.assertFalse(gate.claim(11, 23, day=day))


if __name__ == "__main__":
    unittest.main()
