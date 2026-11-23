"""Fondus discrets indiquant le texte restant au-dessus et au-dessous."""
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import QWidget


class _FadeEdge(QWidget):
    def __init__(self, viewport, color: str, top: bool):
        super().__init__(viewport)
        self.color = QColor(color)
        self.top = top
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.NoFocus)
        self.hide()

    def paintEvent(self, event):  # noqa: N802 - API Qt
        gradient = QLinearGradient(0, 0, 0, self.height())
        transparent = QColor(self.color)
        transparent.setAlpha(0)
        gradient.setColorAt(0, self.color if self.top else transparent)
        gradient.setColorAt(1, transparent if self.top else self.color)
        painter = QPainter(self)
        painter.fillRect(self.rect(), gradient)


class ScrollFade(QObject):
    def __init__(self, editor, color: str, height: int = 16):
        super().__init__(editor)
        self.viewport = editor.viewport()
        self.scrollbar = editor.verticalScrollBar()
        self.height = height
        self.top = _FadeEdge(self.viewport, color, True)
        self.bottom = _FadeEdge(self.viewport, color, False)
        self.viewport.installEventFilter(self)
        self.scrollbar.valueChanged.connect(self.update)
        self.scrollbar.rangeChanged.connect(self.update)
        self.update()

    def eventFilter(self, watched, event):  # noqa: N802 - API Qt
        if event.type() in (QEvent.Resize, QEvent.Show):
            self.update()
        return super().eventFilter(watched, event)

    def set_color(self, color: str) -> None:
        for edge in (self.top, self.bottom):
            edge.color = QColor(color)
            edge.update()

    def update(self, *_args) -> None:
        height = min(self.height, self.viewport.height() // 3)
        self.top.setGeometry(0, 0, self.viewport.width(), height)
        self.bottom.setGeometry(0, self.viewport.height() - height, self.viewport.width(), height)
        self.top.setVisible(self.scrollbar.value() > self.scrollbar.minimum())
        self.bottom.setVisible(self.scrollbar.value() < self.scrollbar.maximum())
        self.top.raise_()
        self.bottom.raise_()
