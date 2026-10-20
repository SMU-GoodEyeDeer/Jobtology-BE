"""Triage / probe heuristics: gibberish vs structured classification.

Regression coverage for the dotted-leader table-of-contents false positive:
a TOC page is dense with ASCII dot leaders ("......") that are legitimate
text, yet were dragged below the gibberish ratio gate and dropped from ODL.
"""

from __future__ import annotations

import unittest


from jobtology_be.document_processor.pdf.parsing.probe import (  # noqa: E402
    GIBBERISH_MIN_CHARS,
    GIBBERISH_NORMAL_TEXT_MAX_RATIO,
    PageProfile,
    normal_text_ratio,
)
from jobtology_be.document_processor.pdf.parsing.triage import PageClass, decide_page  # noqa: E402


def _profile(**overrides) -> PageProfile:
    base = dict(
        page_number=1,
        char_count=0,
        normal_text_ratio=1.0,
        replacement_char_ratio=0.0,
        replacement_char_count=0,
        text_readable=True,
        path_count=0,
        image_area_ratio=0.0,
        image_area_in_content_ratio=0.0,
        has_suspicious_regions=False,
        page_width_pt=595.0,
        page_height_pt=842.0,
    )
    base.update(overrides)
    return PageProfile(**base)


class NormalTextRatioTests(unittest.TestCase):
    def test_dot_leader_toc_counts_as_normal(self) -> None:
        # Mimics a real TOC line: text + dotted leader + page number.
        toc = "\n".join(
            f"{i} 항목 제목 " + "." * 90 + f" {i}" for i in range(1, 30)
        )
        ratio = normal_text_ratio(toc)
        self.assertGreater(
            ratio,
            GIBBERISH_NORMAL_TEXT_MAX_RATIO,
            f"dot-leader TOC must not read as gibberish (ratio={ratio:.3f})",
        )

    def test_ascii_punctuation_is_normal(self) -> None:
        self.assertEqual(normal_text_ratio("...,;:()[]-/"), 1.0)

    def test_pua_glyphs_are_abnormal(self) -> None:
        # Genuine broken CID text layer: private-use-area glyphs.
        pua = "".join(chr(0xE000 + (i % 100)) for i in range(200))
        self.assertLessEqual(normal_text_ratio(pua), GIBBERISH_NORMAL_TEXT_MAX_RATIO)


class DecidePageTests(unittest.TestCase):
    def test_toc_page_is_structured(self) -> None:
        # Reproduces page 2 of the Metlife manual: dense dotted TOC.
        toc = "\n".join(
            f"{i} 항목 제목 " + "." * 90 + f" {i}" for i in range(1, 45)
        )
        profile = _profile(
            char_count=len(toc.strip()),
            normal_text_ratio=normal_text_ratio(toc),
        )
        self.assertGreaterEqual(profile.char_count, GIBBERISH_MIN_CHARS)
        decision = decide_page(profile)
        self.assertEqual(decision.page_class, PageClass.STRUCTURED)

    def test_pua_gibberish_page_is_scan_like(self) -> None:
        pua = "".join(chr(0xE000 + (i % 100)) for i in range(4000))
        profile = _profile(
            char_count=len(pua),
            normal_text_ratio=normal_text_ratio(pua),
        )
        decision = decide_page(profile)
        self.assertEqual(decision.page_class, PageClass.SCAN_LIKE)

    def test_replacement_chars_force_scan_like(self) -> None:
        # Option 2: heavy U+FFFD mojibake is a broken text layer even if the
        # surviving readable chars keep normal_text_ratio above the gate.
        profile = _profile(
            char_count=400,
            normal_text_ratio=0.6,
            replacement_char_ratio=0.5,
            replacement_char_count=120,
        )
        decision = decide_page(profile)
        self.assertEqual(decision.page_class, PageClass.SCAN_LIKE)

    def test_short_page_with_stray_replacement_char_stays_structured(self) -> None:
        # A tiny cover/section page where a single glyph fails to decode (e.g.
        # "제�") spikes replacement_char_ratio without being heavy mojibake.
        # An absolute replacement-count floor keeps it in structured parsing.
        profile = _profile(
            char_count=3,
            normal_text_ratio=0.6,
            replacement_char_ratio=0.5,
            replacement_char_count=1,
        )
        decision = decide_page(profile)
        self.assertEqual(decision.page_class, PageClass.STRUCTURED)

    def test_whitespace_inflated_char_count_does_not_force_scan_like(self) -> None:
        # Sparse page where layout spaces/newlines push char_count past the
        # gibberish floor, yet only one of a few non-space glyphs is U+FFFD.
        # The replacement gate is on the non-space count, so this stays
        # structured (regression for the whitespace-inclusive floor bug).
        profile = _profile(
            char_count=GIBBERISH_MIN_CHARS + 20,
            normal_text_ratio=0.6,
            replacement_char_ratio=0.5,
            replacement_char_count=1,
        )
        decision = decide_page(profile)
        self.assertEqual(decision.page_class, PageClass.STRUCTURED)

    def test_clean_structured_page_unaffected(self) -> None:
        profile = _profile(char_count=1200, normal_text_ratio=0.85)
        self.assertEqual(decide_page(profile).page_class, PageClass.STRUCTURED)

    def test_scanned_image_page_still_scan_like(self) -> None:
        profile = _profile(char_count=0, normal_text_ratio=0.0, image_area_ratio=0.99)
        self.assertEqual(decide_page(profile).page_class, PageClass.SCAN_LIKE)


if __name__ == "__main__":
    unittest.main()
