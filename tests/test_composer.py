import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QTextCursor, QTextFormat
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import app


QT = QApplication.instance() or QApplication([])
QT.setQuitOnLastWindowClosed(False)


class ComposerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        settings = QSettings(str(Path(self.temp.name) / "settings.ini"), QSettings.IniFormat)
        settings.setValue("theme", "dark")
        with patch.object(app, "QSettings", return_value=settings), patch.object(app.AppServerClient, "launch"):
            self.window = app.MainWindow(Path(self.temp.name))
            self.window.client.thread_id = "fixture"
            self.window._thread_ready()
            self.window._set_status("Prêt")
            self.window.show()
            QTest.qWait(20)

    def tearDown(self):
        self.window._shutdown()
        self.window.close()
        self.window.deleteLater()
        QT.processEvents()
        self.temp.cleanup()

    def test_default_composer_is_one_line_and_arrow_is_visible_when_disabled(self):
        window = self.window
        self.assertLessEqual(window.composer.height(), 48)
        self.assertTrue(window.input.toPlainText() == "")
        self.assertFalse(window.send_button.isEnabled())
        icon = window.send_button.icon().pixmap(20, 20, QIcon.Disabled).toImage()
        arrow = icon.pixelColor(10, 6)
        self.assertGreater(arrow.alpha(), 150)
        self.assertGreater(arrow.lightness(), 140)

    def test_growth_caps_at_five_lines_and_preserves_both_fades(self):
        window = self.window
        initial = window.composer.height()
        window.input.setPlainText("\n".join("Question" for _ in range(5)))
        QTest.qWait(20)
        five = window.input.height()
        self.assertGreater(window.composer.height(), initial)
        window.input.setPlainText("\n".join("Question" for _ in range(20)))
        QTest.qWait(20)
        self.assertEqual(window.input.height(), five)
        bar = window.input.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        bar.setValue(bar.maximum() // 2)
        self.assertFalse(window.input_fade.top.isHidden())
        self.assertFalse(window.input_fade.bottom.isHidden())
        window.input.clear()
        QTest.qWait(20)
        self.assertEqual(window.composer.height(), initial)
        self.assertTrue(window.input_fade.top.isHidden())
        self.assertTrue(window.input_fade.bottom.isHidden())

    def test_one_line_has_no_scrollbar_at_different_font_sizes(self):
        window = self.window
        for size in (9, 10, 11, 12, 14):
            with self.subTest(size=size):
                window.input.setFont(QFont("Segoe UI", size))
                for text in ("", "Question"):
                    window.input.setPlainText(text)
                    window.input._fit_height()
                    QTest.qWait(10)
                    self.assertEqual(window.input.verticalScrollBar().maximum(), 0)
                    self.assertFalse(window.input.verticalScrollBar().isVisible())
                    self.assertTrue(window.input_fade.top.isHidden())
                    self.assertTrue(window.input_fade.bottom.isHidden())

    def test_answer_starts_in_same_column_as_question_text(self):
        window = self.window
        question_start = window._append_message("Vous", "Question", QColor("#a8abb1"))
        window._assistant_text = "Réponse alignée.\n\n- Une liste\n- Un autre élément"
        window._render_assistant_message()
        QTest.qWait(10)
        question = QTextCursor(window.transcript.document())
        question.setPosition(question_start)
        answer = window.transcript.document().find("Réponse alignée.")
        answer.setPosition(answer.selectionStart())
        self.assertEqual(window.transcript.cursorRect(question).x(), window.transcript.cursorRect(answer).x())

    def test_enter_sends_shift_enter_adds_line_and_stop_stays_integrated(self):
        window = self.window
        window.input.setPlainText("Une question")
        QTest.keyClick(window.input, Qt.Key_Return, Qt.ShiftModifier)
        self.assertIn("\n", window.input.toPlainText())
        with patch.object(window.client, "send_message") as sent:
            QTest.keyClick(window.input, Qt.Key_Return)
            sent.assert_called_once()
        self.assertTrue(window.send_button.isHidden())
        self.assertFalse(window.stop_button.isHidden())
        self.assertIs(window.stop_button.parent(), window.composer)

    def test_question_frame_preserves_selectable_content_and_transcript_fades(self):
        window = self.window
        text = "Une question qui reste sélectionnable."
        window._append_message("Vous", text, QColor("#a8abb1"))
        frame = next(frame for frame in window.transcript.document().rootFrame().childFrames()
                     if frame.frameFormat().property(QTextFormat.UserProperty))
        self.assertEqual(frame.frameFormat().background().color(), QColor("#333333"))
        cursor = QTextCursor(window.transcript.document())
        cursor.select(QTextCursor.Document)
        self.assertIn(text, cursor.selectedText())
        window._assistant_text = "\n\n".join("Réponse longue à faire défiler." for _ in range(100))
        window._render_assistant_message()
        QTest.qWait(20)
        bar = window.transcript.verticalScrollBar()
        bar.setValue(bar.maximum() // 2)
        self.assertFalse(window.transcript_fade.top.isHidden())
        self.assertFalse(window.transcript_fade.bottom.isHidden())
        window.transcript.clear()
        QT.processEvents()


if __name__ == "__main__":
    unittest.main()
