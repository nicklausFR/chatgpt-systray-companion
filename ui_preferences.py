"""Qt translations and application appearance, shared by every UI surface."""
from __future__ import annotations

import json
import re
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QLibraryInfo, QLocale, QObject, Qt, QTranslator, Signal
from PySide6.QtGui import QColor, QPalette, QTextCharFormat, QTextCursor, QTextFormat, QTextTable
from PySide6.QtWidgets import QApplication


TRANSLATIONS = Path(__file__).resolve().parent / "translations"
UI_TEXT_PROPERTY = QTextFormat.UserProperty + 20
# The existing dark palette is also the canonical palette for stored styles.
LIGHT_COLORS = dict(zip(
    "#202020 #202830 #252525 #282828 #2c2c2c #303030 #333333 #343434 #3a3a3a #404040 #435363 #444444 #454545 #465b74 #484848 #4a4a4a #505050 #575757 #626262 #656565 #666666 #777777 #858585 #a0a0a0 #a5a5a5 #a5c9af #a8abb1 #a8c7fa #aaaaaa #b4b4b4 #b5b5b5 #c0e5ca #c7c7c7 #cccccc #d4d4d4 #dedede #ececec #ededed #eeeeee #f0a0a0 #ffffff".split(),
    "#f2f2f2 #edf4fc #fafafa #f6f6f6 #f7f7f7 #efefef #f0f0f0 #e9e9e9 #e3e3e3 #d8d8d8 #a8bed5 #dfdfdf #cfcfcf #c4daf5 #cecece #d5d5d5 #b8b8b8 #b0b0b0 #999999 #aaaaab #969696 #777777 #707070 #606060 #626263 #28733f #606875 #175ab0 #646464 #555555 #545454 #185a30 #8a8a8a #454546 #363636 #303031 #242424 #222222 #202021 #a82222 #111111".split(),
    strict=True,
))


class UiText(str):
    """Retain the source of UI text so an open window can be retranslated."""
    def __new__(cls, source: str, values: dict | None = None):
        translated = QCoreApplication.translate("Companion", source)
        obj = super().__new__(cls, translated.format(**values) if values else translated)
        obj.source, obj.values = source, values or {}
        return obj

    def __str__(self):
        translated = QCoreApplication.translate("Companion", self.source)
        return translated.format(**self.values) if self.values else translated

    def __format__(self, spec):
        return format(str(self), spec)

    def format(self, **values):
        return UiText(self.source, values)


def tr(source: str) -> UiText:
    return UiText(source)


def ui_text(obj, setter: str, text):
    bindings = getattr(obj, "_ui_texts", {})
    if isinstance(text, UiText):
        bindings[setter] = text
    else:
        bindings.pop(setter, None)
    obj._ui_texts = bindings
    getattr(obj, setter)(str(text))
    return obj


def retranslate(root):
    for obj in [root, *root.findChildren(QObject)]:
        for setter, text in getattr(obj, "_ui_texts", {}).items():
            getattr(obj, setter)(str(text))


def ui_color(dark: str) -> str:
    app = QApplication.instance()
    return LIGHT_COLORS.get(dark.lower(), dark) if app and app.property("companionTheme") == "light" else dark


def themed_stylesheet(widget, source: str):
    widget._ui_style = source
    widget.setStyleSheet(re.sub(r"#[0-9a-fA-F]{6}", lambda match: ui_color(match[0]), source))


def restyle(root):
    for widget in [root, *root.findChildren(QObject)]:
        if hasattr(widget, "_ui_style"):
            themed_stylesheet(widget, widget._ui_style)


def insert_ui_text(cursor, text, char_format):
    char_format = QTextCharFormat(char_format)
    if isinstance(text, UiText):
        char_format.setProperty(UI_TEXT_PROPERTY, json.dumps(_encode_text(text), ensure_ascii=False))
    cursor.insertText(str(text), char_format)


def _encode_text(text):
    if isinstance(text, UiText):
        return {"source": text.source, "values": {key: _encode_text(value) for key, value in text.values.items()}}
    return text


def _decode_text(data):
    if isinstance(data, dict) and "source" in data:
        return tr(data["source"]).format(**{key: _decode_text(value) for key, value in data["values"].items()})
    return data


def retranslate_document(document):
    replacements = []
    block = document.begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            source = fragment.charFormat().property(UI_TEXT_PROPERTY)
            if source:
                replacements.append((fragment.position(), fragment.length(), fragment.charFormat(), source))
            iterator += 1
        block = block.next()
    for position, length, char_format, source in reversed(replacements):
        cursor = QTextCursor(document)
        cursor.setPosition(position)
        cursor.setPosition(position + length, QTextCursor.KeepAnchor)
        insert_ui_text(cursor, _decode_text(json.loads(source)), char_format)


def recolor_document(document, previous: str, current: str):
    if previous == current:
        return
    colors = LIGHT_COLORS if current == "light" else {light: dark for dark, light in LIGHT_COLORS.items()}

    def recolor(fmt):
        for key, brush in list(fmt.properties().items()):
            if hasattr(brush, "color") and brush.style() == Qt.SolidPattern:
                replacement = colors.get(brush.color().name())
                if replacement:
                    brush.setColor(QColor(replacement))
                    fmt.setProperty(key, brush)
        return fmt

    def frames(frame):
        frame.setFrameFormat(recolor(frame.frameFormat()))
        if isinstance(frame, QTextTable):
            for row in range(frame.rows()):
                for column in range(frame.columns()):
                    cell = frame.cellAt(row, column)
                    cell.setFormat(recolor(cell.format()))
        for child in frame.childFrames():
            frames(child)

    frames(document.rootFrame())
    block = document.begin()
    fragments = []
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            fragments.append((fragment.position(), fragment.length(), recolor(fragment.charFormat())))
            iterator += 1
        block = block.next()
    for position, length, fmt in fragments:
        cursor = QTextCursor(document)
        cursor.setPosition(position)
        cursor.setPosition(position + length, QTextCursor.KeepAnchor)
        cursor.setCharFormat(fmt)


class UiPreferences(QObject):
    language_changed = Signal()
    theme_changed = Signal(str, str)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.app = QApplication.instance()
        self.language = ""
        self.theme = "auto"
        self.effective_theme = self.app.property("companionTheme") or "dark"
        self.app.styleHints().colorSchemeChanged.connect(self._system_theme_changed)
        language = settings.value("language", "fr" if QLocale.system().language() == QLocale.French else "en", type=str)
        self.set_language(language if language in ("fr", "en") else "fr", persist=False)
        theme = settings.value("theme", "auto", type=str)
        self.set_theme(theme if theme in ("light", "dark", "auto") else "auto", persist=False)

    def set_language(self, language: str, persist: bool = True):
        if language not in ("fr", "en") or language == self.language:
            return
        # QApplication owns the references, including when a window is recreated.
        for translator in getattr(self.app, "_companion_translators", []):
            self.app.removeTranslator(translator)
        translators = []
        qt_translator = QTranslator(self.app)
        if (qt_translator.load(QLocale(language), "qtbase", "_", str(TRANSLATIONS))
                or qt_translator.load(QLocale(language), "qtbase", "_", QLibraryInfo.path(QLibraryInfo.TranslationsPath))):
            self.app.installTranslator(qt_translator)
            translators.append(qt_translator)
        translator = QTranslator(self.app)
        if language == "en":
            if not translator.load(str(TRANSLATIONS / "companion_en.qm")):
                raise RuntimeError("Missing English translation catalog: companion_en.qm")
            self.app.installTranslator(translator)
            translators.append(translator)
        self.app._companion_translators = translators
        self.language = language
        if persist:
            self.settings.setValue("language", language)
            self.settings.sync()
        self.language_changed.emit()

    def set_theme(self, theme: str, persist: bool = True):
        if theme not in ("light", "dark", "auto"):
            return
        self.theme = theme
        scheme = {"auto": Qt.ColorScheme.Unknown, "light": Qt.ColorScheme.Light, "dark": Qt.ColorScheme.Dark}[theme]
        self.app.styleHints().setColorScheme(scheme)
        self._apply_theme(self.app.styleHints().colorScheme() if theme == "auto" else scheme)
        if persist:
            self.settings.setValue("theme", theme)
            self.settings.sync()

    def _system_theme_changed(self, scheme):
        if self.theme == "auto":
            self._apply_theme(scheme)

    def _apply_theme(self, scheme):
        previous = self.effective_theme
        self.effective_theme = "dark" if scheme == Qt.ColorScheme.Dark else "light"
        self.app.setProperty("companionTheme", self.effective_theme)
        palette = QPalette()
        for role, dark in {
            QPalette.Window: "#252525", QPalette.WindowText: "#ededed",
            QPalette.Base: "#202020", QPalette.AlternateBase: "#303030",
            QPalette.Text: "#dedede", QPalette.Button: "#343434",
            QPalette.ButtonText: "#eeeeee", QPalette.ToolTipBase: "#2c2c2c",
            QPalette.ToolTipText: "#ededed", QPalette.Highlight: "#465b74",
            QPalette.HighlightedText: "#eeeeee", QPalette.Link: "#a8c7fa",
            QPalette.PlaceholderText: "#a5a5a5",
        }.items():
            palette.setColor(role, QColor(ui_color(dark)))
        for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
            palette.setColor(QPalette.Disabled, role, QColor(ui_color("#858585")))
        self.app.setPalette(palette)
        self.theme_changed.emit(previous, self.effective_theme)
