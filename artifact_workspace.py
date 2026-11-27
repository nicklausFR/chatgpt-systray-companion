"""Manage the working files created by the assistant."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from uuid import uuid4

from generated_files import GeneratedBundle, GeneratedFile


class ArtifactWorkspace:
    def __init__(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="chatgpt-systray-companion-")
        self.root = Path(self._temporary.name).resolve()
        self.current: Path | None = None
        self._before = {}

    def new_chat(self, directory: Path | None = None) -> Path:
        current = directory if directory is not None else self.root / uuid4().hex
        for folder in (current / "exports", current / "inputs"):
            if folder.resolve() != folder.absolute():
                raise OSError(str(folder))
            folder.mkdir(parents=True, exist_ok=True)
        self.current = current
        self._before = {}
        return self.current

    @staticmethod
    def files(exports: Path) -> dict[str, Path]:
        if exports.is_symlink() or exports.resolve() != exports.absolute() or not exports.is_dir():
            return {}
        found = {}
        for parent, directories, names in os.walk(exports, followlinks=False):
            directories[:] = [name for name in directories if not (Path(parent) / name).is_symlink()
                              and (Path(parent) / name).resolve().is_relative_to(exports)]
            for name in names:
                file = Path(parent) / name
                try:
                    if file.is_symlink() or not file.resolve().is_relative_to(exports) or not file.is_file():
                        continue
                    found[file.relative_to(exports).as_posix()] = file
                except OSError:
                    continue
        return found

    @staticmethod
    def signatures(files: dict[str, Path]) -> dict[str, list[int]]:
        signatures = {}
        for key, file in files.items():
            try:
                info = file.stat()
            except FileNotFoundError:
                continue
            signatures[key] = [info.st_size, info.st_mtime_ns]
        return signatures

    def snapshot(self) -> None:
        self._before = self.signatures(self.files(self.current / "exports")) if self.current else {}

    def deliveries(self) -> list[GeneratedBundle]:
        return self.collect(self.current / "exports", self._before) if self.current else []

    @classmethod
    def collect(cls, exports: Path, before: dict[str, list[int]] | None = None) -> list[GeneratedBundle]:
        files = cls.files(exports)
        if before is not None:
            current = cls.signatures(files)
            files = {key: files[key] for key in current}
            groups = {key.split("/", 1)[0] for key, signature in current.items() if before.get(key) != signature}
        else:
            groups = {key.split("/", 1)[0] for key in files}
        bundles = []
        for group in sorted(groups):
            if group in files:
                bundles.append(GeneratedBundle(group, (GeneratedFile(group, source=files[group], source_root=exports),), False))
            else:
                entries = tuple(GeneratedFile(key[len(group) + 1:], source=file, source_root=exports)
                                for key, file in sorted(files.items()) if key.startswith(group + "/"))
                if entries:
                    bundles.append(GeneratedBundle(group, entries, True))
        return bundles

    def close(self) -> None:
        # Cleanup is limited to this exact temporary root, never a user-selected destination.
        if (not self.root.is_symlink() and self.root.resolve() == Path(self._temporary.name).absolute()
                and self.root.parent == Path(tempfile.gettempdir()).resolve()
                and self.root.name.startswith("chatgpt-systray-companion-")):
            self._temporary.cleanup()
