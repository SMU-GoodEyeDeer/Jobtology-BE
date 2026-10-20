"""PDF preview에서 공통으로 쓰는 bbox/geometry 유틸.

`analyze.py`와 `normalize.py`가 둘 다 쓰는 순수 좌표 계산만 둔다.
여기 함수들은 DocIR를 직접 바꾸지 않고, bbox 포함/교차/면적/거리 같은
작은 계산만 담당한다.
"""

from __future__ import annotations

from ...models import PageInfo
from ..meta import PdfBoundingBox


def _bbox_touches_or_near(left: PdfBoundingBox, right: PdfBoundingBox, *, tolerance_pt: float) -> bool:
    """두 bbox가 겹치거나 tolerance 안에서 맞닿아 있는지 본다."""
    horizontal_gap = max(left.left_pt - right.right_pt, right.left_pt - left.right_pt, 0.0)
    vertical_gap = max(left.bottom_pt - right.top_pt, right.bottom_pt - left.top_pt, 0.0)
    return horizontal_gap <= tolerance_pt and vertical_gap <= tolerance_pt


def _bbox_contains(container: PdfBoundingBox, item: PdfBoundingBox, *, tolerance_pt: float) -> bool:
    """container bbox가 item bbox를 포함하는지 본다."""
    return (
        container.left_pt - tolerance_pt <= item.left_pt
        and container.bottom_pt - tolerance_pt <= item.bottom_pt
        and container.right_pt + tolerance_pt >= item.right_pt
        and container.top_pt + tolerance_pt >= item.top_pt
    )


def _shared_bbox_distance(left: PdfBoundingBox, right: PdfBoundingBox) -> float:
    return abs(left.left_pt - right.left_pt) + abs(left.bottom_pt - right.bottom_pt) + abs(
        left.right_pt - right.right_pt
    ) + abs(left.top_pt - right.top_pt)


def _shared_page_content_margins(page: PageInfo) -> tuple[float, float, float, float]:
    """페이지 margin이 없을 때 HTML preview 기본값으로 보정한다."""
    return (
        page.margin_top_pt if page.margin_top_pt is not None else 48.0,
        page.margin_right_pt if page.margin_right_pt is not None else 42.0,
        page.margin_bottom_pt if page.margin_bottom_pt is not None else 48.0,
        page.margin_left_pt if page.margin_left_pt is not None else 42.0,
    )


def _union_box_bounds(
    boxes: list[tuple[float, float, float, float]],
) -> tuple[float, float, float, float] | None:
    if not boxes:
        return None
    left = min(box[0] for box in boxes)
    bottom = min(box[1] for box in boxes)
    right = max(box[2] for box in boxes)
    top = max(box[3] for box in boxes)
    return (left, bottom, right, top)


def _bbox_from_bounds(bounds: tuple[float, float, float, float] | None) -> PdfBoundingBox | None:
    if bounds is None:
        return None
    return PdfBoundingBox(
        left_pt=bounds[0],
        bottom_pt=bounds[1],
        right_pt=bounds[2],
        top_pt=bounds[3],
    )


def _bbox_center(bbox: PdfBoundingBox) -> tuple[float, float]:
    return ((bbox.left_pt + bbox.right_pt) / 2.0, (bbox.bottom_pt + bbox.top_pt) / 2.0)


def _bbox_area(bbox: PdfBoundingBox) -> float:
    return max(bbox.right_pt - bbox.left_pt, 0.0) * max(bbox.top_pt - bbox.bottom_pt, 0.0)


def _bbox_intersection(left: PdfBoundingBox, right: PdfBoundingBox) -> PdfBoundingBox | None:
    intersection = PdfBoundingBox(
        left_pt=max(left.left_pt, right.left_pt),
        bottom_pt=max(left.bottom_pt, right.bottom_pt),
        right_pt=min(left.right_pt, right.right_pt),
        top_pt=min(left.top_pt, right.top_pt),
    )
    if intersection.right_pt <= intersection.left_pt or intersection.top_pt <= intersection.bottom_pt:
        return None
    return intersection
