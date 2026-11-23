"""Extract tr() sources for Qt Linguist and compile the checked catalog with lrelease."""
from __future__ import annotations

import argparse
import ast
import os
import string
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET

import PySide6


ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "translations/companion_en.ts"
SOURCES = ("app.py", "attachments.py", "codex_check.py", "generated_files.py")


def source_texts() -> set[str]:
    return {
        node.args[0].value
        for filename in SOURCES
        for node in ast.walk(ast.parse((ROOT / filename).read_text(encoding="utf-8")))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "tr"
        and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
    }


def update_sources() -> None:
    tree = ET.parse(CATALOG)
    context = tree.getroot().find("context")
    existing = {message.findtext("source") for message in context.findall("message")}
    for source in sorted(source_texts() - existing):
        message = ET.SubElement(context, "message")
        ET.SubElement(message, "source").text = source
        ET.SubElement(message, "translation", type="unfinished")
    ET.indent(tree)
    tree.write(CATALOG, encoding="utf-8", xml_declaration=True)


def check_catalog() -> None:
    messages = ET.parse(CATALOG).getroot().findall("context/message")
    missing = source_texts() - {message.findtext("source") for message in messages}
    if missing:
        raise ValueError(f"Missing sources; run with --update: {sorted(missing)}")
    formatter = string.Formatter()
    for message in messages:
        source = message.findtext("source")
        translation = message.find("translation")
        target = translation.text or ""
        fields = lambda text: sorted((field, spec, conversion) for _, field, spec, conversion in formatter.parse(text) if field is not None)
        if not target or translation.get("type") == "unfinished" or fields(source) != fields(target):
            raise ValueError(f"Incomplete translation or mismatched placeholders: {source!r}")


def build_catalogs() -> None:
    check_catalog()
    executable = Path(PySide6.__file__).parent / ("lrelease.exe" if os.name == "nt" else "lrelease")
    subprocess.run([str(executable), str(CATALOG), "-qm", str(CATALOG.with_suffix(".qm"))], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true", help="add new source strings before compiling")
    parser.add_argument("--check", action="store_true", help="validate without compiling")
    args = parser.parse_args()
    if args.update:
        update_sources()
    if args.check:
        check_catalog()
    else:
        build_catalogs()
