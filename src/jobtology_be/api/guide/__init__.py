"""Korean frontend integration guide rendered from canonical repository markdown."""

from importlib.resources import files
from pathlib import Path
from typing import Final

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from markdown_it import MarkdownIt

_GUIDE_RESOURCE_NAME: Final = "fe-integration.md"
# Wheel installs resolve the packaged resource; repository checkouts resolve this path.
_REPOSITORY_GUIDE_PATH: Final = Path(__file__).resolve().parents[4] / "docs" / "fe-integration.md"

_markdown = MarkdownIt("default", {"html": False})

_GUIDE_STYLE: Final = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Malgun Gothic",
    "Apple SD Gothic Neo", NanumGothic, sans-serif;
  line-height: 1.7;
  word-break: keep-all;
  color: #1f2933;
  background: #ffffff;
}
main {
  max-width: 46rem;
  margin: 0 auto;
  padding: 2rem 1.25rem 4rem;
}
h1, h2, h3 { line-height: 1.3; }
h1 { font-size: 1.75rem; margin: 0 0 1.5rem; }
h2 { font-size: 1.35rem; margin-top: 2.5rem; border-bottom: 1px solid #d9e2ec; padding-bottom: 0.35rem; }
h3 { font-size: 1.1rem; margin-top: 1.75rem; }
a { color: #1155cc; }
code, pre, kbd {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
}
code { background: #f0f4f8; padding: 0.15em 0.35em; border-radius: 3px; font-size: 0.92em; overflow-wrap: anywhere; }
pre {
  background: #f0f4f8;
  border: 1px solid #d9e2ec;
  border-radius: 6px;
  padding: 0.9rem 1rem;
  overflow-x: auto;
}
pre code { background: none; padding: 0; font-size: 0.85rem; }
table { border-collapse: collapse; display: block; overflow-x: auto; max-width: 100%; }
th, td { border: 1px solid #d9e2ec; padding: 0.4rem 0.75rem; text-align: left; vertical-align: top; }
th { background: #f0f4f8; }
blockquote { margin: 1rem 0; padding: 0.25rem 1rem; border-left: 4px solid #d9e2ec; color: #3e4c59; }
@media (prefers-color-scheme: dark) {
  body { color: #e6e9ee; background: #14181d; }
  a { color: #8ab4f8; }
  h2 { border-bottom-color: #324055; }
  code, pre, th { background: #1e242c; }
  pre { border-color: #324055; }
  th, td { border-color: #324055; }
  blockquote { border-left-color: #324055; color: #b6c1cd; }
}
"""


def load_guide_markdown() -> str:
    """Resolve the canonical guide markdown: packaged resource first, repository docs second."""
    resource = files("jobtology_be.api.guide").joinpath(_GUIDE_RESOURCE_NAME)
    if resource.is_file():
        return resource.read_text(encoding="utf-8")
    if _REPOSITORY_GUIDE_PATH.is_file():
        return _REPOSITORY_GUIDE_PATH.read_text(encoding="utf-8")
    raise FileNotFoundError(
        "Guide markdown not found as a packaged resource or at "
        f"{_REPOSITORY_GUIDE_PATH}"
    )


def render_guide_html(markdown: str) -> str:
    """Render guide markdown to a standalone, styled HTML document."""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="ko">\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Jobtology API 프론트엔드 통합 가이드</title>\n"
        f"<style>{_GUIDE_STYLE}</style>\n"
        "<body>\n<main>\n"
        f"{_markdown.render(markdown)}"
        "</main>\n</body>\n</html>\n"
    )


router = APIRouter()


@router.get("/api/guide", include_in_schema=False, response_class=HTMLResponse)
def api_guide() -> HTMLResponse:
    """Public, browser-readable rendering of docs/fe-integration.md."""
    return HTMLResponse(render_guide_html(load_guide_markdown()))
