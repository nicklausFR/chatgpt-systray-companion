"""Vérification non bloquante du binaire Codex avant le démarrage du serveur."""
from __future__ import annotations

import re

from PySide6.QtCore import QObject, QProcess, QTimer, Signal
from ui_preferences import tr


class CodexProbe(QObject):
    ready = Signal(str, str)
    failed = Signal(object)
    TIMEOUT_MS = 10000

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(lambda: self._fail(tr("Codex ne répond pas. Réessayez ou sélectionnez une autre installation.")))
        self._done = True
        self._path = ""

    def check(self, executable: str) -> None:
        self.cancel()
        self._done = False
        self._path = executable
        self.process.setProgram(executable)
        self.process.setArguments(["--version"])
        self.timer.start(self.TIMEOUT_MS)
        self.process.start()

    def cancel(self) -> None:
        self._done = True
        self.timer.stop()
        if self.process.state() != QProcess.NotRunning:
            self.process.kill()
            self.process.waitForFinished(1000)

    def _error(self, _error) -> None:
        self._fail(tr("Impossible d'exécuter Codex. Vérifiez le fichier sélectionné et son installation."))

    def _fail(self, message: str) -> None:
        if self._done:
            return
        self.cancel()
        self.failed.emit(message)

    def _finished(self, code: int, _status) -> None:
        if self._done:
            return
        output = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        version = re.search(r"^codex-cli\s+(\d+\.\d+\.\d+[^\s]*)\s*$", output, re.MULTILINE)
        if code != 0 or version is None:
            self._fail(tr("Ce fichier n'est pas un moteur Codex utilisable. Installez Codex ou choisissez son fichier codex.exe."))
            return
        self._done = True
        self.timer.stop()
        self.ready.emit(self._path, version.group(1))
