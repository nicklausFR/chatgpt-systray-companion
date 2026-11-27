import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor, QImage, QTextFormat
from PySide6.QtWidgets import QApplication, QDialogButtonBox

import app
from attachments import AttachmentRow, prepare_attachment
from generated_files import GeneratedBundle, GeneratedFile
from translations.build_catalogs import check_catalog
from ui_preferences import LIGHT_COLORS, tr


QT = QApplication.instance() or QApplication([])
QT.setQuitOnLastWindowClosed(False)


class UiPreferencesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings_path = self.root / "settings.ini"
        self.settings = QSettings(str(self.settings_path), QSettings.IniFormat)
        self.settings.setValue("language", "fr")
        self.settings.setValue("theme", "dark")
        self.settings_patch = patch.object(app, "QSettings", return_value=self.settings)
        self.settings_patch.start()
        self.launch_patch = patch.object(app.AppServerClient, "launch")
        self.launch_patch.start()
        self.windows = []
        self.window = self.make_window()

    def make_window(self):
        window = app.MainWindow(self.root)
        window.client.thread_id = "same-thread"
        window._thread_ready()
        window._set_status(tr("Prêt"))
        self.windows.append(window)
        QT.processEvents()
        return window

    def tearDown(self):
        self.window.preferences.set_language("fr", persist=False)
        for window in self.windows:
            window._shutdown()
            window.close()
            window.deleteLater()
        QT.processEvents()
        self.launch_patch.stop()
        self.settings_patch.stop()
        self.temp.cleanup()

    def choose(self, group, value):
        next(action for action in group.actions() if action.data() == value).trigger()

    def test_switch_language_live_preserves_generation_draft_markdown_and_account(self):
        window = self.window
        window._account_ready("person@example.test")
        window.client.rate_limits_changed.emit({"codex": {"primary": {"windowDurationMins": 300, "usedPercent": 30}}}, False)
        model = {"model": "fixture", "displayName": "Fixture",
                 "supportedReasoningEfforts": [{"reasoningEffort": "low"}]}
        window.client.models = [model]
        window.client._model = "fixture"
        window.client._effort = "low"
        window._populate_models([model])
        window._append_message("Vous", "Question en français", QColor("#a8abb1"))
        markdown = "## Hello\n\n```python\nprint('Vous')\n```"
        window._assistant_text = markdown
        window._render_assistant_message()
        window.input.setPlainText("Brouillon inchangé")
        window._assistant_text = "Réponse en cours, à conserver"
        window._set_busy(True)
        window.client.status_changed.emit(tr("Réponse en cours…"))
        self.choose(window.language_group, "en")
        self.assertEqual(window.new_action.text(), "New chat")
        self.assertEqual(window.account_menu.title(), "Account")
        self.assertEqual(window.account_action.text(), "person@example.test")
        self.assertEqual(window.account_button.text(), "person@example.test  ▾")
        self.assertEqual(window.quota_actions[0].text(), "5 h · 70% remaining")
        self.assertEqual(window.model_button.text(), "Fixture · Low  ▾")
        self.assertFalse(window.model_button.isEnabled())
        self.assertEqual(window.login_action.text(), "Sign in again…")
        self.assertEqual(window.instructions_action.text(), "Instructions…")
        self.assertEqual(window.theme_menu.title(), "Appearance")
        self.assertEqual(window.status_label.text(), "Generating response…")
        self.assertEqual(window.tray_icon.quit_action.text(), "Quit")
        self.assertEqual(window.input.toPlainText(), "Brouillon inchangé")
        self.assertEqual(window._assistant_text, "Réponse en cours, à conserver")
        self.assertEqual(window.client.thread_id, "same-thread")
        self.assertTrue(window._busy)
        self.assertIn("Question en français", window.transcript.toPlainText())
        self.assertNotIn("You\n", window.transcript.toPlainText())
        self.assertIn("print('Vous')", window.transcript.toPlainText())
        response = next(frame for frame in window.transcript.document().rootFrame().childFrames()
                        if frame.frameFormat().hasProperty(app.RESPONSE_MARKDOWN_PROPERTY))
        self.assertEqual(response.frameFormat().property(app.RESPONSE_MARKDOWN_PROPERTY), markdown)
        self.choose(window.language_group, "fr")
        self.assertEqual(window.new_action.text(), "Nouvelle discussion")
        self.assertEqual(window.account_button.text(), "person@example.test  ▾")
        self.assertEqual(window.quota_actions[0].text(), "5 h · 70 % restants")
        self.assertEqual(window.model_button.text(), "Fixture · Faible  ▾")
        self.assertEqual(window.status_label.text(), "Réponse en cours…")
        self.assertIn("Question en français", window.transcript.toPlainText())
        self.assertNotIn("Vous\n", window.transcript.toPlainText())

    def test_setup_dialog_attachment_and_standard_qt_actions_follow_language(self):
        window = self.window
        with patch.object(app, "find_codex", return_value=None):
            # Invoke the implementation while launch remains disabled elsewhere.
            window._codex_setup_required(tr("Configuration de Codex requise"))
        path = self.root / "notes.txt"
        path.write_text("Contenu inchangé", encoding="utf-8")
        row = AttachmentRow("key", path.name, path, window)
        row.set_result(prepare_attachment(path), "")
        self.choose(window.language_group, "en")
        self.assertEqual(window.setup_title.text(), "Set up Codex")
        self.assertEqual(window.setup_message.text(), "Codex setup required")
        self.assertEqual(window.account_action.text(), "Codex unavailable")
        self.assertEqual(window.account_button.text(), "Codex unavailable  ▾")
        self.assertEqual(row.detail.text(), "Text · 1 KB")
        self.assertEqual(row.icon.toolTip(), "Open file")
        dialog = app.InstructionsDialog("Préférence inchangée", window)
        self.assertEqual(dialog.buttons.button(QDialogButtonBox.Save).text(), "Save")
        self.assertEqual(dialog.buttons.button(QDialogButtonBox.Cancel).text(), "Cancel")
        self.assertEqual(dialog.instructions_editor.toPlainText(), "Préférence inchangée")
        self.choose(window.language_group, "fr")
        self.assertEqual(dialog.buttons.button(QDialogButtonBox.Save).text(), "Enregistrer")
        self.assertEqual(row.detail.text(), "Texte · 1 Ko")
        menu = window.input.createStandardContextMenu()
        self.assertTrue(any("Annuler" in action.text() for action in menu.actions()))
        menu.deleteLater()
        dialog.deleteLater()
        row.deleteLater()

    def test_theme_switch_recolors_existing_content_tables_icons_and_dialogs(self):
        window = self.window
        window._append_message("Vous", "Question", QColor("#a8abb1"))
        window._assistant_text = "| A | B |\n|---|---|\n| Un | Deux |\n\n```txt\n#333333\n```"
        bundle = GeneratedBundle("notes.txt", (GeneratedFile("notes.txt", "exact bytes"),), False)
        window._render_assistant_message([bundle])
        dialog = app.InstructionsDialog("", window)
        image = QImage(20, 20, QImage.Format_RGB32)
        image.fill(Qt.red)
        preview = app.ImagePreview(image, window)
        before = window.transcript.toPlainText()
        for theme in ("light", "dark", "light"):
            self.choose(window.theme_group, theme)
            expected = LIGHT_COLORS["#333333"] if theme == "light" else "#333333"
            question = next(frame for frame in window.transcript.document().rootFrame().childFrames()
                            if frame.frameFormat().property(QTextFormat.UserProperty))
            self.assertEqual(question.frameFormat().background().color(), QColor(expected))
            self.assertEqual(window.transcript.toPlainText(), before)
            self.assertEqual(window.input_fade.top.color, QColor(expected))
            self.assertIn("#fafafa" if theme == "light" else "#252525", window.styleSheet())
            self.assertIn("#fafafa" if theme == "light" else "#252525", dialog.styleSheet())
            self.assertIn("#f2f2f2" if theme == "light" else "#202020", preview.scroll_area.styleSheet())
        dialog.deleteLater()
        preview.deleteLater()

    def test_automatic_theme_follows_qt_windows_notifications_and_forced_theme_ignores_them(self):
        window = self.window
        self.choose(window.theme_group, "auto")
        for scheme, expected in ((Qt.ColorScheme.Dark, "dark"), (Qt.ColorScheme.Light, "light")):
            QT.styleHints().colorSchemeChanged.emit(scheme)
            self.assertEqual(window.preferences.effective_theme, expected)
            self.assertEqual(window.preferences.theme, "auto")
            self.assertEqual(self.settings.value("theme"), "auto")
        self.choose(window.theme_group, "dark")
        QT.styleHints().colorSchemeChanged.emit(Qt.ColorScheme.Light)
        self.assertEqual(window.preferences.effective_theme, "dark")

    def test_generated_card_language_changes_without_touching_names_or_export_bytes(self):
        bundle = GeneratedBundle("dossier", (GeneratedFile("français.txt", "octets inchangés"),), True)
        self.window._render_assistant_message([bundle])
        self.choose(self.window.language_group, "en")
        text = self.window.transcript.toPlainText()
        self.assertIn("Generated folder · 1 file", text)
        self.assertIn("Save folder…", text)
        self.assertIn("français.txt", text)
        self.assertEqual(bundle.files[0].content, "octets inchangés")
        self.choose(self.window.language_group, "fr")
        self.assertIn("Dossier généré · 1 fichier", self.window.transcript.toPlainText())

    def test_choices_persist_and_are_restored_on_relaunch(self):
        self.choose(self.window.language_group, "en")
        self.choose(self.window.theme_group, "light")
        saved = QSettings(str(self.settings_path), QSettings.IniFormat)
        self.assertEqual(saved.value("language"), "en")
        self.assertEqual(saved.value("theme"), "light")
        other = self.make_window()
        self.assertEqual(other.preferences.language, "en")
        self.assertEqual(other.preferences.effective_theme, "light")
        self.assertEqual(other.new_action.text(), "New chat")
        self.assertTrue(next(action for action in other.theme_group.actions() if action.data() == "light").isChecked())

    def test_catalog_has_complete_translations_and_matching_placeholders(self):
        check_catalog()


if __name__ == "__main__":
    unittest.main()
