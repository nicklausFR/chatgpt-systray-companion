import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QPoint, QSettings, QTimer
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import app


QT = QApplication.instance() or QApplication([])
QT.setQuitOnLastWindowClosed(False)


class MarkdownCopyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        settings = QSettings(str(Path(self.temp.name) / "s.ini"), QSettings.IniFormat)
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

    def add_response(self, markdown):
        self.window._assistant_text = markdown
        self.window._render_assistant_message()
        QTest.qWait(10)
        return next(frame for frame in self.window.transcript.document().rootFrame().childFrames()
                    if frame.frameFormat().property(app.RESPONSE_MARKDOWN_PROPERTY) == markdown)

    def point_in(self, frame):
        transcript = self.window.transcript
        rect = transcript.document().documentLayout().frameBoundingRect(frame)
        return QPoint(int(rect.left()) + 20, int(rect.top()) + 5 - transcript.verticalScrollBar().value())

    def test_hover_button_copies_exact_markdown_including_code_links_and_unicode(self):
        markdown = "## Résumé\n\n**Gras**, *italique* et [un lien](https://example.com).\n\n```python\nprint('été')\n```\n"
        frame = self.add_response(markdown)
        transcript = self.window.transcript
        transcript._update_copy_button(self.point_in(frame))
        self.assertFalse(transcript._copy_button.isHidden())
        transcript._copy_button.click()
        self.assertEqual(QApplication.clipboard().text(), markdown)
        self.assertEqual(transcript._copy_button.text(), "")
        self.assertFalse(transcript._copy_button.icon().isNull())
        self.assertEqual(transcript._copy_button.toolTip(), "Markdown copié")

    def test_hovering_older_response_copies_its_own_source(self):
        first = "**Première réponse**"
        second = "*Deuxième réponse*"
        first_frame = self.add_response(first)
        self.add_response(second)
        transcript = self.window.transcript
        transcript.verticalScrollBar().setValue(0)
        transcript._update_copy_button(self.point_in(first_frame))
        transcript._copy_button.click()
        self.assertEqual(QApplication.clipboard().text(), first)
        transcript._update_copy_button(QPoint(-1, -1))
        self.assertTrue(transcript._copy_button.isHidden())

    def test_table_only_response_keeps_source_on_outer_message(self):
        markdown = "| A | B |\n|---|---|\n| **un** | deux |"
        frame = self.add_response(markdown)
        self.assertIs(frame.parentFrame(), self.window.transcript.document().rootFrame())
        self.assertEqual(self.window.transcript.response_markdown_at(self.point_in(frame)), markdown)

    def test_right_click_offers_markdown_copy_and_executes_it(self):
        markdown = "# À copier\n\nUne **réponse**."
        frame = self.add_response(markdown)
        point = self.point_in(frame)
        actions = []
        def choose():
            menu = QT.activePopupWidget()
            if menu is not None:
                actions.extend(action.text() for action in menu.actions())
                for action in menu.actions():
                    if action.text() == "Copier en Markdown":
                        action.trigger()
                menu.close()
        QTimer.singleShot(20, choose)
        self.window._editor_context_menu(self.window.transcript, point)
        self.assertIn("Copier en Markdown", actions)
        self.assertEqual(QApplication.clipboard().text(), markdown)

    def test_user_question_has_no_response_copy_action(self):
        self.window._append_message("Vous", "Question", QColor("#a8abb1"))
        self.assertIsNone(self.window.transcript.response_markdown_at(QPoint(20, 30)))

    def test_interrupted_response_is_copyable_and_new_chat_removes_old_source(self):
        partial = "## Réponse partielle\n\n**En cours"
        self.window._assistant_text = partial
        self.window._turn_finished("interrupted", "")
        transcript = self.window.transcript
        frame = next(frame for frame in transcript.document().rootFrame().childFrames()
                     if frame.frameFormat().property(app.RESPONSE_MARKDOWN_PROPERTY) == partial)
        transcript._update_copy_button(self.point_in(frame))
        transcript._copy_button.click()
        self.assertEqual(QApplication.clipboard().text(), partial)
        transcript.clear()
        self.assertTrue(transcript._copy_button.isHidden())
        self.assertIsNone(transcript._copy_markdown)
        self.assertIsNone(transcript.response_markdown_at(QPoint(20, 20)))


if __name__ == "__main__":
    unittest.main()
