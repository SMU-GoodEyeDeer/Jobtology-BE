"""Minimal HWPX container reader."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from os import PathLike
from pathlib import Path
import re
from typing import BinaryIO
from xml.etree import ElementTree as ET
import zipfile

_SECTION_NAME_RE = re.compile(r"^Contents/section(\d+)\.xml$")
_HEADER_NAME = "Contents/header.xml"


def _read_source_bytes(source: str | PathLike[str] | bytes | BinaryIO) -> bytes:
    if isinstance(source, bytes):
        return source

    if isinstance(source, (str, PathLike)):
        return Path(source).read_bytes()

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
        raise TypeError("Expected a binary HWPX source, but read text data.")
    return data


def _section_sort_key(name: str) -> int:
    match = _SECTION_NAME_RE.match(name)
    if match is None:
        return -1
    return int(match.group(1))


@dataclass(frozen=True)
class _HwpxElementWrapper:
    element: ET.Element


class HwpxDocument:
    """Read-only HWPX container."""

    def __init__(
        self,
        *,
        source_bytes: bytes,
        sections: list[_HwpxElementWrapper],
        headers: list[_HwpxElementWrapper],
    ) -> None:
        self._source_bytes = source_bytes
        self.sections = sections
        self.headers = headers

    @classmethod
    def open(cls, source: str | PathLike[str] | bytes | BinaryIO) -> "HwpxDocument":
        source_bytes = _read_source_bytes(source)

        with zipfile.ZipFile(BytesIO(source_bytes)) as archive:
            section_names = sorted(
                (
                    name
                    for name in archive.namelist()
                    if _SECTION_NAME_RE.match(name)
                ),
                key=_section_sort_key,
            )
            sections = [
                _HwpxElementWrapper(ET.fromstring(archive.read(name)))
                for name in section_names
            ]

            headers: list[_HwpxElementWrapper] = []
            try:
                headers.append(_HwpxElementWrapper(ET.fromstring(archive.read(_HEADER_NAME))))
            except KeyError:
                pass

        return cls(
            source_bytes=source_bytes,
            sections=sections,
            headers=headers,
        )

    def __enter__(self) -> "HwpxDocument":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        """Compatibility no-op for context manager callers."""

    def to_bytes(self) -> bytes:
        return self._source_bytes


__all__ = ["HwpxDocument"]
