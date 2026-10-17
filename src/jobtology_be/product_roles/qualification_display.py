"""Readable, deduplicated qualification labels for analysis display (not hashed)."""

import re
from collections.abc import Iterable

from jobtology_be.application.requirement_metadata import (
    QualificationKind,
    RequirementQualification,
)

# Work-learning (일학습병행) standards are published as "<family>_L<level>_<version>",
# e.g. "SW개발_L5_25V2" or the pre-2020 "SW아키텍트_L6_ver2.0".
_WORK_LEARNING = re.compile(
    r"^(?P<family>.+?)_L(?P<level>\d+)_(?P<version>\d{2}V\d+|ver\d+(?:\.\d+)?)$")
_YEAR_VERSION = re.compile(r"^(\d{2})V(\d+)$")
_LEGACY_VERSION = re.compile(r"^ver(\d+)(?:\.(\d+))?$")
_KIND_ORDER: dict[QualificationKind | None, int] = {
    "NATIONAL_TECHNICAL": 0, "WORK_LEARNING": 1, None: 2,
}


def describe_qualification(code: str, name: str | None) -> RequirementQualification:
    if name is None or not name.strip():
        return RequirementQualification(code=code, name=name, display_name=code, kind=None)
    match = _WORK_LEARNING.fullmatch(name.strip())
    if match is None:
        return RequirementQualification(code=code, name=name, display_name=name.strip(),
                                        kind="NATIONAL_TECHNICAL")
    return RequirementQualification(
        code=code, name=name, kind="WORK_LEARNING",
        display_name=f"{match['family'].strip()} {int(match['level'])}수준",
    )


def _version_rank(name: str | None) -> tuple[int, int, int]:
    match = _WORK_LEARNING.fullmatch((name or "").strip())
    version = match["version"] if match else ""
    if year := _YEAR_VERSION.fullmatch(version):
        return (1, int(year[1]), int(year[2]))
    if legacy := _LEGACY_VERSION.fullmatch(version):
        return (0, int(legacy[1]), int(legacy[2] or 0))
    return (-1, 0, 0)


def display_qualifications(
    pairs: Iterable[tuple[str, str | None]],
) -> tuple[RequirementQualification, ...]:
    """Keep the newest standard per work-learning family/level; national ones first."""
    latest: dict[tuple[str, str], tuple[tuple[int, int, int], RequirementQualification]] = {}
    for code, name in dict(pairs).items():
        item = describe_qualification(code, name)
        key = (item.display_name, code) if item.kind != "WORK_LEARNING" else (item.display_name, "")
        candidate = (_version_rank(name), item)
        current = latest.get(key)
        if current is None or (candidate[0], code) > (current[0], current[1].code):
            latest[key] = candidate
    return tuple(sorted((item for _, item in latest.values()),
                        key=lambda item: (_KIND_ORDER[item.kind], item.display_name, item.code)))
