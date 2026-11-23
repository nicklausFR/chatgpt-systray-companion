"""Construit l'exécutable Windows, avec Python/Qt inclus et sans console."""
from __future__ import annotations

import argparse
import os
import struct
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QLibraryInfo
from PySide6.QtWidgets import QApplication

from app import APP_EXECUTABLE, APP_NAME, APP_VERSION, MainWindow


def write_icon(path: Path) -> None:
    icon = MainWindow._tray_graphic(256)
    images: list[tuple[int, bytes]] = []
    for size in (16, 24, 32, 48, 64, 128, 256):
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        if not icon.pixmap(size, size).save(buffer, "PNG"):
            raise RuntimeError("Impossible de générer l'icône Windows.")
        images.append((size, bytes(buffer.data())))
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    directory = bytearray()
    for size, data in images:
        directory.extend(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    path.write_bytes(header + directory + b"".join(data for _, data in images))


def main() -> int:
    if sys.platform != "win32":
        raise SystemExit("Cette compilation doit être exécutée sous Windows.")
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Construire l'exécutable Windows")
    parser.add_argument("--dist-dir", type=Path, default=root / "dist", help="dossier de sortie de l'exécutable")
    args = parser.parse_args()
    distribution = args.dist_dir.resolve()
    build = root / "build"
    build.mkdir(exist_ok=True)
    subprocess.run([sys.executable, "-B", str(root / "translations/build_catalogs.py")], cwd=root, check=True)
    application = QApplication([])
    icon = build / f"{APP_EXECUTABLE}.ico"
    write_icon(icon)
    version = tuple(int(part) for part in APP_VERSION.split(".")) + (0,)
    version_file = build / "version-info.txt"
    version_file.write_text(
        f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={version!r}, prodvers={version!r},
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040c04b0', [
    StringStruct('FileDescription', '{APP_NAME}'),
    StringStruct('FileVersion', '{APP_VERSION}'),
    StringStruct('InternalName', '{APP_EXECUTABLE}'),
    StringStruct('OriginalFilename', '{APP_EXECUTABLE}.exe'),
    StringStruct('ProductName', '{APP_NAME}'),
    StringStruct('ProductVersion', '{APP_VERSION}')
  ])]), VarFileInfo([VarStruct('Translation', [1036, 1200])])])
""",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYINSTALLER_CONFIG_DIR"] = str(build / "cache")
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed",
         "--name", APP_EXECUTABLE, "--icon", str(icon), "--version-file", str(version_file),
         "--distpath", str(distribution), "--workpath", str(build / "pyinstaller"),
         "--specpath", str(build), "--noupx", "--exclude-module", "PyQt5",
         "--add-data", f"{root / 'translations/companion_en.qm'};translations",
         "--add-data", f"{Path(QLibraryInfo.path(QLibraryInfo.TranslationsPath)) / 'qtbase_fr.qm'};translations",
         "--exclude-module", "PyQt6", "--exclude-module", "PySide2", str(root / "app.py")],
        cwd=root, env=environment, check=True,
    )
    print(f"Exécutable prêt : {distribution / (APP_EXECUTABLE + '.exe')}")
    application.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
