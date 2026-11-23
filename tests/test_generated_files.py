import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import json
import base64
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QPoint, QSettings, QUrl
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import app
from generated_files import GeneratedBundle, GeneratedFile, parse_response, save_file, save_folder, save_zip, validate_bundle


QT = QApplication.instance() or QApplication([])
QT.setQuitOnLastWindowClosed(False)


def payload(kind="folder"):
    files = [{"path": "index.html", "content": "<!doctype html>\n<p>Été</p>\n"}]
    if kind == "folder":
        files.append({"path": "assets/style.css", "content": "body { color: blue; }\n"})
    return {"type": kind, "name": "mon-site" if kind == "folder" else "index.html", "files": files}


def reply(data):
    return "Voici les fichiers.\n\n```companion-files\n" + json.dumps(data, ensure_ascii=False) + "\n```\n"


def zip_payload():
    return {"type": "zip", "name": "image-400.zip", "files": [{
        "path": "images/image.svg", "content": '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400"><rect width="400" height="400" fill="#2563eb"/></svg>'}]}


class GeneratedFileTests(unittest.TestCase):
    def test_zip_contains_actual_files_with_exact_contents_and_relative_paths(self):
        bundle = parse_response(reply(zip_payload())).bundles[0]
        self.assertTrue(bundle.archive)
        self.assertFalse(bundle.folder)
        with tempfile.TemporaryDirectory() as tmp:
            saved = save_zip(bundle, Path(tmp) / bundle.name)
            with zipfile.ZipFile(saved) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(archive.namelist(), ["images/image.svg"])
                self.assertEqual(archive.read("images/image.svg"), bundle.files[0].content.encode("utf-8"))

    def test_zip_failed_replace_preserves_existing_file_and_revalidates_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "archive.zip"
            path.write_bytes(b"original")
            with patch("generated_files.os.replace", side_effect=PermissionError("locked")), self.assertRaises(PermissionError):
                save_zip(validate_bundle(zip_payload()), path)
            self.assertEqual(path.read_bytes(), b"original")
            with self.assertRaises(ValueError):
                save_zip(GeneratedBundle("bad.zip", (GeneratedFile("../escape.txt", "x"),), False, True), path)
            self.assertEqual(list(Path(tmp).iterdir()), [path])

    def test_zip_name_gets_expected_extension(self):
        data = zip_payload()
        data["name"] = "image-400"
        self.assertEqual(validate_bundle(data).name, "image-400.zip")

    def test_parse_delivery_preserves_prose_and_exact_file_contents(self):
        parsed = parse_response(reply(payload()))
        self.assertIn("Voici les fichiers.", parsed.markdown)
        self.assertNotIn("companion-files", parsed.markdown)
        self.assertEqual(len(parsed.bundles), 1)
        self.assertEqual(parsed.bundles[0].files[0].content, payload()["files"][0]["content"])

    def test_ordinary_markdown_and_nested_protocol_examples_are_not_interpreted(self):
        for text in ("## Exemple\n\n```json\n{\"x\": 1}\n```\n", "````markdown\n" + reply(payload()) + "````\n"):
            parsed = parse_response(text)
            self.assertEqual(parsed.markdown, text)
            self.assertFalse(parsed.bundles)

    def test_incomplete_or_invalid_delivery_has_no_saveable_files(self):
        for text in ("```companion-files\n{", "```companion-files\n{bad}\n```", reply({"files": []})):
            parsed = parse_response(text)
            self.assertFalse(parsed.bundles)
            self.assertNotIn("companion-files", parsed.markdown)
            self.assertTrue(parsed.markdown.strip())

    def test_windows_unsafe_paths_and_reserved_names_are_rejected(self):
        for path in ("../secret.txt", "C:/secret.txt", "C:secret.txt", "/tmp/file", "\\\\server\\share\\file",
                     "a/../../file", "a/CON.txt", "NUL", "a/file.txt:stream", "a/file. ", "a//file", "a/./file"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_bundle({"name": "test", "files": [{"path": path, "content": "x"}]})

    def test_duplicate_case_and_file_directory_conflicts_are_rejected(self):
        for paths in (("INDEX.html", "index.HTML"), ("assets", "assets/a.css")):
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                validate_bundle({"files": [{"path": path, "content": "x"} for path in paths]})

    def test_binary_file_bytes_are_not_restricted_by_format(self):
        data = bytes(range(256))
        bundle = validate_bundle({"files": [{"path": "data.anything", "content": base64.b64encode(data).decode(), "encoding": "base64"}]})
        with tempfile.TemporaryDirectory() as tmp:
            saved = save_file(bundle.files[0], Path(tmp) / "data.anything")
            self.assertEqual(saved.read_bytes(), data)

    def test_folder_creates_subdirectories_and_never_replaces_existing_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            existing = parent / "mon-site"
            existing.mkdir()
            original = existing / "index.html"
            original.write_text("original", encoding="utf-8")
            saved = save_folder(validate_bundle(payload()), parent)
            self.assertEqual(saved.name, "mon-site (2)")
            self.assertEqual(original.read_text(encoding="utf-8"), "original")
            self.assertEqual((saved / "assets/style.css").read_bytes(), b"body { color: blue; }\n")
            self.assertIn("Été", (saved / "index.html").read_text(encoding="utf-8"))

    def test_failed_folder_write_cleans_only_the_new_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            keep = parent / "keep.txt"
            keep.write_text("keep", encoding="utf-8")
            with patch.object(Path, "open", side_effect=OSError("write failed")), self.assertRaises(OSError):
                save_folder(validate_bundle(payload()), parent)
            self.assertEqual(list(parent.iterdir()), [keep])

    def test_single_file_preserves_utf8_newlines_and_failed_replace_preserves_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notes.txt"
            file = GeneratedFile("notes.txt", "Été\r\nligne\n")
            save_file(file, path)
            self.assertEqual(path.read_bytes(), file.content.encode("utf-8"))
            with patch("generated_files.os.replace", side_effect=PermissionError("locked")), self.assertRaises(PermissionError):
                save_file(GeneratedFile("notes.txt", "nouveau"), path)
            self.assertEqual(path.read_bytes(), file.content.encode("utf-8"))
            self.assertEqual(list(Path(tmp).iterdir()), [path])


class GeneratedFileUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = QSettings(str(self.root / "settings.ini"), QSettings.IniFormat)
        with patch.object(app, "QSettings", return_value=self.settings), patch.object(app.AppServerClient, "launch"):
            self.window = app.MainWindow(self.root)
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

    def render_bundle(self, kind="folder"):
        self.window._assistant_text = reply(payload(kind))
        self.window._render_assistant_message()
        QTest.qWait(10)
        key = next(iter(self.window.transcript._generated_bundles))
        return key, self.window.transcript._generated_bundles[key]

    def test_response_shows_file_card_and_native_folder_selection_saves_tree(self):
        key, bundle = self.render_bundle()
        text = self.window.transcript.toPlainText()
        self.assertIn("mon-site", text)
        self.assertIn("assets/style.css", text)
        self.assertIn("Enregistrer le dossier", text)
        self.assertNotIn("companion-files", text)
        with patch.object(app.QFileDialog, "getExistingDirectory", return_value=str(self.root)), patch.object(app.QToolTip, "showText"):
            self.window.transcript._follow_link(QUrl(key))
        self.assertTrue((self.root / "mon-site/assets/style.css").is_file())
        self.assertEqual(Path(self.settings.value("export_directory")), self.root)

    def test_native_save_as_selects_single_file_destination(self):
        _key, bundle = self.render_bundle("file")
        destination = self.root / "choisi.html"
        def choose(*_args):
            self.assertTrue(self.window._native_dialog_open)
            return str(destination), ""
        with patch.object(app.QFileDialog, "getSaveFileName", side_effect=choose), patch.object(app.QToolTip, "showText"):
            self.window._save_generated_bundle(bundle)
        self.assertFalse(self.window._native_dialog_open)
        self.assertEqual(destination.read_text(encoding="utf-8"), bundle.files[0].content)

    def test_zip_card_saves_archive_directly_through_windows_save_as(self):
        self.window._assistant_text = reply(zip_payload())
        self.window._render_assistant_message()
        text = self.window.transcript.toPlainText()
        self.assertIn("image-400.zip", text)
        self.assertIn("Archive ZIP", text)
        self.assertIn("Enregistrer le ZIP", text)
        self.assertNotIn("UTF-8", text)
        key = next(iter(self.window.transcript._generated_bundles))
        destination = self.root / "image.zip"
        def choose(*args):
            self.assertEqual(Path(args[2]).name, "image-400.zip")
            self.assertIn("*.zip", args[3])
            return str(destination), ""
        with patch.object(app.QFileDialog, "getSaveFileName", side_effect=choose), patch.object(app.QToolTip, "showText"):
            self.window.transcript._follow_link(QUrl(key))
        with zipfile.ZipFile(destination) as archive:
            self.assertEqual(archive.namelist(), ["images/image.svg"])

    def test_inline_download_link_replaces_card_for_same_file_only(self):
        exports = self.root / "exports"
        exports.mkdir()
        archive_path = exports / "contenu aleatoire.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("notes.txt", "test")
        notes_path = exports / "notes.md"
        notes_path.write_text("Notes", encoding="utf-8")
        artifacts = [GeneratedBundle(path.name, (GeneratedFile(path.name, source=path, source_root=exports),), False)
                     for path in (archive_path, notes_path)]

        url = QUrl("./exports/contenu%20aleatoire.zip")
        self.window._assistant_text = f"[Télécharger le ZIP]({url.toEncoded().data().decode('ascii')}) — contient des fichiers."
        self.window._render_assistant_message(artifacts)
        transcript = self.window.transcript
        text = transcript.toPlainText()
        self.assertIn("Télécharger le ZIP — contient des fichiers.", text)
        self.assertEqual([bundle.name for bundle in transcript._generated_bundles.values()], ["notes.md"])
        displayed_url = QUrl(transcript.document().find("Télécharger le ZIP").charFormat().anchorHref())
        self.assertEqual(displayed_url, url)
        destination = self.root / "choisi.zip"
        with patch.object(app.QFileDialog, "getSaveFileName", return_value=(str(destination), "")) as dialog, \
             patch.object(app.QToolTip, "showText"):
            transcript._follow_link(displayed_url)
        dialog.assert_called_once()
        self.assertEqual(destination.read_bytes(), archive_path.read_bytes())

    def test_cancelled_save_creates_no_files(self):
        _key, bundle = self.render_bundle()
        before = set(self.root.iterdir())
        with patch.object(app.QFileDialog, "getExistingDirectory", return_value=""):
            self.window._save_generated_bundle(bundle)
        self.assertEqual(set(self.root.iterdir()), before)

    def test_save_error_is_reported_and_old_response_retains_export_data(self):
        key, bundle = self.render_bundle()
        self.window._assistant_text = "Une autre réponse."
        self.window._render_assistant_message()
        self.assertEqual(self.window.transcript._generated_bundles[key], bundle)
        with patch.object(app.QFileDialog, "getExistingDirectory", return_value=str(self.root)), \
             patch.object(app, "save_folder", side_effect=PermissionError("denied")), \
             patch.object(app.QMessageBox, "critical") as error:
            self.window._save_generated_bundle(bundle)
        error.assert_called_once()
        self.window.transcript.clear()
        self.assertFalse(self.window.transcript._generated_bundles)


if __name__ == "__main__":
    unittest.main()
