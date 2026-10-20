"""PDF HTML preview 보정 레이어.

PDF parse 단계가 이 레이어를 사용해 DocIR에 layout hint를 반영한다.
일반 HTML 출력은 최종 DocIR의 `to_html()`이 공통 렌더러로 처리한다.
"""

from __future__ import annotations

from .normalize import enrich_pdf_doc_ir

__all__ = [
    "enrich_pdf_doc_ir",
]
