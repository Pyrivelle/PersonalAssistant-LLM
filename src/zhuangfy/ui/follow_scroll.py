"""Keep a live view at the latest entry until the reader scrolls away."""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QScrollBar


class FollowScroll:
    def __init__(self, scrollbar: QScrollBar) -> None:
        self.scrollbar = scrollbar
        self.following = True
        self._updating = False
        self._position = 0
        scrollbar.rangeChanged.connect(self._range_changed)
        scrollbar.actionTriggered.connect(self._user_scrolled)
        scrollbar.sliderMoved.connect(self._user_scrolled)

    def _user_scrolled(self, _action: int) -> None:
        # An action is emitted before Qt applies its new value. A plain
        # valueChanged signal also fires during layout, so it cannot reliably
        # distinguish a reader scrolling from a widget being shown.
        QTimer.singleShot(0, self._check_user_position)

    def _check_user_position(self) -> None:
        if not self._updating:
            self.following = self.scrollbar.value() >= self.scrollbar.maximum() - 8

    def _range_changed(self, _minimum: int, _maximum: int) -> None:
        if self.following and not self._updating:
            self.jump_to_latest()

    def begin_update(self) -> None:
        self._position = self.scrollbar.value()
        self._updating = True

    def end_update(self) -> None:
        self._updating = False
        self._restore()
        # Qt may recalculate a scroll area's range after the new widgets are laid out.
        QTimer.singleShot(0, self._restore)

    def _restore(self) -> None:
        self._updating = True
        try:
            self.scrollbar.setValue(
                self.scrollbar.maximum() if self.following else
                min(self._position, self.scrollbar.maximum()))
        finally:
            self._updating = False

    def jump_to_latest(self) -> None:
        self.following = True
        self._restore()
