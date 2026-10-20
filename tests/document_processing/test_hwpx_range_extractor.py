from __future__ import annotations

from io import BytesIO
import unittest
from xml.etree import ElementTree as ET
import zipfile

from jobtology_be.document_processor import DocIR, extract_hwpx_range, extract_hwpx_ranges


class HwpxRangeExtractorTests(unittest.TestCase):
    PARAGRAPH_TEXTS = ["First", "Second", "Third", "Fourth"]

    @classmethod
    def _build_sample_hwpx_bytes(cls) -> bytes:
        paragraphs = "\n".join(
            f"  <hp:p><hp:run><hp:t>{text}</hp:t></hp:run></hp:p>"
            for text in cls.PARAGRAPH_TEXTS
        )
        hwpx_bytes = BytesIO()
        with zipfile.ZipFile(hwpx_bytes, "w") as archive:
            archive.writestr(
                "Contents/header.xml",
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head" '
                'xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core" />\n',
            )
            archive.writestr(
                "Contents/section0.xml",
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
                'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">\n'
                f"{paragraphs}\n"
                "</hs:sec>\n",
            )
            # Non-section parts that must survive extraction untouched.
            archive.writestr("Contents/content.hpf", "<manifest/>")
            archive.writestr("BinData/image1.png", b"\x89PNG\r\n\x1a\n-fake-")
        return hwpx_bytes.getvalue()

    @staticmethod
    def _paragraph_texts(hwpx_bytes: bytes) -> list[str]:
        doc = DocIR.from_file(hwpx_bytes, doc_type="hwpx")
        return [paragraph.text for paragraph in doc.paragraphs]

    @staticmethod
    def _entry_bytes(hwpx_bytes: bytes, name: str) -> bytes:
        with zipfile.ZipFile(BytesIO(hwpx_bytes)) as archive:
            return archive.read(name)

    def test_node_ids_match_docir(self) -> None:
        source = self._build_sample_hwpx_bytes()
        doc = DocIR.from_file(source, doc_type="hwpx")

        self.assertEqual(
            [paragraph.text for paragraph in doc.paragraphs],
            self.PARAGRAPH_TEXTS,
        )
        # The extractor must recognize exactly the node ids DocIR exposes.
        node_ids = {paragraph.node_id for paragraph in doc.paragraphs}
        self.assertEqual(len(node_ids), len(self.PARAGRAPH_TEXTS))

    def test_extract_middle_range_keeps_only_that_range(self) -> None:
        source = self._build_sample_hwpx_bytes()
        doc = DocIR.from_file(source, doc_type="hwpx")
        paragraphs = doc.paragraphs

        extracted = extract_hwpx_range(
            source,
            start_node_id=paragraphs[1].node_id,
            end_node_id=paragraphs[2].node_id,
        )

        self.assertEqual(self._paragraph_texts(extracted), ["Second", "Third"])

    def test_extract_single_paragraph(self) -> None:
        source = self._build_sample_hwpx_bytes()
        paragraphs = DocIR.from_file(source, doc_type="hwpx").paragraphs

        extracted = extract_hwpx_range(
            source,
            start_node_id=paragraphs[0].node_id,
            end_node_id=paragraphs[0].node_id,
        )

        self.assertEqual(self._paragraph_texts(extracted), ["First"])

    def test_non_section_parts_preserved_verbatim(self) -> None:
        source = self._build_sample_hwpx_bytes()
        paragraphs = DocIR.from_file(source, doc_type="hwpx").paragraphs

        extracted = extract_hwpx_range(
            source,
            start_node_id=paragraphs[1].node_id,
            end_node_id=paragraphs[2].node_id,
        )

        for name in ("Contents/header.xml", "Contents/content.hpf", "BinData/image1.png"):
            self.assertEqual(
                self._entry_bytes(extracted, name),
                self._entry_bytes(source, name),
                f"{name} must be preserved byte-for-byte",
            )

    @staticmethod
    def _build_multi_section_hwpx_bytes() -> bytes:
        """A 2-section package with the manifests that must stay consistent on drop."""
        section0 = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
            'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">\n'
            "  <hp:p><hp:run><hp:t>Front A</hp:t></hp:run></hp:p>\n"
            "  <hp:p><hp:run><hp:t>Front B</hp:t></hp:run></hp:p>\n"
            "</hs:sec>\n"
        )
        section1 = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
            'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">\n'
            "  <hp:p><hp:run><hp:t>Body One</hp:t></hp:run></hp:p>\n"
            "  <hp:p><hp:run><hp:t>Body Two</hp:t></hp:run></hp:p>\n"
            "  <hp:p><hp:run><hp:t>Body Three</hp:t></hp:run></hp:p>\n"
            "</hs:sec>\n"
        )
        content_hpf = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
            '<opf:package xmlns:opf="http://www.idpf.org/2007/opf/"><opf:manifest>'
            '<opf:item id="header" href="Contents/header.xml" media-type="application/xml"/>'
            '<opf:item id="section0" href="Contents/section0.xml" media-type="application/xml"/>'
            '<opf:item id="section1" href="Contents/section1.xml" media-type="application/xml"/>'
            '</opf:manifest><opf:spine><opf:itemref idref="header"/>'
            '<opf:itemref idref="section0"/>'
            '<opf:itemref idref="section1" linear="yes"/></opf:spine></opf:package>'
        )
        pkg = "http://www.hancom.co.kr/hwpml/2016/meta/pkg#"
        container_rdf = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
            '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            f'<rdf:Description rdf:about=""><ns0:hasPart xmlns:ns0="{pkg}" rdf:resource="Contents/section0.xml"/></rdf:Description>'
            f'<rdf:Description rdf:about="Contents/section0.xml"><rdf:type rdf:resource="{pkg}SectionFile"/></rdf:Description>'
            f'<rdf:Description rdf:about=""><ns0:hasPart xmlns:ns0="{pkg}" rdf:resource="Contents/section1.xml"/></rdf:Description>'
            f'<rdf:Description rdf:about="Contents/section1.xml"><rdf:type rdf:resource="{pkg}SectionFile"/></rdf:Description>'
            "</rdf:RDF>"
        )
        header = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head" '
            'xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core" secCnt="2" />'
        )
        hwpx_bytes = BytesIO()
        with zipfile.ZipFile(hwpx_bytes, "w") as archive:
            archive.writestr("Contents/header.xml", header)
            archive.writestr("Contents/section0.xml", section0)
            archive.writestr("Contents/section1.xml", section1)
            archive.writestr("Contents/content.hpf", content_hpf)
            archive.writestr("META-INF/container.rdf", container_rdf)
        return hwpx_bytes.getvalue()

    def test_multi_section_drop_renumbers_and_fixes_manifests(self) -> None:
        source = self._build_multi_section_hwpx_bytes()
        paragraphs = DocIR.from_file(source, doc_type="hwpx").paragraphs
        # Range lives in the second section (Body One / Body Two).
        body = [p for p in paragraphs if p.native_anchor.structural_path.startswith("s2.p")]

        extracted = extract_hwpx_range(
            source,
            start_node_id=body[0].node_id,
            end_node_id=body[1].node_id,
        )

        self.assertEqual(self._paragraph_texts(extracted), ["Body One", "Body Two"])

        with zipfile.ZipFile(BytesIO(extracted)) as archive:
            names = archive.namelist()
            self.assertEqual(
                [n for n in names if n.startswith("Contents/section")],
                ["Contents/section0.xml"],
            )
            content_hpf = archive.read("Contents/content.hpf").decode("utf-8")
            self.assertIn('idref="section0"', content_hpf)
            self.assertNotIn("section1", content_hpf)

            container_rdf = archive.read("META-INF/container.rdf").decode("utf-8")
            ET.fromstring(container_rdf)  # must stay well-formed
            self.assertNotIn("section1.xml", container_rdf)
            self.assertIn("Contents/section0.xml", container_rdf)

            header = archive.read("Contents/header.xml").decode("utf-8")
            self.assertIn('secCnt="1"', header)

    @staticmethod
    def _build_secpr_hwpx_bytes() -> bytes:
        """Single section whose first paragraph carries the section definition."""
        hwpx_bytes = BytesIO()
        with zipfile.ZipFile(hwpx_bytes, "w") as archive:
            archive.writestr(
                "Contents/header.xml",
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head" '
                'xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core" secCnt="1" />\n',
            )
            archive.writestr(
                "Contents/section0.xml",
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
                'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">\n'
                "  <hp:p><hp:run>"
                '<hp:secPr><hp:pagePr width="59528" height="84188"/></hp:secPr>'
                "<hp:ctrl><hp:colPr/></hp:ctrl>"
                "<hp:t>Title</hp:t></hp:run></hp:p>\n"
                "  <hp:p><hp:run><hp:t>Body One</hp:t></hp:run></hp:p>\n"
                "  <hp:p><hp:run><hp:t>Body Two</hp:t></hp:run></hp:p>\n"
                "  <hp:p><hp:run><hp:t>Body Three</hp:t></hp:run></hp:p>\n"
                "</hs:sec>\n",
            )
        return hwpx_bytes.getvalue()

    def test_mid_range_carries_section_definition_into_first_kept_paragraph(self) -> None:
        source = self._build_secpr_hwpx_bytes()
        paragraphs = DocIR.from_file(source, doc_type="hwpx").paragraphs
        # Range starts at "Body One" (p2), so the section definition (in p1) would
        # otherwise be lost.
        extracted = extract_hwpx_range(
            source,
            start_node_id=paragraphs[1].node_id,
            end_node_id=paragraphs[2].node_id,
        )

        # No extra empty paragraph — content is exactly the requested range.
        self.assertEqual(self._paragraph_texts(extracted), ["Body One", "Body Two"])

        HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
        root = ET.fromstring(self._entry_bytes(extracted, "Contents/section0.xml"))
        first_paragraph = root.find(f"{HP}p")
        # The section definition (secPr + columns) is carried as a leading run of the
        # first kept paragraph, which still holds its own text.
        self.assertIsNotNone(first_paragraph.find(f".//{HP}secPr"))
        self.assertIsNotNone(first_paragraph.find(f".//{HP}colPr"))
        self.assertEqual("".join(first_paragraph.itertext()).strip(), "Body One")
        self.assertEqual(len(root.findall(f".//{HP}secPr")), 1)

    @staticmethod
    def _build_imaged_hwpx_bytes() -> bytes:
        """Single section with a header image-fill (image1) and an inline image (image2)."""
        header = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head" '
            'xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core" secCnt="1">'
            '<hc:fillBrush><hc:imgBrush><hc:img binaryItemIDRef="image1"/></hc:imgBrush></hc:fillBrush>'
            "</hh:head>"
        )
        section0 = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
            'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
            "<hp:p><hp:run><hp:secPr/><hp:t>Title</hp:t></hp:run></hp:p>"
            '<hp:p><hp:run><hp:pic binaryItemIDRef="image2"/><hp:t>Has image</hp:t></hp:run></hp:p>'
            "<hp:p><hp:run><hp:t>No image</hp:t></hp:run></hp:p>"
            "</hs:sec>"
        )
        content_hpf = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
            '<opf:package xmlns:opf="http://www.idpf.org/2007/opf/"><opf:manifest>'
            '<opf:item id="header" href="Contents/header.xml" media-type="application/xml"/>'
            '<opf:item id="section0" href="Contents/section0.xml" media-type="application/xml"/>'
            '<opf:item id="image1" href="BinData/image1.png" media-type="image/png" isEmbeded="1"/>'
            '<opf:item id="image2" href="BinData/image2.png" media-type="image/png" isEmbeded="1"/>'
            '<opf:item id="image3" href="BinData/image3.png" media-type="image/png" isEmbeded="1"/>'
            '</opf:manifest><opf:spine><opf:itemref idref="header"/>'
            '<opf:itemref idref="section0"/></opf:spine></opf:package>'
        )
        hwpx_bytes = BytesIO()
        with zipfile.ZipFile(hwpx_bytes, "w") as archive:
            archive.writestr("Contents/header.xml", header)
            archive.writestr("Contents/section0.xml", section0)
            archive.writestr("Contents/content.hpf", content_hpf)
            for name in ("image1", "image2", "image3"):
                archive.writestr(f"BinData/{name}.png", b"\x89PNG\r\n\x1a\n-" + name.encode())
        return hwpx_bytes.getvalue()

    def test_prunes_unreferenced_images_but_keeps_header_and_referenced(self) -> None:
        source = self._build_imaged_hwpx_bytes()
        paragraphs = DocIR.from_file(source, doc_type="hwpx").paragraphs
        # Extract only the last paragraph ("No image"): it references no image, so the
        # only survivors should be image1 (used by a header fill style).
        extracted = extract_hwpx_range(
            source,
            start_node_id=paragraphs[2].node_id,
            end_node_id=paragraphs[2].node_id,
        )

        with zipfile.ZipFile(BytesIO(extracted)) as archive:
            bindata = sorted(n for n in archive.namelist() if n.startswith("BinData/"))
            self.assertEqual(bindata, ["BinData/image1.png"])  # header-referenced kept
            content_hpf = archive.read("Contents/content.hpf").decode("utf-8")
            self.assertIn('id="image1"', content_hpf)
            self.assertNotIn('id="image2"', content_hpf)  # dropped: not in range
            self.assertNotIn('id="image3"', content_hpf)  # dropped: unreferenced

    def test_keeps_image_referenced_by_kept_range(self) -> None:
        source = self._build_imaged_hwpx_bytes()
        paragraphs = DocIR.from_file(source, doc_type="hwpx").paragraphs
        # Range includes the paragraph that references image2.
        extracted = extract_hwpx_range(
            source,
            start_node_id=paragraphs[1].node_id,
            end_node_id=paragraphs[2].node_id,
        )
        with zipfile.ZipFile(BytesIO(extracted)) as archive:
            bindata = sorted(n for n in archive.namelist() if n.startswith("BinData/"))
            self.assertEqual(bindata, ["BinData/image1.png", "BinData/image2.png"])

    def test_batch_matches_individual_extraction(self) -> None:
        source = self._build_sample_hwpx_bytes()
        paragraphs = DocIR.from_file(source, doc_type="hwpx").paragraphs
        ranges = [
            (paragraphs[0].node_id, paragraphs[1].node_id),
            (paragraphs[2].node_id, paragraphs[3].node_id),
        ]

        batch = extract_hwpx_ranges(source, ranges)

        self.assertEqual(len(batch), 2)
        self.assertEqual(self._paragraph_texts(batch[0]), ["First", "Second"])
        self.assertEqual(self._paragraph_texts(batch[1]), ["Third", "Fourth"])
        # Batch output must be identical to extracting each range on its own.
        for (start_id, end_id), batched in zip(ranges, batch):
            individual = extract_hwpx_range(source, start_node_id=start_id, end_node_id=end_id)
            self.assertEqual(batched, individual)

    def test_range_spanning_sections_keeps_both(self) -> None:
        source = self._build_multi_section_hwpx_bytes()
        paragraphs = DocIR.from_file(source, doc_type="hwpx").paragraphs
        front = [p for p in paragraphs if p.native_anchor.structural_path.startswith("s1.p")]
        body = [p for p in paragraphs if p.native_anchor.structural_path.startswith("s2.p")]

        # Span from the 2nd paragraph of section1 into the 1st of section2.
        extracted = extract_hwpx_range(
            source,
            start_node_id=front[1].node_id,
            end_node_id=body[0].node_id,
        )

        self.assertEqual(self._paragraph_texts(extracted), ["Front B", "Body One"])
        with zipfile.ZipFile(BytesIO(extracted)) as archive:
            sections = sorted(n for n in archive.namelist() if n.startswith("Contents/section"))
            self.assertEqual(sections, ["Contents/section0.xml", "Contents/section1.xml"])
            self.assertIn('secCnt="2"', archive.read("Contents/header.xml").decode("utf-8"))
            content_hpf = archive.read("Contents/content.hpf").decode("utf-8")
            self.assertIn('idref="section0"', content_hpf)
            self.assertIn('idref="section1"', content_hpf)
            rdf = archive.read("META-INF/container.rdf").decode("utf-8")
            ET.fromstring(rdf)
            self.assertIn("section1.xml", rdf)
            self.assertNotIn("section2", rdf)

    def test_unknown_node_id_raises(self) -> None:
        source = self._build_sample_hwpx_bytes()
        paragraphs = DocIR.from_file(source, doc_type="hwpx").paragraphs

        with self.assertRaises(KeyError):
            extract_hwpx_range(
                source,
                start_node_id=paragraphs[0].node_id,
                end_node_id="p_does_not_exist",
            )


if __name__ == "__main__":
    unittest.main()
