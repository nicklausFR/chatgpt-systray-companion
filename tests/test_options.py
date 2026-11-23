import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QDialogButtonBox

import app


QT = QApplication.instance() or QApplication([])
QT.setQuitOnLastWindowClosed(False)


class OptionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings_path = self.root / "settings.ini"
        self.settings = QSettings(str(self.settings_path), QSettings.IniFormat)
        self.settings_patch = patch.object(app, "QSettings", return_value=self.settings)
        self.settings_patch.start()
        with patch.object(app.AppServerClient, "launch"):
            self.window = app.MainWindow(self.root)
        self.client = self.window.client
        self.client.thread_id = "same-thread"
        self.window._thread_ready()
        self.window._set_status("Prêt")
        self.window.show()

    def tearDown(self):
        self.window._shutdown()
        self.window.close()
        self.window.deleteLater()
        QT.processEvents()
        self.settings_patch.stop()
        self.temp.cleanup()

    def test_instructions_survive_relaunch_as_utf8_text(self):
        text = "Réponds en français.\nPrivilégie les réponses courtes."
        self.client.set_custom_instructions(text)
        stored = QSettings(str(self.settings_path), QSettings.IniFormat)
        self.assertEqual(stored.value("custom_instructions"), text)
        other = app.AppServerClient(self.root)
        self.assertEqual(other.custom_instructions, text)
        other.shutdown()

    def send_input(self, question="Bonjour"):
        with patch.object(self.client, "_request") as request:
            self.client.send_message(question)
        self.assertEqual(request.call_args.args[0], "turn/start")
        params = request.call_args.args[1]
        self.assertEqual(params["threadId"], "same-thread")
        return params["input"]

    def test_current_instructions_are_sent_each_turn_and_edits_apply_without_new_chat(self):
        self.client.set_custom_instructions("Réponds en trois lignes.")
        for _index in range(2):
            content = self.send_input()
            self.assertIn("Réponds en trois lignes.", content[0]["text"])
            self.assertEqual(content[-1], {"type": "text", "text": "Bonjour"})
        with patch.object(self.client, "new_thread") as new_thread:
            self.client.set_custom_instructions("Réponds en une phrase.")
            content = self.send_input()
        new_thread.assert_not_called()
        self.assertIn("Réponds en une phrase.", content[0]["text"])
        self.assertNotIn("trois lignes", content[0]["text"])

    def test_clearing_instructions_explicitly_cancels_old_preferences_in_existing_chat(self):
        self.client.set_custom_instructions("Réponds uniquement OUI.")
        self.send_input()
        self.client.set_custom_instructions("  \n  ")
        for _index in range(2):
            content = self.send_input()
            self.assertIn("ont été supprimées", content[0]["text"])
            self.assertNotIn("uniquement OUI", content[0]["text"])

    def test_new_thread_omits_old_preferences_and_client_no_longer_bans_tools(self):
        self.assertNotIn("N'inspecte pas les fichiers", app.CHAT_INSTRUCTIONS)
        self.assertNotIn("n'utilise aucun outil", app.CHAT_INSTRUCTIONS)
        self.client.set_custom_instructions("Anciennes préférences")
        self.send_input()
        self.client.set_custom_instructions("")
        with patch.object(self.client, "_request") as request:
            self.client.new_thread()
        self.assertEqual(request.call_args.args[1]["sandbox"], "workspace-write")
        self.assertNotEqual(Path(request.call_args.args[1]["cwd"]), self.root)
        self.client.thread_id = "same-thread"
        self.assertEqual(len(self.send_input()), 1)

    def edit_dialog(self, text, save):
        observed = []
        def choose():
            dialog = QT.activeModalWidget()
            if isinstance(dialog, app.InstructionsDialog):
                observed.append(True)
                dialog.instructions_editor.setPlainText(text)
                button = QDialogButtonBox.Save if save else QDialogButtonBox.Cancel
                dialog.buttons.button(button).click()
        QTimer.singleShot(20, choose)
        # Avoid a hung test if Qt fails to expose the modal dialog.
        def cancel_visible():
            for widget in QT.topLevelWidgets():
                if isinstance(widget, app.InstructionsDialog) and widget.isVisible():
                    widget.reject()
        guard = QTimer()
        guard.setSingleShot(True)
        guard.timeout.connect(cancel_visible)
        guard.start(1000)
        self.window.instructions_action.trigger()
        guard.stop()
        self.assertTrue(observed)

    def test_options_save_preserves_current_conversation_and_draft(self):
        self.window._append_message("Vous", "Une question", QColor("#aaaaaa"))
        self.window.input.setPlainText("Brouillon")
        with patch.object(self.client, "new_thread") as new_thread:
            self.edit_dialog("Tutoiement, réponses concises.", True)
        new_thread.assert_not_called()
        self.assertEqual(self.client.custom_instructions, "Tutoiement, réponses concises.")
        self.assertIn("Une question", self.window.transcript.toPlainText())
        self.assertEqual(self.window.input.toPlainText(), "Brouillon")

    def test_cancel_does_not_change_saved_instructions_even_during_generation(self):
        self.client.set_custom_instructions("Instructions initiales")
        self.window._set_busy(True)
        self.assertTrue(self.window.options_button.isEnabled())
        self.edit_dialog("Ne pas enregistrer", False)
        self.assertEqual(self.client.custom_instructions, "Instructions initiales")

    def test_options_remain_available_without_codex(self):
        self.client.thread_id = None
        self.window._set_busy(False)
        self.edit_dialog("Utilise le français.", True)
        self.assertEqual(self.client.custom_instructions, "Utilise le français.")


if __name__ == "__main__":
    unittest.main()
