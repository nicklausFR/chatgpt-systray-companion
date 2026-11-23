from __future__ import annotations

from ui_preferences import tr, ui_text, ui_color, themed_stylesheet

from base64 import b64encode
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any
from xml.etree import ElementTree
from zipfile import ZipFile

from PySide6.QtCore import QBuffer, QIODevice, QMimeData, QObject, QRunnable, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QImage, QImageReader, QPainter, QPixmap
from PySide6.QtPdf import QPdfDocument
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QStyle, QToolButton, QVBoxLayout


@dataclass(frozen=True)
class Attachment:
    name: str
    source: Path | None
    content: list[dict[str, Any]]
    detail: str
    preview: QImage | None = None


def attachment_badge(attachment: Attachment) -> QImage:
    kind = attachment.detail.split(" · ", 1)[0]
    label = "PDF" if kind == "PDF" else (Path(attachment.name).suffix.lstrip(".").upper() or "TXT")[:4]
    color = ui_color("#c65b52") if kind == "PDF" else ui_color("#5275b7")
    image = QImage(48, 48, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    painter.drawRoundedRect(5, 2, 38, 44, 5, 5)
    painter.setFont(QFont("Segoe UI", 9, QFont.Bold))
    painter.setPen(QColor("white"))
    painter.drawText(image.rect(), Qt.AlignCenter, label)
    painter.end()
    return image


def local_paths_from_text(text: str) -> list[Path]:
    paths = []
    for line in text.strip().splitlines():
        value = line.strip().strip('"')
        url = QUrl(value)
        if url.isLocalFile():
            paths.append(Path(url.toLocalFile()))
        elif Path(value).is_absolute() and Path(value).is_file():
            paths.append(Path(value))
        else:
            return []
    return paths


def local_paths_from_mime(mime: QMimeData) -> list[Path]:
    if mime.hasUrls() and all(url.isLocalFile() for url in mime.urls()):
        return [Path(url.toLocalFile()) for url in mime.urls()]
    return local_paths_from_text(mime.text()) if mime.hasText() else []


def _image_content(image: QImage) -> dict[str, Any]:
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise ValueError(tr("Impossible de préparer cette image."))
    encoded = b64encode(bytes(buffer.data())).decode("ascii")
    buffer.close()
    return {"type": "image", "url": f"data:image/png;base64,{encoded}"}


def _size_label(size: int) -> str:
    if size >= 1024 * 1024:
        return tr("{size:.1f} Mo").format(size=size / (1024 * 1024))
    return tr("{size} Ko").format(size=max(1, round(size / 1024)))


def _pdf_content(path: Path, cancelled: Event) -> tuple[list[dict[str, Any]], QImage, int]:
    document = QPdfDocument()
    error = document.load(str(path))
    if error != QPdfDocument.Error.None_:
        raise ValueError(tr("Impossible de lire ce PDF. Vérifiez qu’il est lisible et sans mot de passe."))
    content = []
    preview = QImage()
    pages = document.pageCount()
    try:
        for page in range(pages):
            if cancelled.is_set():
                raise RuntimeError(tr("Préparation annulée."))
            text = document.getAllText(page).text()
            content.append({"type": "text", "text": f"Pièce jointe {path.name} · page {page + 1}/{pages}\n{text}"})
            size = document.pagePointSize(page).toSize().scaled(QSize(1600, 1600), Qt.KeepAspectRatio)
            image = document.render(page, size)
            if image.isNull():
                raise ValueError(tr('Impossible de lire la page {page_1} du PDF.').format(page_1=page + 1))
            content.append(_image_content(image))
            if page == 0:
                preview = image
    finally:
        document.close()
    return content, preview, pages


def _docx_text(path: Path) -> str:
    with ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []
    for paragraph in root.iter(namespace + "p"):
        parts = []
        for node in paragraph.iter():
            if node.tag == namespace + "t":
                parts.append(node.text or "")
            elif node.tag == namespace + "tab":
                parts.append("\t")
            elif node.tag in (namespace + "br", namespace + "cr"):
                parts.append("\n")
        paragraphs.append("".join(parts))
    return "\n".join(paragraphs)


def prepare_attachment(source: Path | QImage, name: str | None = None, cancelled: Event | None = None) -> Attachment:
    cancelled = cancelled or Event()
    if cancelled.is_set():
        raise RuntimeError(tr("Préparation annulée."))
    if isinstance(source, QImage):
        return Attachment(name or tr("Image collée.png"), None, [_image_content(source)],
                          tr("Image · {width} × {height}").format(width=source.width(), height=source.height()), source)
    path = source.resolve(strict=True)
    if not path.is_file():
        raise ValueError(tr("Choisissez un fichier."))
    size = _size_label(path.stat().st_size)
    if path.suffix.lower() == ".pdf":
        content, preview, pages = _pdf_content(path, cancelled)
        if not pages:
            raise ValueError(tr("Ce PDF ne contient aucune page."))
        detail = tr("PDF · {pages} page · {size}") if pages == 1 else tr("PDF · {pages} pages · {size}")
        return Attachment(path.name, path, content, detail.format(pages=pages, size=size), preview)
    if path.suffix.lower() == ".docx":
        text = _docx_text(path)
        kind = "DOCX"
    elif QImageReader(str(path)).canRead():
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        image = reader.read()
        if image.isNull():
            raise ValueError(tr("Impossible de lire cette image."))
        return Attachment(path.name, path, [_image_content(image)],
                          tr("Image · {width} × {height} · {size}").format(width=image.width(), height=image.height(), size=size), image)
    else:
        raw = path.read_bytes()
        if raw.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
            text = raw.decode("utf-32")
        elif raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = raw.decode("utf-16")
        else:
            if b"\x00" in raw:
                raise ValueError(tr("Format non pris en charge. Utilisez un PDF, DOCX, une image ou un fichier texte."))
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = raw.decode("cp1252")
        kind = tr("Texte")
    content = [{"type": "text", "text": f"Contenu de la pièce jointe {path.name}\n\n{text}"}]
    return Attachment(path.name, path, content, tr("{kind} · {size}").format(kind=kind, size=size))


class AttachmentSignals(QObject):
    finished = Signal(str, object, object)


class AttachmentLoader(QRunnable):
    def __init__(self, key: str, source: Path | QImage, name: str):
        super().__init__()
        self.key = key
        self.source = source
        self.name = name
        self.cancelled = Event()
        self.signals = AttachmentSignals()

    def run(self) -> None:
        try:
            attachment = prepare_attachment(self.source, self.name, self.cancelled)
            self.signals.finished.emit(self.key, attachment, "")
        except Exception as error:
            self.signals.finished.emit(self.key, None, error.args[0] if error.args and isinstance(error.args[0], str) else str(error))


class AttachmentRow(QFrame):
    remove_requested = Signal(str)
    preview_requested = Signal(QImage)

    def __init__(self, key: str, name: str, source: Path | None, parent=None):
        super().__init__(parent)
        self.key = key
        self.name = name
        self.source = source
        self._preview: QImage | None = None
        self.setObjectName("attachmentRow")
        self.icon = QToolButton()
        self.icon.setCursor(Qt.PointingHandCursor)
        self.icon.setAutoRaise(True)
        self.icon.setFixedSize(36, 36)
        self.icon.setIconSize(QSize(32, 32))
        self.icon.setIcon(self.style().standardIcon(QStyle.SP_FileIcon))
        self.icon.setEnabled(False)
        self.icon.clicked.connect(self._open_preview)
        self.name_label = QLabel(name)
        self.name_label.setTextFormat(Qt.PlainText)
        self.name_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        ui_text(self.name_label, 'setToolTip', str(source or name))
        self.detail = ui_text(QLabel(), 'setText', tr("Préparation…"))
        self.detail.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        themed_stylesheet(self.detail, "color: #aaaaaa; font-size: 11px;")
        texts = QVBoxLayout()
        texts.setSpacing(0)
        texts.addWidget(self.name_label)
        texts.addWidget(self.detail)
        remove = QToolButton()
        remove.setCursor(Qt.PointingHandCursor)
        remove.setAutoRaise(True)
        ui_text(remove, 'setText', "×")
        ui_text(remove, 'setToolTip', tr("Retirer la pièce jointe"))
        remove.clicked.connect(lambda: self.remove_requested.emit(key))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(7, 5, 5, 5)
        layout.addWidget(self.icon)
        layout.addLayout(texts, 1)
        layout.addWidget(remove)

    def set_result(self, attachment: Attachment | None, error: str) -> None:
        if error:
            ui_text(self.detail, 'setText', error)
            ui_text(self.detail, 'setToolTip', error)
            themed_stylesheet(self.detail, "color: #f0a0a0; font-size: 11px;")
        elif attachment is not None:
            ui_text(self.detail, 'setText', attachment.detail)
            if attachment.preview is not None and attachment.content[0]["type"] == "image":
                pixmap = QPixmap.fromImage(attachment.preview)
                self.icon.setIcon(QIcon(pixmap))
                self._preview = attachment.preview
                self.icon.setEnabled(True)
                ui_text(self.icon, 'setToolTip', tr("Afficher l’image à 100 %"))
            else:
                self.icon.setIcon(QIcon(QPixmap.fromImage(attachment_badge(attachment))))
                self.icon.setEnabled(True)
                ui_text(self.icon, 'setToolTip', tr("Ouvrir le fichier"))

    def _open_preview(self) -> None:
        if self._preview is not None:
            self.preview_requested.emit(self._preview)
        elif self.source is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.source)))

    def resizeEvent(self, event):  # noqa: N802 - API Qt
        super().resizeEvent(event)
        self.layout().activate()
        ui_text(self.name_label, 'setText', self.name_label.fontMetrics().elidedText(self.name, Qt.ElideMiddle, self.name_label.width()))
