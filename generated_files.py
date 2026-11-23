"""File delivery and saving, independent of file formats."""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from ui_preferences import tr
from pathlib import Path, PureWindowsPath

MAX_FILES = 200
MAX_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class GeneratedFile:
    path: str
    content: str | bytes = ""
    source: Path | None = None
    source_root: Path | None = None


@dataclass(frozen=True)
class GeneratedBundle:
    name: str
    files: tuple[GeneratedFile, ...]
    folder: bool
    archive: bool = False


@dataclass(frozen=True)
class ParsedResponse:
    markdown: str
    bundles: tuple[GeneratedBundle, ...]


def relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or value.startswith(("/", "\\")) or PureWindowsPath(value).drive:
        raise ValueError(tr("Un nom de fichier relatif est nécessaire."))
    parts = value.replace("\\", "/").split("/")
    for part in parts:
        if (not part or part in (".", "..") or part.endswith((".", " "))
                or PureWindowsPath(part).is_reserved()
                or any(char in '<>:"|?*' or ord(char) < 32 for char in part)):
            raise ValueError(tr('Nom de fichier non valide : {value}').format(value=value))
    return "/".join(parts)


def validate_bundle(payload) -> GeneratedBundle:
    """Compatibility with inline text/base64 deliveries; no file-type whitelist."""
    if not isinstance(payload, dict):
        raise ValueError(tr("La liste des fichiers n'est pas valide."))
    entries = payload.get("files")
    if not isinstance(entries, list) or not entries or len(entries) > MAX_FILES:
        raise ValueError(tr('Un livrable doit contenir entre 1 et {MAX_FILES} fichiers.').format(MAX_FILES=MAX_FILES))
    kind = payload.get("type", "folder" if len(entries) > 1 else "file")
    if kind not in ("file", "folder", "zip") or (kind == "file" and len(entries) != 1):
        raise ValueError(tr("Le type de livrable ne correspond pas aux fichiers proposés."))
    files = []
    total = 0
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("content"), str):
            raise ValueError(tr("Le contenu complet de chaque fichier est nécessaire."))
        path = relative_path(entry.get("path"))
        content = entry["content"]
        encoding = entry.get("encoding", "utf-8")
        if encoding == "base64":
            try:
                content = base64.b64decode("".join(content.split()), validate=True)
            except (ValueError, binascii.Error) as error:
                raise ValueError(tr("Le contenu encodé du fichier n'est pas valide.")) from error
        elif encoding != "utf-8":
            raise ValueError(tr("Encodage du fichier non pris en charge."))
        total += len(content.encode("utf-8") if isinstance(content, str) else content)
        files.append(GeneratedFile(path, content))
    if total > MAX_BYTES:
        raise ValueError(tr("Les fichiers proposés sont trop volumineux pour cet enregistrement."))
    default_name = tr("fichiers.zip") if kind == "zip" else tr("fichiers-generes") if kind == "folder" else files[0].path.split("/")[-1]
    name = payload.get("name", default_name)
    if kind == "zip" and isinstance(name, str) and not name.lower().endswith(".zip"):
        name += ".zip"
    return check_bundle(GeneratedBundle(name, tuple(files), kind == "folder", kind == "zip"))


def checked_source(file: GeneratedFile) -> Path:
    if file.source is None or file.source_root is None:
        raise ValueError(tr("Le fichier généré est introuvable."))
    root = file.source_root.resolve(strict=True)
    source = file.source.resolve(strict=True)
    if (file.source_root.is_symlink() or root != file.source_root.absolute()
            or not source.is_relative_to(root) or not source.is_file() or file.source.is_symlink()):
        raise ValueError(tr("Le fichier doit rester dans l’espace de travail de la discussion."))
    return source


def check_bundle(bundle: GeneratedBundle) -> GeneratedBundle:
    name = relative_path(bundle.name)
    if "/" in name or not bundle.files:
        raise ValueError(tr("Le nom du livrable n’est pas valide."))
    paths = set()
    for file in bundle.files:
        path = relative_path(file.path).casefold()
        if path in paths:
            raise ValueError(tr('Un fichier est proposé plusieurs fois : {file_path}').format(file_path=file.path))
        paths.add(path)
        if file.source is not None:
            checked_source(file)
        elif not isinstance(file.content, (str, bytes)):
            raise ValueError(tr("Le contenu du fichier n’est pas valide."))
    for path in paths:
        parts = path.split("/")
        if any("/".join(parts[:index]) in paths for index in range(1, len(parts))):
            raise ValueError(tr("Un même nom ne peut pas désigner un fichier et un dossier."))
    return bundle


def parse_response(markdown: str) -> ParsedResponse:
    lines = markdown.splitlines(keepends=True)
    visible, bundles = [], []
    index = 0
    while index < len(lines):
        opening = re.match(r"^[ \t]{0,3}(`{3,}|~{3,})[ \t]*([^\r\n]*)", lines[index])
        if opening is None:
            visible.append(lines[index])
            index += 1
            continue
        fence, language = opening.groups()
        closing = index + 1
        while closing < len(lines):
            candidate = lines[closing].strip()
            if len(candidate) >= len(fence) and set(candidate) == {fence[0]}:
                break
            closing += 1
        if language.strip().lower() != "companion-files":
            visible.extend(lines[index:min(closing + 1, len(lines))])
        elif closing == len(lines):
            visible.append(tr("\n\nLes fichiers sont incomplets. Demandez à l'assistant de régénérer les fichiers complets.\n"))
        else:
            try:
                bundles.append(validate_bundle(json.loads("".join(lines[index + 1:closing]))))
                visible.append("\n\n")
            except json.JSONDecodeError:
                visible.append(tr("\n\nLes fichiers n'ont pas pu être préparés. Demandez à l'assistant de régénérer les fichiers complets.\n"))
            except (ValueError, UnicodeError) as error:
                visible.append(tr("\n\nLes fichiers n'ont pas pu être préparés : {error}\n").format(error=error))
        index = closing + 1
    body = "".join(visible)
    return ParsedResponse(body if body.strip() or not bundles else tr("Fichiers prêts à être enregistrés."), tuple(bundles))


def _write_contents(file: GeneratedFile, output) -> None:
    if file.source is not None:
        with checked_source(file).open("rb") as source:
            shutil.copyfileobj(source, output, 1024 * 1024)
    else:
        output.write(file.content.encode("utf-8") if isinstance(file.content, str) else file.content)


def save_file(file: GeneratedFile, destination: Path) -> Path:
    check_bundle(GeneratedBundle(Path(file.path).name, (file,), False))
    destination = Path(destination).absolute()
    descriptor, temporary = tempfile.mkstemp(prefix=".companion-", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            _write_contents(file, output)
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return destination


def save_zip(bundle: GeneratedBundle, destination: Path) -> Path:
    check_bundle(bundle)
    destination = Path(destination).absolute()
    descriptor, temporary = tempfile.mkstemp(prefix=".companion-", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "w+b") as output:
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for file in bundle.files:
                    with archive.open(relative_path(file.path), "w", force_zip64=True) as entry:
                        _write_contents(file, entry)
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return destination


def save_folder(bundle: GeneratedBundle, parent: Path) -> Path:
    check_bundle(bundle)
    parent = Path(parent).resolve(strict=True)
    index = 1
    while True:
        target = parent / (bundle.name if index == 1 else f"{bundle.name} ({index})")
        try:
            target.mkdir()
            break
        except FileExistsError:
            index += 1
    try:
        created_root = target.resolve()
        for file in bundle.files:
            destination = target.joinpath(*relative_path(file.path).split("/"))
            if not destination.parent.resolve().is_relative_to(created_root):
                raise ValueError(tr("Le fichier doit rester dans le dossier choisi."))
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.resolve().is_relative_to(created_root):
                raise ValueError(tr("Le fichier doit rester dans le dossier choisi."))
            with destination.open("xb") as output:
                _write_contents(file, output)
    except Exception:
        if not target.is_symlink() and target.resolve().parent == parent:
            shutil.rmtree(target)
        raise
    return target
