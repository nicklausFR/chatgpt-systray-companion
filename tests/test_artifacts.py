import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QSettings
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

import app
from artifact_workspace import ArtifactWorkspace
from attachments import Attachment
from generated_files import GeneratedBundle, GeneratedFile, save_file, save_folder, save_zip

QT = QApplication.instance() or QApplication([])


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.workspace = ArtifactWorkspace()
        self.root = self.workspace.new_chat()

    def tearDown(self):
        self.workspace.close()

    def test_actual_binary_files_and_archives_are_delivered_without_format_filtering(self):
        outputs = self.root / "exports"
        raw = bytes(range(256))
        (outputs / "data.custom").write_bytes(raw)
        with zipfile.ZipFile(outputs / "image.zip", "w") as archive:
            archive.writestr("image.png", raw)
        bundles = self.workspace.deliveries()
        self.assertEqual([bundle.name for bundle in bundles], ["data.custom", "image.zip"])
        with tempfile.TemporaryDirectory() as tmp:
            for bundle in bundles:
                target = save_file(bundle.files[0], Path(tmp) / bundle.name)
                self.assertEqual(target.read_bytes(), (outputs / bundle.name).read_bytes())
            with zipfile.ZipFile(Path(tmp) / "image.zip") as archive:
                self.assertEqual(archive.read("image.png"), raw)

    def test_only_changed_deliveries_appear_and_folder_contains_complete_project(self):
        project = self.root / "exports/project"
        project.mkdir()
        (project / "one.txt").write_text("old", encoding="utf-8")
        self.workspace.snapshot()
        self.assertFalse(self.workspace.deliveries())
        (project / "two.bin").write_bytes(b"\0\xff")
        bundle, = self.workspace.deliveries()
        self.assertTrue(bundle.folder)
        self.assertEqual([file.path for file in bundle.files], ["one.txt", "two.bin"])
        with tempfile.TemporaryDirectory() as tmp:
            folder = save_folder(bundle, Path(tmp))
            archive_path = save_zip(bundle, Path(tmp) / "project.zip")
            self.assertEqual((folder / "two.bin").read_bytes(), b"\0\xff")
            with zipfile.ZipFile(archive_path) as archive:
                self.assertEqual(archive.read("two.bin"), b"\0\xff")

    def test_source_outside_artifact_root_is_rejected_before_writing(self):
        source = self.root / "inputs/private.bin"
        source.write_bytes(b"private")
        file = GeneratedFile("private.bin", source=source, source_root=self.root / "exports")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                save_file(file, Path(tmp) / "private.bin")
            self.assertFalse(list(Path(tmp).iterdir()))

    def test_large_native_files_are_streamed_and_cleanup_preserves_saved_files(self):
        source = self.root / "exports/large.dat"
        block = b"12345678" * 131072
        with source.open("wb") as stream:
            for _index in range(22):
                stream.write(block)
        bundle, = self.workspace.deliveries()
        with tempfile.TemporaryDirectory() as tmp:
            saved = save_file(bundle.files[0], Path(tmp) / bundle.name)
            self.workspace.close()
            self.assertFalse(self.root.exists())
            self.assertEqual(saved.stat().st_size, len(block) * 22)
            with saved.open("rb") as stream:
                self.assertEqual(stream.read(len(block)), block)

    def test_new_chat_has_distinct_workspace_and_preserves_prior_artifacts_until_exit(self):
        old = self.root / "exports/previous.dat"
        old.write_bytes(b"old")
        new = self.workspace.new_chat()
        self.assertNotEqual(new, self.root)
        self.assertTrue(old.exists())
        self.assertFalse(self.workspace.deliveries())

    def test_attachment_original_is_staged_and_only_workspace_is_writable(self):
        settings = QSettings(str(self.root / "settings.ini"), QSettings.IniFormat)
        with patch.object(app, "QSettings", return_value=settings):
            client = app.AppServerClient(self.root)
        client.artifacts = self.workspace
        client.thread_id = "fixture"
        source = self.root / "original.bin"
        source.write_bytes(b"\0\xfforiginal")
        attachment = Attachment("original.bin", source, [], "Fichier")
        with patch.object(client, "_request") as request:
            client.send_message("Utilise ce fichier", [attachment])
        params = request.call_args.args[1]
        self.assertEqual(params["sandboxPolicy"]["writableRoots"], [str(self.root)])
        self.assertTrue(params["sandboxPolicy"]["excludeTmpdirEnvVar"])
        staged, = (self.root / "inputs").iterdir()
        self.assertEqual(staged.read_bytes(), source.read_bytes())
        self.assertIn("inputs/" + staged.name, params["input"][1]["text"])
        client.shutdown()

    def test_final_response_does_not_concatenate_tool_progress_messages(self):
        settings = QSettings(str(self.root / "settings.ini"), QSettings.IniFormat)
        with patch.object(app, "QSettings", return_value=settings), patch.object(app.AppServerClient, "launch"):
            window = app.MainWindow(self.root)
        try:
            window.client.thread_id = "fixture"
            for message_id, text in (("one", "Je prépare les fichiers."), ("two", "Voici les fichiers.")):
                window.client._handle_message({"method": "item/started", "params": {"threadId": "fixture", "item": {"id": message_id, "type": "agentMessage"}}})
                window.client._handle_message({"method": "item/agentMessage/delta", "params": {"threadId": "fixture", "delta": text}})
            self.assertEqual(window._assistant_text, "Voici les fichiers.")
        finally:
            window._shutdown()
            window.close()
            window.deleteLater()
            QT.processEvents()
