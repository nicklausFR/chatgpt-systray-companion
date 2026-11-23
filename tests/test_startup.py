import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QProcess, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPlainTextEdit

import app
from codex_check import CodexProbe
from scroll_fade import ScrollFade


QT = QApplication.instance() or QApplication([])
QT.setQuitOnLastWindowClosed(False)


def wait_for(predicate, seconds=3):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
    if not predicate():
        raise AssertionError("Le signal attendu n'est pas arrivé.")


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = QSettings(str(Path(self.temp.name) / "settings.ini"), QSettings.IniFormat)
        self.settings_patch = patch.object(app, "QSettings", return_value=self.settings)
        self.settings_patch.start()
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            window._shutdown()
            window.close()
            window.deleteLater()
        QT.processEvents()
        self.settings_patch.stop()
        self.temp.cleanup()

    def window(self):
        with patch.object(app.AppServerClient, "launch"):
            window = app.MainWindow(Path(self.temp.name))
            QT.processEvents()
        self.windows.append(window)
        return window

    def test_missing_codex_opens_setup_without_error_dialog(self):
        window = self.window()
        with patch.object(app, "find_codex", return_value=None), patch.object(app.QMessageBox, "critical") as dialog:
            window.client.launch()
        self.assertTrue(window.isVisible())
        self.assertFalse(window.setup_panel.isHidden())
        self.assertIn("introuvable", window.setup_message.text())
        self.assertFalse(window.input.isEnabled())
        self.assertTrue(window.retry_codex_button.isEnabled())
        self.assertTrue(window.choose_codex_button.isEnabled())
        self.assertGreaterEqual(window.setup_title.height(), window.setup_title.fontMetrics().height())
        self.assertGreaterEqual(window.install_codex_button.height(), 30)
        dialog.assert_not_called()

    def test_protocol_error_and_timeout_offer_recovery(self):
        window = self.window()
        window.client._handle_message({"id": 1, "error": {"code": -32601, "message": "Method not found"}})
        self.assertIn("compatibilité", window.setup_message.text())
        window.client._startup_timer.start(10)
        wait_for(lambda: "ne répond pas" in window.setup_message.text())
        self.assertFalse(window.input.isEnabled())

    def test_login_is_offered_and_setup_closes_when_ready(self):
        window = self.window()
        window.client.codex_version = "0.169.1"
        window.client._preferred_codex = "C:/installation/codex.exe"
        with patch.object(window.client, "state", return_value=QProcess.Running):
            window.client._account_response({"account": None, "requiresOpenaiAuth": True})
            self.assertFalse(window.setup_login_button.isHidden())
            self.assertTrue(window.setup_login_button.isEnabled())
            self.assertFalse(window.client._startup_timer.isActive())
            self.assertEqual(self.settings.value("codex_path"), "C:/installation/codex.exe")
            window.client.thread_id = "fixture"
            window._thread_ready()
            self.assertTrue(window.setup_panel.isHidden())
            self.assertTrue(window.input.isEnabled())

    def test_retry_rechecks_and_resets_previous_protocol_state(self):
        window = self.window()
        window.client._buffer = b"ancien fragment"
        window.client.thread_id = "ancien"
        window.client._callbacks[1] = lambda _: None
        with patch.object(app, "find_codex", return_value=sys.executable), patch.object(window.client._probe, "check") as probe:
            window._retry_codex(sys.executable)
        probe.assert_called_once_with(sys.executable)
        self.assertEqual(window.client._buffer, b"")
        self.assertFalse(window.client._callbacks)
        self.assertIsNone(window.client.thread_id)
        self.assertFalse(window.input.isEnabled())

    def test_installer_button_opens_official_instructions(self):
        window = self.window()
        with patch.object(app.QDesktopServices, "openUrl") as opened:
            window.install_codex_button.click()
        self.assertEqual(opened.call_args.args[0].toString(), app.CODEX_INSTALL_URL)

    def test_automatic_detection_can_replace_a_stale_manual_path(self):
        window = self.window()
        window.client._preferred_codex = "C:/ancienne-installation/codex.exe"
        with patch.object(app, "find_codex", return_value=sys.executable) as finder, patch.object(window.client._probe, "check"):
            window.detect_codex_button.click()
        finder.assert_called_once_with("")
        self.assertEqual(window.client._preferred_codex, "")


class DiscoveryTests(unittest.TestCase):
    def test_standalone_cli_is_preferred_over_desktop_app_engine(self):
        desktop = "C:/Program Files/WindowsApps/OpenAI.Codex_1/app/resources/codex.exe"
        with tempfile.TemporaryDirectory() as tmp, patch.object(app.shutil, "which", return_value=desktop), patch.object(app, "application_directory", return_value=Path(tmp)/"portable"), patch.dict(os.environ, {"CODEX_HOME": tmp, "LOCALAPPDATA": tmp, "APPDATA": tmp}):
            binary = Path(tmp) / "npm/node_modules/@openai/codex/node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"fixture")
            self.assertEqual(Path(app.find_codex()), binary)

    def test_official_installation_is_found_without_path(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app.shutil, "which", return_value=None), patch.object(app, "application_directory", return_value=Path(tmp)/"portable"), patch.dict(os.environ, {"CODEX_HOME": tmp, "LOCALAPPDATA": tmp}):
            binary = Path(tmp) / "packages/standalone/current/codex.exe"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"fixture")
            self.assertEqual(Path(app.find_codex()), binary)

    def test_explicit_missing_path_does_not_launch_another_installation(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app.shutil, "which") as which:
            self.assertIsNone(app.find_codex(str(Path(tmp)/"absent.exe")))
            which.assert_not_called()


class ProbeTests(unittest.TestCase):
    def test_non_codex_binary_is_rejected(self):
        probe = CodexProbe()
        failures = []
        probe.failed.connect(failures.append)
        probe.check(sys.executable)
        wait_for(lambda: bool(failures))
        self.assertIn("pas un moteur Codex", failures[0])
        self.assertEqual(probe.process.state(), QProcess.NotRunning)

    def test_missing_executable_reports_one_failure(self):
        probe = CodexProbe()
        failures = []
        probe.failed.connect(failures.append)
        probe.check(str(Path(tempfile.gettempdir()) / "companion-codex-not-present.exe"))
        wait_for(lambda: bool(failures))
        QTest.qWait(30)
        self.assertEqual(len(failures), 1)

    def test_stalled_probe_is_killed_on_timeout(self):
        probe = CodexProbe()
        failures = []
        probe.failed.connect(failures.append)
        probe.TIMEOUT_MS = 100
        start = probe.process.start
        def start_sleeper():
            probe.process.setArguments(["-c", "import time; time.sleep(20)"])
            start()
        with patch.object(probe.process, "start", side_effect=start_sleeper):
            probe.check(sys.executable)
        wait_for(lambda: bool(failures))
        self.assertIn("ne répond pas", failures[0])
        self.assertEqual(probe.process.state(), QProcess.NotRunning)


class ScrollFadeTests(unittest.TestCase):
    def test_edges_follow_scroll_position_and_disappear_without_overflow(self):
        editor = QPlainTextEdit()
        editor.resize(320, 130)
        fade = ScrollFade(editor, "#333333")
        editor.setPlainText("\n".join(f"Ligne {i}" for i in range(60)))
        editor.show()
        QTest.qWait(30)
        bar = editor.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        bar.setValue(bar.minimum())
        self.assertTrue(fade.top.isHidden())
        self.assertFalse(fade.bottom.isHidden())
        bar.setValue(bar.maximum() // 2)
        self.assertFalse(fade.top.isHidden())
        self.assertFalse(fade.bottom.isHidden())
        bar.setValue(bar.maximum())
        self.assertFalse(fade.top.isHidden())
        self.assertTrue(fade.bottom.isHidden())
        self.assertTrue(fade.top.testAttribute(Qt.WA_TransparentForMouseEvents))
        editor.setPlainText("Court")
        QTest.qWait(10)
        self.assertTrue(fade.top.isHidden())
        self.assertTrue(fade.bottom.isHidden())
        editor.close()
        editor.deleteLater()


if __name__ == "__main__":
    unittest.main()
