"""Live views follow new entries without pulling readers away from older ones."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QAbstractSlider, QPlainTextEdit

from zhuangfy.ui.follow_scroll import FollowScroll


class FollowScrollTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_new_lines_follow_bottom_until_user_scrolls_up(self):
        view = QPlainTextEdit()
        view.resize(300, 130)
        follower = FollowScroll(view.verticalScrollBar())
        view.show()
        follower.begin_update()
        view.setPlainText("\n".join(str(number) for number in range(100)))
        follower.end_update()
        self.app.processEvents()
        bar = view.verticalScrollBar()
        self.assertEqual(bar.value(), bar.maximum())

        bar.triggerAction(QAbstractSlider.SliderAction.SliderToMinimum)
        self.app.processEvents()
        self.assertFalse(follower.following)
        old_position = bar.value()
        follower.begin_update()
        view.appendPlainText("another line")
        follower.end_update()
        self.app.processEvents()
        self.assertEqual(bar.value(), old_position)

        follower.jump_to_latest()
        view.appendPlainText("latest line")
        self.app.processEvents()
        self.assertEqual(bar.value(), bar.maximum())
        view.close()

    def test_late_layout_change_still_follows_latest(self):
        view = QPlainTextEdit()
        view.resize(300, 130)
        follower = FollowScroll(view.verticalScrollBar())
        view.setPlainText("\n".join(str(number) for number in range(100)))
        view.show()
        self.app.processEvents()
        self.assertEqual(view.verticalScrollBar().value(),
                         view.verticalScrollBar().maximum())
        view.close()


if __name__ == "__main__":
    unittest.main()
