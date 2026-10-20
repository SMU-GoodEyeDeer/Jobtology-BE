"""Legacy Word DOC to DOCX conversion through the bundled .NET CLI."""

from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from xml.etree import ElementTree as ET
import zipfile


DOC_CONVERTER_ENV_VAR = "DOCUMENT_PROCESSOR_DOC_CONVERTER"
DEFAULT_CONVERSION_TIMEOUT_SECONDS = 120.0
_WORDPROCESSINGML_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


class DocConversionError(RuntimeError):
    """Raised when the legacy DOC converter cannot produce a valid DOCX file."""


def _bundled_converter_path() -> Path:
    return (
        Path(__file__).resolve().parent
        / "vendor"
        / "doc2docx"
        / "net8"
        / "DocToDocx.Cli.dll"
    )


def _converter_path() -> Path:
    configured = os.environ.get(DOC_CONVERTER_ENV_VAR)
    return Path(configured).expanduser() if configured else _bundled_converter_path()


def _converter_command() -> list[str]:
    converter_path = _converter_path()
    if not converter_path.is_file():
        raise FileNotFoundError(
            "Legacy DOC converter not found at "
            f"{converter_path}. Build tools/doc2docx or set "
            f"{DOC_CONVERTER_ENV_VAR} to a published converter DLL or executable."
        )

    if converter_path.suffix.lower() != ".dll":
        return [str(converter_path)]

    dotnet = shutil.which("dotnet")
    if dotnet is None:
        raise FileNotFoundError(
            "Legacy DOC conversion requires the .NET 8 runtime, but 'dotnet' was not found on PATH."
        )
    return [dotnet, str(converter_path)]


def _validate_docx_bytes(data: bytes) -> None:
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            names = set(archive.namelist())
    except zipfile.BadZipFile as exc:
        raise DocConversionError(
            "DOC converter output is not a valid DOCX ZIP archive."
        ) from exc

    required_parts = {"[Content_Types].xml", "word/document.xml"}
    missing = sorted(required_parts - names)
    if missing:
        raise DocConversionError(
            "DOC converter output is missing required DOCX part(s): "
            + ", ".join(missing)
        )


def _normalize_docsharp_docx(data: bytes) -> bytes:
    """Normalize known DocSharp output quirks before python-docx reads it."""
    with zipfile.ZipFile(BytesIO(data)) as source_archive:
        try:
            styles_xml = source_archive.read("word/styles.xml")
        except KeyError:
            return data

        try:
            root = ET.fromstring(styles_xml)
        except ET.ParseError as exc:
            raise DocConversionError(
                "DOC converter output contains malformed word/styles.xml."
            ) from exc
        type_attr = f"{{{_WORDPROCESSINGML_NS}}}type"
        changed = False
        for style in root.findall(f"{{{_WORDPROCESSINGML_NS}}}style"):
            if style.get(type_attr) == "list":
                style.set(type_attr, "numbering")
                changed = True

        if not changed:
            return data

        ET.register_namespace("w", _WORDPROCESSINGML_NS)
        normalized_styles = ET.tostring(
            root,
            encoding="utf-8",
            xml_declaration=True,
        )
        output = BytesIO()
        with zipfile.ZipFile(output, "w") as target_archive:
            for item in source_archive.infolist():
                item_data = (
                    normalized_styles
                    if item.filename == "word/styles.xml"
                    else source_archive.read(item.filename)
                )
                target_archive.writestr(item, item_data)
        return output.getvalue()


def convert_doc_to_docx_bytes(
    doc_path: str | Path,
    *,
    timeout_seconds: float = DEFAULT_CONVERSION_TIMEOUT_SECONDS,
) -> bytes:
    """Convert a Word 97-2003 ``.doc`` file to validated ``.docx`` bytes."""
    source_path = Path(doc_path)
    if not source_path.is_file():
        raise FileNotFoundError(f"DOC file not found: {source_path}")

    command = _converter_command()
    with tempfile.TemporaryDirectory() as tmp_dir:
        output_path = Path(tmp_dir) / f"{source_path.stem}.docx"
        try:
            completed = subprocess.run(
                [*command, str(source_path), str(output_path)],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise DocConversionError(
                f"DOC conversion timed out after {timeout_seconds:g} seconds."
            ) from exc
        except OSError as exc:
            raise DocConversionError(f"Could not start DOC converter: {exc}") from exc

        if completed.returncode != 0:
            details = (completed.stderr or completed.stdout).strip()
            if len(details) > 4000:
                details = details[-4000:]
            message = f"DOC conversion failed with exit code {completed.returncode}."
            if details:
                message = f"{message} {details}"
            raise DocConversionError(message)

        if not output_path.is_file():
            raise DocConversionError(
                "DOC converter completed without creating a DOCX file."
            )

        raw_data = output_path.read_bytes()
        _validate_docx_bytes(raw_data)
        data = _normalize_docsharp_docx(raw_data)
        _validate_docx_bytes(data)
        return data


__all__ = [
    "DEFAULT_CONVERSION_TIMEOUT_SECONDS",
    "DOC_CONVERTER_ENV_VAR",
    "DocConversionError",
    "convert_doc_to_docx_bytes",
]
