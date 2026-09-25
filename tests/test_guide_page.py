from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jobtology_be.api.guide import (
    _REPOSITORY_GUIDE_PATH,
    load_guide_markdown,
    render_guide_html,
)
from jobtology_be.main import create_app
from jobtology_be.settings import Settings


def _client() -> TestClient:
    return TestClient(create_app(Settings()))


def test_api_guide_serves_korean_html_with_documentation_links():
    # Given
    client = _client()

    # When
    response = client.get("/api-guide")

    # Then
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert "프론트엔드 통합 가이드" in body
    assert 'href="/docs"' in body
    assert 'href="/redoc"' in body
    assert 'href="/openapi.json"' in body
    assert "READY" in body


def test_api_guide_route_is_absent_from_the_openapi_document():
    # Given
    app = create_app(Settings())

    # When
    schema = app.openapi()

    # Then
    assert "/api-guide" not in schema["paths"]


def test_renderer_escapes_raw_html():
    # When
    html = render_guide_html("<script>alert('xss')</script>")

    # Then
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_renderer_supports_tables_and_fenced_code_blocks():
    # Given
    markdown = "| a | b |\n|---|---|\n| 1 | 2 |\n\n```sh\nuv run pytest\n```\n"

    # When
    html = render_guide_html(markdown)

    # Then
    assert "<table>" in html
    assert "<pre><code" in html


def test_loader_serves_repository_markdown_from_any_working_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    # Given
    monkeypatch.chdir(tmp_path)

    # When
    loaded = load_guide_markdown()

    # Then
    assert loaded == _REPOSITORY_GUIDE_PATH.read_text(encoding="utf-8")
    assert "프론트엔드 통합 가이드" in loaded


def test_guide_documents_the_additive_native_catalog_boundary() -> None:
    # When
    loaded = load_guide_markdown()

    # Then
    assert "GET /api/v2/occupations" in loaded
    assert "GET /api/v2/publications/{publication_id}/alignments" in loaded
    assert "`READY`" in loaded
    assert "`decision_id`" in loaded
    assert "`source_enrichment_id`" in loaded
    assert "`source_posting_id`" in loaded
    assert "`source_current`" in loaded
    assert "v1의 `occupation_id`" in loaded


def test_loader_fails_loud_when_no_source_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    # Given
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "jobtology_be.api.guide._REPOSITORY_GUIDE_PATH", tmp_path / "missing.md"
    )

    # Then
    with pytest.raises(FileNotFoundError):
        _ = load_guide_markdown()
