import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

import app
from conversation_history import ConversationHistory


QT = QApplication.instance() or QApplication([])
QT.setQuitOnLastWindowClosed(False)


class ConversationHistoryTests(unittest.TestCase):
    def test_unclean_exit_recovers_the_stream_and_native_files(self):
        with tempfile.TemporaryDirectory() as directory:
            source = r"""
import os
import sys
from pathlib import Path
from artifact_workspace import ArtifactWorkspace
from conversation_history import ConversationHistory

chat = ConversationHistory(Path(sys.argv[1])).open()
workspace = ArtifactWorkspace()
workspace.new_chat(chat.workspace)
turn = chat.begin("Prompt saved before sending", [])
turn.checkpoint("Partial response")
(chat.workspace / "exports" / "generated.bin").write_bytes(b"\x00\xff")
os._exit(0)
"""
            subprocess.run([sys.executable, "-c", source, directory], check=True, timeout=20)
            history = ConversationHistory(Path(directory))
            entry, = history.entries()
            chat = history.open(entry.key)
            try:
                chat.recover()
                turn, = chat.turns
                self.assertEqual(turn.data["response"], "Partial response")
                self.assertEqual(turn.data["status"], "interrupted")
                bundle, = turn.bundles()
                self.assertEqual(bundle.files[0].source.read_bytes(), bytes([0, 255]))
            finally:
                chat.close()


class HistoryWindowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        settings = QSettings(str(self.root / "settings.ini"), QSettings.IniFormat)
        settings.setValue("language", "fr")
        for target, attribute, options in (
            (app, "QSettings", {"return_value": settings}),
            (app.AppServerClient, "launch", {}),
            (app.AppServerClient, "_request", {}),
        ):
            mock = patch.object(target, attribute, **options)
            mock.start()
            self.addCleanup(mock.stop)
        self.window = self._window()

    def _window(self):
        window = app.MainWindow(self.root)
        self.addCleanup(self._close, window)
        self._start_thread(window)
        return window

    @staticmethod
    def _close(window):
        window._shutdown()
        window.close()
        window.deleteLater()
        QT.processEvents()

    @staticmethod
    def _start_thread(window):
        window.client.new_thread()
        window.client._thread_response({"thread": {"id": "fixture"}})

    def _send(self, prompt, response, generated=None):
        window = self.window
        window.input.setPlainText(prompt)
        window._send()
        window.client._turn_response({"turn": {"id": "fixture-turn"}})
        window._append_assistant_delta(response)
        if generated is not None:
            (window.client.artifacts.current / "exports" / "même fichier.bin").write_bytes(generated)
        window._turn_finished("completed", "")
        return window._history_chat.path.name

    def test_selecting_an_entry_restores_the_main_window_and_continues_with_context(self):
        reply = "[Fichier](./exports/m%C3%AAme%20fichier.bin)"
        key = self._send("Première question", reply, b"first")
        self._send("Deuxième question", reply, b"second")
        self.window._shutdown()
        self.window = self._window()
        dialog = app.HistoryDialog(self.window)
        dialog.show()
        item = dialog.entries.topLevelItem(0)
        self.assertEqual(item.data(0, Qt.UserRole), key)
        self.assertTrue(item.text(2))
        point = QPoint(dialog.entries.columnViewportPosition(1) + 20, dialog.entries.visualItemRect(item).center().y())
        QTest.mouseClick(dialog.entries.viewport(), Qt.LeftButton, pos=point)
        self.assertEqual(dialog.result(), QDialog.Accepted)
        self.assertIn("Première question", self.window.transcript.toPlainText())
        self.assertIn("Deuxième question", self.window.transcript.toPlainText())
        transcript = self.window.transcript
        saved = []
        for frame in transcript.document().rootFrame().childFrames():
            keys = frame.frameFormat().property(app.RESPONSE_EXPORTS_PROPERTY)
            if keys:
                saved.append(transcript._export_bundle(keys[0]).files[0].source.read_bytes())
        self.assertEqual(saved, [b"first", b"second"])
        self.window.client._thread_response({"thread": {"id": "restored"}})
        self.window.input.setPlainText("Et ensuite ?")
        self.window._send()
        method, params, _callback = self.window.client._request.call_args.args
        self.assertEqual(method, "turn/start")
        self.assertEqual(params["threadId"], "restored")
        for prompt in ("Première question", "Deuxième question"):
            self.assertIn(prompt, params["input"][0]["text"])
        self.assertEqual(params["input"][-1]["text"], "Et ensuite ?")

    def test_delete_one_selection_and_all_from_history(self):
        keys = []
        for number in range(4):
            keys.append(self._send(f"Question {number}", "Réponse", bytes([number])))
            self.window._new_chat()
            self.window.client._thread_response({"thread": {"id": "fixture"}})
        dialog = app.HistoryDialog(self.window)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            dialog.entries.topLevelItem(0).setCheckState(0, Qt.Checked)
            dialog.remove.click()
            self.assertEqual(dialog.entries.topLevelItemCount(), 3)
            for index in (0, 2):
                dialog.entries.topLevelItem(index).setCheckState(0, Qt.Checked)
            dialog.remove.click()
            self.assertEqual(dialog.entries.topLevelItemCount(), 1)
            dialog.remove_all.click()
        self.assertFalse(self.window.history.entries())
        self.assertTrue(all(not (self.window.history.root / key).exists() for key in keys))

    def test_context_action_opens_codex_with_a_draft_pointing_to_the_full_transcript(self):
        self._send("Question & réponse", "Réponse Markdown", b"artifact")
        with patch.object(app.QDesktopServices, "openUrl", return_value=True) as open_url:
            self.window.handoff_action.trigger()
        url = urlsplit(open_url.call_args.args[0].toEncoded().data().decode())
        self.assertEqual((url.scheme, url.netloc, url.path), ("codex", "threads", "/new"))
        query = parse_qs(url.query)
        path = Path(query["path"][0])
        self.assertIn("discussion-companion.json", query["prompt"][0])
        context, = json.loads((path / "discussion-companion.json").read_text(encoding="utf-8"))
        self.assertEqual(context["user"], "Question & réponse")
        self.assertEqual(context["assistant"], "Réponse Markdown")
        self.assertEqual(Path(context["files"][0]).read_bytes(), b"artifact")
