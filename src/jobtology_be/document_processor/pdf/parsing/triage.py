"""Scan-like vs structured PDF page triage."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .probe import (
    FULL_OCR_CONTENT_IMAGE_RATIO_THRESHOLD,
    FULL_OCR_IMAGE_RATIO_THRESHOLD,
    GIBBERISH_MIN_CHARS,
    GIBBERISH_NORMAL_TEXT_MAX_RATIO,
    REPLACEMENT_CHAR_THRESHOLD,
    REPLACEMENT_MIN_COUNT,
    PageProfile,
)


VECTOR_SCAN_MAX_IMAGE_RATIO = 0.2
VECTOR_SCAN_MIN_PATH_COUNT = 100


class PageClass(str, Enum):
    SCAN_LIKE = "scan_like"
    STRUCTURED = "structured"


@dataclass(frozen=True)
class PageDecision:
    page_number: int
    page_class: PageClass


def decide_page(page_profile: PageProfile) -> PageDecision:
    if page_profile.image_area_ratio >= FULL_OCR_IMAGE_RATIO_THRESHOLD:
        return PageDecision(
            page_number=page_profile.page_number,
            page_class=PageClass.SCAN_LIKE,
        )
    if page_profile.image_area_in_content_ratio >= FULL_OCR_CONTENT_IMAGE_RATIO_THRESHOLD:
        return PageDecision(
            page_number=page_profile.page_number,
            page_class=PageClass.SCAN_LIKE,
        )
    # Heavy U+FFFD content is a broken/mojibake text layer regardless of how
    # many readable characters survive alongside it. Gate on an absolute
    # non-space replacement count (not the whitespace-inclusive char_count) so a
    # sparse page with a stray replacement among layout spaces (e.g. "제 \n �")
    # is not dropped, while a genuinely garbled run still triggers.
    if (
        page_profile.replacement_char_count >= REPLACEMENT_MIN_COUNT
        and page_profile.replacement_char_ratio >= REPLACEMENT_CHAR_THRESHOLD
    ):
        return PageDecision(
            page_number=page_profile.page_number,
            page_class=PageClass.SCAN_LIKE,
        )
    if (
        page_profile.char_count >= GIBBERISH_MIN_CHARS
        and page_profile.normal_text_ratio <= GIBBERISH_NORMAL_TEXT_MAX_RATIO
    ):
        return PageDecision(
            page_number=page_profile.page_number,
            page_class=PageClass.SCAN_LIKE,
        )
    if (
        page_profile.char_count == 0
        and not page_profile.text_readable
        and page_profile.image_area_ratio < VECTOR_SCAN_MAX_IMAGE_RATIO
        and page_profile.path_count > VECTOR_SCAN_MIN_PATH_COUNT
    ):
        return PageDecision(
            page_number=page_profile.page_number,
            page_class=PageClass.SCAN_LIKE,
        )
    return PageDecision(
        page_number=page_profile.page_number,
        page_class=PageClass.STRUCTURED,
    )


def summarize_page_decisions(page_decisions: list[PageDecision]) -> dict[str, int]:
    summary = {page_class.value: 0 for page_class in PageClass}
    for decision in page_decisions:
        summary[decision.page_class.value] += 1
    return summary


__all__ = [
    "PageClass",
    "PageDecision",
    "decide_page",
    "summarize_page_decisions",
]
