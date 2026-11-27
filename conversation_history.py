"""Local conversation journal and snapshots of generated files."""
from __future__ import annotations

import json
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from PySide6.QtCore import QIODevice, QLockFile, QSaveFile
from PySide6.QtGui import QImage

from artifact_workspace import ArtifactWorkspace
from attachments import Attachment
from generated_files import GeneratedBundle, checked_source, parse_response


@dataclass(frozen=True)
class HistoryEntry:
    key: str
    title: str
    created: datetime


def write_bytes(path: Path, chunks: Iterable[bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    target = QSaveFile(str(path))
    if not target.open(QIODevice.WriteOnly):
        raise OSError(target.errorString())
    try:
        for chunk in chunks:
            if target.write(chunk) != len(chunk):
                raise OSError(target.errorString())
        if not target.commit():
            raise OSError(target.errorString())
    finally:
        if target.isOpen():
            target.cancelWriting()


def copy_file(source: Path, target: Path) -> None:
    with source.open("rb") as stream:
        write_bytes(target, iter(lambda: stream.read(1024 * 1024), b""))


class HistoryTurn:
    def __init__(self, path: Path, exports: Path, data: dict):
        self.path = path
        self.exports = exports
        self.data = data

    def _save(self, data: dict) -> None:
        text = json.dumps(data, ensure_ascii=False)
        write_bytes(self.path / "turn.json", (text.encode("utf-8"),))
        self.data = data

    def checkpoint(self, response: str) -> None:
        if response != self.data["response"]:
            self._save(dict(self.data, response=response))

    def finish(self, response: str, status: str, error: str = "") -> None:
        self._save(dict(self.data, response=response, status=status, error=error))
        target = self.path / "exports"
        if target.is_dir():
            shutil.rmtree(target)
        for bundle in ArtifactWorkspace.collect(self.exports, self.data["before"]):
            for file in bundle.files:
                source = checked_source(file)
                copy_file(source, target / source.relative_to(self.exports))
        self._save(dict(self.data, finalized=True, before={}))

    def bundles(self) -> list[GeneratedBundle]:
        return ArtifactWorkspace.collect(self.path / "exports")

    def save_attachments(self, attachments: list[Attachment]) -> None:
        for index, attachment in enumerate(attachments):
            directory = self.path / "attachments" / str(index)
            if attachment.source is not None:
                copy_file(attachment.source, directory / "original" / attachment.source.name)
            if attachment.preview is not None and attachment.content and attachment.content[0]["type"] == "image":
                directory.mkdir(parents=True, exist_ok=True)
                if not attachment.preview.save(str(directory / "preview.png"), "PNG"):
                    raise OSError(str(directory / "preview.png"))

    def attachments(self) -> list[Attachment]:
        attachments = []
        for index, (name, detail) in enumerate(self.data["attachments"]):
            directory = self.path / "attachments" / str(index)
            original = directory / "original"
            source = next(original.iterdir(), None) if original.is_dir() else None
            preview = QImage(str(directory / "preview.png"))
            content = [{"type": "image"}] if not preview.isNull() else []
            attachments.append(Attachment(name, source, content, detail, preview if content else None))
        return attachments


class HistoryChat:
    def __init__(self, path: Path, lock: QLockFile):
        self.path = path
        self.workspace = path / "workspace"
        self.lock = lock
        self.turns = []
        for journal in sorted((path / "turns").glob("*/turn.json")):
            data = json.loads(journal.read_text(encoding="utf-8"))
            self.turns.append(HistoryTurn(journal.parent, self.workspace / "exports", data))

    def begin(self, prompt: str, attachments: list[Attachment]) -> HistoryTurn:
        exports = self.workspace / "exports"
        turn = HistoryTurn(self.path / "turns" / f"{len(self.turns) + 1:08d}", exports, {})
        turn._save({
            "created": datetime.now(timezone.utc).isoformat(),
            "prompt": prompt,
            "attachments": [(attachment.name, str(attachment.detail)) for attachment in attachments],
            "response": "",
            "status": "",
            "error": "",
            "finalized": False,
            "before": ArtifactWorkspace.signatures(ArtifactWorkspace.files(exports)),
        })
        self.turns.append(turn)
        return turn

    def recover(self) -> None:
        if self.turns and not self.turns[-1].data["finalized"]:
            turn = self.turns[-1]
            data = turn.data
            turn.finish(data["response"], data["status"] or "interrupted", data["error"])

    def context(self, *, include_files: bool = False) -> list[dict]:
        context = []
        for turn in self.turns:
            data = turn.data
            entry = {
                "user": data["prompt"],
                "assistant": parse_response(data["response"]).markdown,
                "attachments": [name for name, _detail in data["attachments"]],
            }
            if include_files:
                entry["files"] = [str(file.source) for bundle in turn.bundles() for file in bundle.files]
            context.append(entry)
        return context

    def close(self) -> None:
        try:
            if not self.turns and self.path.is_dir():
                shutil.rmtree(self.path, ignore_errors=True)
        finally:
            self.lock.unlock()


class ConversationHistory:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def entries(self) -> list[HistoryEntry]:
        entries = []
        for path in self.root.glob("*/turns/00000001/turn.json"):
            key = path.parents[2].name
            try:
                if UUID(key).hex != key or path.resolve() != path:
                    continue
                data = json.loads(path.read_text(encoding="utf-8"))
                title = " ".join(data["prompt"].split()) or ", ".join(name for name, _detail in data["attachments"])
                entries.append(HistoryEntry(key, title, datetime.fromisoformat(data["created"])))
            except (OSError, ValueError, KeyError):
                continue
        return sorted(entries, key=lambda entry: entry.created, reverse=True)

    def open(self, key: str | None = None) -> HistoryChat:
        self.root.mkdir(parents=True, exist_ok=True)
        restoring = key is not None
        key = key if restoring else uuid4().hex
        if UUID(key).hex != key:
            raise ValueError(key)
        path = self.root / key
        if path.resolve() != path:
            raise OSError(str(path))
        lock = QLockFile(str(self.root / (key + ".lock")))
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            raise BlockingIOError(str(path))
        try:
            if restoring and not path.is_dir():
                raise FileNotFoundError(str(path))
            chat = HistoryChat(path, lock)
        except Exception:
            lock.unlock()
            raise
        return chat

    def delete(self, key: str) -> None:
        chat = self.open(key)
        try:
            shutil.rmtree(chat.path)
        finally:
            chat.close()
