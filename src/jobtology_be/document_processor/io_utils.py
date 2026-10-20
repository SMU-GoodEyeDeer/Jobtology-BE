"""Input normalization helpers for document parsing entrypoints."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import tempfile
from typing import BinaryIO, Literal
import zipfile


SourceDocType = Literal["auto", "hwp", "hwpx", "doc", "docx", "pdf"]
ResolvedDocType = Literal["hwp", "hwpx", "doc", "docx", "pdf"]


def _read_file_object(source: BinaryIO) -> bytes:
    position = None
    if hasattr(source, "tell"):
        try:
            position = source.tell()
        except OSError:
            position = None
    if hasattr(source, "seek"):
        try:
            source.seek(0)
        except OSError:
            pass

    data = source.read()

    if position is not None and hasattr(source, "seek"):
        try:
            source.seek(position)
        except OSError:
            pass

    if isinstance(data, str):
        raise TypeError("Expected a binary file object, but read text data.")
    return data


def get_source_name(source: object) -> str | None:
    if isinstance(source, (str, Path)):
        return str(source)

    name = getattr(source, "name", None)
    if isinstance(name, str) and name:
        return name
    return None


def infer_doc_type(source: object, doc_type: SourceDocType) -> ResolvedDocType:
    if doc_type == "pdf":
        return "pdf"
    if doc_type in ("hwp", "hwpx", "doc", "docx"):
        return doc_type

    name = get_source_name(source)
    if name is not None:
        suffix = Path(name).suffix.lower()
        if suffix == ".hwp":
            return "hwp"
        if suffix == ".hwpx":
            return "hwpx"
        if suffix == ".doc":
            return "doc"
        if suffix == ".docx":
            return "docx"
        if suffix == ".pdf":
            return "pdf"

    if isinstance(source, bytes):
        return infer_doc_type_from_bytes(source)

    module_name = source.__class__.__module__.split(".", 1)[0]
    if module_name == "hwpx":
        return "hwpx"
    if module_name == "docx":
        return "docx"

    raise ValueError(
        "Could not infer document type. Pass doc_type='hwp', 'hwpx', 'doc', 'docx', or 'pdf'."
    )


def infer_doc_type_from_bytes(source: bytes) -> ResolvedDocType:
    if source.startswith(b"%PDF-"):
        return "pdf"
    if source.startswith(b"HWP Document File"):
        return "hwp"

    try:
        with zipfile.ZipFile(BytesIO(source)) as zf:
            names = set(zf.namelist())
    except zipfile.BadZipFile as exc:
        raise ValueError(
            "Could not infer document type from bytes. Pass doc_type explicitly."
        ) from exc

    if "[Content_Types].xml" in names:
        return "docx"
    if any(name.startswith("Contents/section") and name.endswith(".xml") for name in names):
        return "hwpx"

    raise ValueError(
        "Could not infer document type from bytes. Pass doc_type explicitly."
    )


def coerce_source_to_supported_value(
    source: str | Path | bytes | BinaryIO,
    *,
    doc_type: ResolvedDocType,
) -> str | Path | bytes:
    if isinstance(source, (str, Path, bytes)):
        return source

    return _read_file_object(source)


class TemporarySourcePath:
    """Context manager for source types that require a filesystem path."""

    def __init__(self, source: object, *, suffix: str) -> None:
        self._source = source
        self._suffix = suffix
        self._temp_file: tempfile.NamedTemporaryFile[bytes] | None = None
        self.path: Path | None = None

    def __enter__(self) -> Path:
        if isinstance(self._source, (str, Path)):
            self.path = Path(self._source)
            return self.path

        if isinstance(self._source, bytes):
            data = self._source
        elif hasattr(self._source, "read"):
            data = _read_file_object(self._source)
        else:
            raise TypeError(f"Unsupported source type for temporary path materialization: {type(self._source)!r}")

        self._temp_file = tempfile.NamedTemporaryFile(suffix=self._suffix, delete=False)
        self._temp_file.write(data)
        self._temp_file.flush()
        self._temp_file.close()
        self.path = Path(self._temp_file.name)
        return self.path

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.path is not None and self._temp_file is not None:
            self.path.unlink(missing_ok=True)


__all__ = [
    "ResolvedDocType",
    "SourceDocType",
    "TemporarySourcePath",
    "coerce_source_to_supported_value",
    "get_source_name",
    "infer_doc_type",
]
