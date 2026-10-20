"""PDF preview 전용 모델과 threshold 상수.

여기 모델들은 canonical DocIR에 넣기 애매한 PDF 전용 sidecar 정보다.
ODL layout region, pdfium에서 뽑은 선/박스 primitive, visual block 후보,
그리고 normalize 단계에서 임시로 쓰는 매칭 결과를 정의한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from ...models import ImageIR, ParagraphIR, RunIR, TableIR
from ..meta import PdfBoundingBox

_VISUAL_TOUCH_TOLERANCE_PT = 1.0
_VISUAL_MIN_LINE_SEGMENT_PT = 5.0
_VISUAL_SEGMENTED_AXIS_TOLERANCE_PT = 1.0
_VISUAL_SEGMENTED_GAP_TOLERANCE_PT = 3.0
_VISUAL_SEGMENTED_MIN_PARTS = 3
_VISUAL_SEGMENTED_MAX_FRAGMENT_PT = 18.0
_VISUAL_SEGMENTED_MIN_SPAN_PT = 10.0
_VISUAL_FRAME_MIN_SIZE_PT = 10.0
_VISUAL_OPEN_FRAME_PRIMITIVE_LIMIT = 500
_CANDIDATE_ASSIGN_TOLERANCE_PT = 2.0
_VISUAL_BOX_SEED_MIN_SIZE_PT = 4.0
_VISUAL_LINE_JOIN_TOLERANCE_PT = 1.5
_LAYOUT_TABLE_GROUP_GAP_TOLERANCE_PT = 18.0
_LAYOUT_TABLE_ALIGNMENT_OVERLAP_RATIO = 0.75
_LAYOUT_TABLE_BOUNDARY_TOLERANCE_PT = 4.0

class PdfLayoutRegion(BaseModel):
    """ODL raw의 layout region.

    `left-page/right-page`는 모아찍힌 논리 페이지 분리용이고,
    `left-column/right-column`은 2단 flow 렌더용 힌트다.
    """
    region_id: str
    region_type: str
    page_number: int
    bounding_box: PdfBoundingBox | None = None


class PdfPreviewTableContext(BaseModel):
    """ODL table의 grid boundary 정보를 HTML table geometry에 보강하기 위한 context."""
    page_number: int | None = None
    bounding_box: PdfBoundingBox | None = None
    grid_row_boundaries: list[float] = Field(default_factory=list)
    grid_column_boundaries: list[float] = Field(default_factory=list)


class PdfPreviewVisualPrimitive(BaseModel):
    """pdfium에서 추출한 선/박스 같은 저수준 시각 요소."""
    page_number: int
    draw_order: int
    object_type: str
    bounding_box: PdfBoundingBox
    fill_color: str | None = None
    stroke_color: str | None = None
    stroke_width_pt: float | None = None
    has_fill: bool = False
    has_stroke: bool = False
    is_axis_aligned_box: bool = False
    candidate_roles: list[str] = Field(default_factory=list)


class PdfPreviewVisualBlockCandidate(BaseModel):
    """시각 요소들을 묶어 만든 layout table 승격 후보."""
    page_number: int
    candidate_type: str
    bounding_box: PdfBoundingBox
    primitive_draw_orders: list[int] = Field(default_factory=list)
    source_roles: list[str] = Field(default_factory=list)
    child_cells: list[PdfBoundingBox] = Field(default_factory=list)


class PdfPreviewContext(BaseModel):
    """PDF preview normalize에 필요한 sidecar 정보 묶음."""
    layout_regions: list[PdfLayoutRegion] = Field(default_factory=list)
    tables: list[PdfPreviewTableContext] = Field(default_factory=list)
    visual_block_candidates: list[PdfPreviewVisualBlockCandidate] = Field(default_factory=list)


@dataclass(slots=True)
class _PreviewRenderNode:
    """paragraph/table/image/run을 bbox 기준으로 후보 영역에 배정하기 위한 임시 노드."""
    kind: str
    node_id: str
    bbox: PdfBoundingBox
    order_key: tuple[float, float, int, int]
    parent_paragraph_id: str | None = None
    parent_para_style: Any = None
    paragraph: ParagraphIR | None = None
    table: TableIR | None = None
    image: ImageIR | None = None
    run: RunIR | None = None


@dataclass(slots=True)
class _AssignedCandidate:
    """visual block candidate 하나에 실제 DocIR 노드들이 배정된 결과."""
    candidate: PdfPreviewVisualBlockCandidate
    region_type: str
    top_offset_pt: float | None
    bottom_offset_pt: float | None
    order_key: tuple[float, float, int, int]
    paragraph_nodes: list[_PreviewRenderNode]
    table_nodes: list[_PreviewRenderNode]
    image_nodes: list[_PreviewRenderNode]
    run_nodes: list[_PreviewRenderNode]


@dataclass(slots=True)
class _AssignedCandidateGroup:
    """인접한 assigned candidate들을 하나의 layout table로 승격하기 위한 그룹."""
    candidates: list[_AssignedCandidate]
    region_type: str
    top_offset_pt: float | None
    bottom_offset_pt: float | None
    order_key: tuple[float, float, int, int]
    bounding_box: PdfBoundingBox


__all__ = [
    "PdfLayoutRegion",
    "PdfPreviewTableContext",
    "PdfPreviewVisualPrimitive",
    "PdfPreviewVisualBlockCandidate",
    "PdfPreviewContext",
    "_PreviewRenderNode",
    "_AssignedCandidate",
    "_AssignedCandidateGroup",
]
