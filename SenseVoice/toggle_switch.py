"""Accessible checkbox behavior with a compact sliding-switch appearance."""

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QCheckBox


class ToggleSwitch(QCheckBox):
    TRACK_WIDTH = 42
    TRACK_HEIGHT = 24
    SPACING = 10

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(30)

    def sizeHint(self) -> QSize:
        width = self.TRACK_WIDTH + self.SPACING + self.fontMetrics().horizontalAdvance(self.text()) + 8
        return QSize(width, max(30, self.fontMetrics().height() + 8))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        top = (self.height() - self.TRACK_HEIGHT) / 2
        track = QRectF(1, top, self.TRACK_WIDTH, self.TRACK_HEIGHT)
        enabled = self.isEnabled()
        checked = self.isChecked()
        painter.setBrush(QColor("#5269c8" if checked and enabled else
                                "#b8c3d4" if enabled else "#d9dfe8"))
        painter.drawRoundedRect(track, 12, 12)
        thumb_x = track.right() - 12 if checked else track.left() + 12
        painter.setBrush(QColor("#ffffff" if enabled else "#f3f5f8"))
        painter.drawEllipse(QPointF(thumb_x, track.center().y()), 9, 9)
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#5269c8"), 1.5))
            painter.drawRoundedRect(track.adjusted(-2, -2, 2, 2), 14, 14)
        painter.setPen(QColor("#243149" if enabled else "#91a0b3"))
        text_rect = self.rect().adjusted(self.TRACK_WIDTH + self.SPACING, 0, -2, 0)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         self.text())
