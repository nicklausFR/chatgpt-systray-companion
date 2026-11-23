import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QPoint, QSettings, Qt, QUrl
from PySide6.QtGui import QColor, QImage, QTextCursor, QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import app
from generated_files import GeneratedBundle, GeneratedFile

QT = QApplication.instance() or QApplication([])
QT.setQuitOnLastWindowClosed(False)


class ResponseImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        settings = QSettings(str(self.root / "settings.ini"), QSettings.IniFormat)
        with patch.object(app, "QSettings", return_value=settings), patch.object(app.AppServerClient, "launch"):
            self.window = app.MainWindow(self.root)
        self.window.client.thread_id = "fixture"
        self.window._thread_ready()
        self.window._set_status("Prêt")
        self.window.show()
        self.image = QImage(2400, 1600, QImage.Format_ARGB32)
        self.image.fill(QColor("#2563eb"))
        self.key = "https://example.invalid/large.png"
        self.transcript = self.window.transcript
        self.transcript._images[self.key] = self.image
        self.transcript.document().addResource(QTextDocument.ImageResource, QUrl(self.key), self.image)

    def tearDown(self):
        if self.transcript._image_preview is not None:
            self.transcript._image_preview.close()
        self.window._shutdown()
        self.window.close()
        self.window.deleteLater()
        QT.processEvents()
        self.temp.cleanup()

    def render(self, markdown):
        self.window._assistant_text = markdown
        self.window._render_assistant_message()
        QTest.qWait(30)

    def assert_fits(self):
        for position, _length, fmt in self.transcript._image_fragments():
            if fmt.name() != self.key:
                continue
            cursor = QTextCursor(self.transcript.document())
            cursor.setPosition(position)
            rect = self.transcript.document().documentLayout().blockBoundingRect(cursor.block())
            self.assertLessEqual(fmt.width(), rect.width() + 1)
            self.assertLessEqual(rect.left() + fmt.width(), self.transcript.viewport().width() - 7)
            self.assertAlmostEqual(fmt.height() / fmt.width(), 1600 / 2400)
            self.assertEqual(self.transcript._images[self.key].size(), self.image.size())

    def test_images_fit_response_lists_quotes_and_table_columns(self):
        image = f"![Image]({self.key})"
        for markdown in (image, "- " + image, "> " + image, f"| Image | Texte |\n|---|---|\n| {image} | Une description |"):
            with self.subTest(markdown=markdown):
                self.render(markdown)
                self.assert_fits()

    def test_images_follow_window_width_in_both_directions(self):
        self.render(f"![Image]({self.key})")
        widths = []
        for width in (700, 408, 620):
            self.window.resize(width, self.window.height())
            QTest.qWait(30)
            self.assert_fits()
            widths.append(next(fmt.width() for _pos, _length, fmt in self.transcript._image_fragments() if fmt.name() == self.key))
        self.assertGreater(widths[0], widths[1])
        self.assertGreater(widths[2], widths[1])

    def test_click_opens_original_pixels_with_scrollbars(self):
        self.render(f"![Image]({self.key})")
        self.transcript.verticalScrollBar().setValue(0)
        position, _length, fmt = next(item for item in self.transcript._image_fragments() if item[2].name() == self.key)
        cursor = QTextCursor(self.transcript.document())
        cursor.setPosition(position)
        rect = self.transcript.document().documentLayout().blockBoundingRect(cursor.block())
        point = QPoint(int(rect.left() + fmt.width() / 2), int(rect.top() + fmt.height() / 2))
        self.assertEqual(self.transcript._image_at(point), self.key)
        QTest.mouseClick(self.transcript.viewport(), Qt.LeftButton, pos=point)
        QTest.qWait(20)
        preview = self.transcript._image_preview
        self.assertIsNotNone(preview)
        self.assertEqual(preview.image_label.pixmap().size(), self.image.size())
        self.assertGreater(preview.scroll_area.horizontalScrollBar().maximum(), 0)
        self.assertGreater(preview.scroll_area.verticalScrollBar().maximum(), 0)

    def test_native_image_deliverable_is_displayed_at_fit_size_and_keeps_original(self):
        exports = self.root / "exports"
        exports.mkdir()
        source = exports / "image.png"
        self.image.save(str(source))
        bundle = GeneratedBundle("image.png", (GeneratedFile("image.png", source=source, source_root=exports),), False)
        self.window._assistant_text = "![Image](exports/image.png)"
        self.window._render_assistant_message([bundle])
        QTest.qWait(30)
        key = "exports/image.png"
        self.assertEqual(self.transcript._images[key].size(), self.image.size())
        image_format = next(fmt for _pos, _length, fmt in self.transcript._image_fragments() if fmt.name() == key)
        self.assertLessEqual(image_format.width(), self.transcript.viewport().width() - 12)
        self.assertIn("Enregistrer le fichier", self.transcript.toPlainText())
