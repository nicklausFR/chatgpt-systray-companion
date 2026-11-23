"""Own the temporary files created by the assistant, independently of formats."""
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

    def new_chat(self) -> Path:
        self.current = self.root / uuid4().hex
        (self.current / "exports").mkdir(parents=True)
        (self.current / "inputs").mkdir()
        self._before = {}
        return self.current

    def _files(self) -> dict[str, Path]:
        if self.current is None:
            return {}
        exports = self.current / "exports"
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

    def snapshot(self) -> None:
        self._before = {key: (file.stat().st_size, file.stat().st_mtime_ns) for key, file in self._files().items()}

    def deliveries(self) -> list[GeneratedBundle]:
        files = self._files()
        changed = {key for key, file in files.items()
                   if self._before.get(key) != (file.stat().st_size, file.stat().st_mtime_ns)}
        groups = sorted({key.split("/", 1)[0] for key in changed})
        bundles = []
        root = self.current / "exports" if self.current else self.root
        for group in groups:
            if group in files:
                bundles.append(GeneratedBundle(group, (GeneratedFile(group, source=files[group], source_root=root),), False))
            else:
                entries = tuple(GeneratedFile(key[len(group) + 1:], source=file, source_root=root)
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
