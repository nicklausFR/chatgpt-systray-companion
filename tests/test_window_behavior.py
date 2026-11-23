import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPoint, QRect, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QSystemTrayIcon

import app


QT = QApplication.instance() or QApplication([])
QT.setQuitOnLastWindowClosed(False)


class WindowBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.tray = patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=True)
        self.tray.start()
        settings = QSettings(str(Path(self.temp.name) / "settings.ini"), QSettings.IniFormat)
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
        self.tray.stop()
        self.temp.cleanup()

    def deactivate(self, popup=None, modal=None, active=None):
        with patch.object(self.window, "isActiveWindow", return_value=False), \
             patch.object(QApplication, "activePopupWidget", return_value=popup), \
             patch.object(QApplication, "activeModalWidget", return_value=modal), \
             patch.object(QApplication, "activeWindow", return_value=active), \
             patch.object(self.window.tray_icon, "geometry", return_value=QRect()):
            QApplication.sendEvent(self.window, QEvent(QEvent.WindowDeactivate))
            QTest.qWait(200)

    def test_unpinned_deactivation_hides_without_losing_draft_or_stopping_response(self):
        window = self.window
        window.input.setPlainText("Brouillon à conserver")
        window._set_busy(True)
        window._assistant_text = "Réponse en cours"
        with patch.object(window.client, "interrupt") as interrupted:
            self.deactivate()
        self.assertFalse(window.isVisible())
        self.assertTrue(window._busy)
        self.assertEqual(window.input.toPlainText(), "Brouillon à conserver")
        self.assertEqual(window._assistant_text, "Réponse en cours")
        self.assertEqual(window.client.thread_id, "fixture")
        interrupted.assert_not_called()

    def test_pin_replaces_close_and_enables_always_on_top_without_changing_geometry(self):
        window = self.window
        geometry = window.geometry()
        self.assertFalse(hasattr(window, "close_button"))
        window.input.setPlainText("Contexte conservé")
        window.pin_button.click()
        self.assertTrue(window._pinned)
        self.assertTrue(window.pin_button.isChecked())
        self.assertTrue(window.windowFlags() & Qt.WindowStaysOnTopHint)
        self.assertEqual(window.windowType(), Qt.Tool)
        self.assertTrue(window.windowFlags() & Qt.FramelessWindowHint)
        self.assertEqual(window.geometry(), geometry)
        self.deactivate()
        self.assertTrue(window.isVisible())
        window.pin_button.click()
        self.assertFalse(window._pinned)
        self.assertFalse(window.windowFlags() & Qt.WindowStaysOnTopHint)
        self.assertEqual(window.input.toPlainText(), "Contexte conservé")
        self.deactivate()
        self.assertFalse(window.isVisible())

    def test_popup_keeps_parent_visible_then_outside_focus_hides_it(self):
        menu = QMenu(self.window)
        self.deactivate(popup=menu)
        self.assertTrue(self.window.isVisible())
        self.deactivate()
        self.assertFalse(self.window.isVisible())
        menu.deleteLater()

    def test_file_dialog_and_active_image_preview_keep_parent_visible(self):
        dialog = QDialog(self.window)
        self.deactivate(modal=dialog)
        self.assertTrue(self.window.isVisible())
        with patch.object(dialog, "isActiveWindow", return_value=True):
            self.deactivate(active=dialog)
        self.assertTrue(self.window.isVisible())
        dialog.deleteLater()

    def test_tray_click_toggles_even_after_focus_loss(self):
        window = self.window
        with patch.object(window, "isActiveWindow", return_value=False), \
             patch.object(QApplication, "activePopupWidget", return_value=None), \
             patch.object(QApplication, "activeModalWidget", return_value=None), \
             patch.object(QApplication, "activeWindow", return_value=None), \
             patch.object(window.tray_icon, "geometry", return_value=QRect(100, 100, 20, 20)), \
             patch.object(app.QCursor, "pos", return_value=QPoint(110, 110)):
            window._hide_if_unfocused()
            self.assertTrue(window.isVisible())
            window._toggle_from_tray()
        self.assertFalse(window.isVisible())
        window._toggle_from_tray()
        self.assertTrue(window.isVisible())

    def test_native_file_picker_keeps_parent_visible_even_without_qt_modal(self):
        def choose(*_args):
            self.deactivate()
            self.assertTrue(self.window.isVisible())
            return [], ""
        with patch.object(app.QFileDialog, "getOpenFileNames", side_effect=choose):
            self.window._choose_files()
        self.assertFalse(self.window._native_dialog_open)

    def test_without_system_tray_focus_loss_keeps_window_reachable(self):
        with patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=False):
            self.deactivate()
        self.assertTrue(self.window.isVisible())

    def test_escape_still_hides_a_pinned_window(self):
        self.window.pin_button.click()
        QTest.keyClick(self.window.input, Qt.Key_Escape)
        self.assertFalse(self.window.isVisible())
        self.assertTrue(self.window._pinned)

    def test_new_discussion_clears_chat_and_draft_but_keeps_model_and_effort(self):
        window = self.window
        window.client._model = "selected-model"
        window.client._effort = "high"
        window._assistant_text = "Une **réponse** à effacer."
        window._render_assistant_message()
        window.input.setPlainText("Brouillon")
        with patch.object(window.client, "new_thread") as start:
            window._set_busy(True)
            window.new_chat_button.click()
            start.assert_not_called()
            window._set_busy(False)
            window.new_chat_button.click()
            start.assert_called_once_with()
        self.assertTrue(window.transcript.document().isEmpty())
        self.assertEqual(window.input.toPlainText(), "")
        self.assertEqual(window.client._model, "selected-model")
        self.assertEqual(window.client._effort, "high")
        self.assertFalse(window.new_chat_button.isEnabled())
        window._thread_ready()
        self.assertTrue(window.new_chat_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
