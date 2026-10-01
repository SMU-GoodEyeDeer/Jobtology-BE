import json
from pathlib import Path

import pytest
from pydantic import JsonValue, ValidationError

from jobtology_be.corpus.snapshot import PublishedCorpusSnapshot
from jobtology_be.editorial import DraftCatalog, DraftReadService, load_drafts
from jobtology_be.modules.analyses.editorial_models import (
    EditorialReleaseMetadata,
    ReleaseState,
    UnusableReleaseError,
)

DATA = Path(__file__).resolve().parents[1] / "config/editorial/four_roles.v1.json"


def _payload() -> dict[str, JsonValue]:
    return json.loads(DATA.read_text(encoding="utf-8"))


def test_load_four_roles_when_file_is_valid() -> None:
    # Given a versioned, read-only editorial file
    # When it is imported
    catalog = load_drafts(DATA)
    # Then it remains draft-only with a stable byte identity
    assert isinstance(catalog, DraftCatalog)
    assert catalog.version == 1
    assert len(catalog.content_sha256) == 64
    assert catalog.content_sha256 == load_drafts(DATA).content_sha256
    assert {item.occupation_id for item in catalog.occupations} == {
        "AI_ENGINEER",
        "BACKEND_DEVELOPER",
        "FRONTEND_DEVELOPER",
        "DATA_ANALYST",
    }
    assert all(item.status == "DRAFT" and item.reviewed_at is None for item in catalog.occupations)
    assert all(len(item.activities) >= 3 for item in catalog.occupations)
    assert all(
        not requirement.source_refs
        for role in catalog.occupations
        for requirement in role.requirements
    )


def test_read_projection_when_draft_is_loaded() -> None:
    # Given an imported draft catalog
    service = DraftReadService(load_drafts(DATA))
    # When reading an occupation
    response = service.get_occupation("AI_ENGINEER")
    # Then only explicitly draft, non-analysis-ready content is exposed
    assert response is not None
    assert response.status == "DRAFT"
    assert response.analysis_ready is False
    assert response.content_sha256 == service.catalog.content_sha256
    assert "source_refs" not in response.model_dump_json()
    assert "reviewed_at" not in response.model_dump_json()
    assert service.get_occupation("NONEXISTENT") is None
    assert len(service.list_occupations()) == 4


@pytest.mark.parametrize(
    ("change", "error"),
    [
        (lambda p: p["occupations"].append(p["occupations"][0]), "duplicate occupation"),
        (
            lambda p: p["occupations"][0]["requirements"].append(
                p["occupations"][0]["requirements"][0]
            ),
            "duplicate requirement",
        ),
        (
            lambda p: p["occupations"][0]["activities"].append(
                p["occupations"][0]["activities"][0]
            ),
            "duplicate activity",
        ),
        (
            lambda p: p["occupations"][0]["activities"][0].update(completion_criteria=[]),
            "completion_criteria",
        ),
        (
            lambda p: p["occupations"][0]["activities"][0].update(
                outcome_requirement_keys=["absent"]
            ),
            "unknown outcome",
        ),
        (
            lambda p: p["occupations"][0]["activities"][0].update(
                prerequisite_action_ids=[p["occupations"][0]["activities"][1]["action_id"]]
            ),
            "cycle",
        ),
        (
            lambda p: p["occupations"][0]["activities"][0].update(
                prerequisite_action_ids=["absent"]
            ),
            "prerequisite",
        ),
        (lambda p: p["occupations"][0].update(status="PUBLISHED"), "status"),
        (lambda p: p["occupations"][0].update(reviewed_at="2026-10-01"), "reviewed_at"),
        (
            lambda p: p["occupations"][0]["requirements"][0].update(support_refs=["fabricated"]),
            "support_refs",
        ),
    ],
)
def test_reject_invalid_draft_when_schema_or_graph_is_broken(change, error: str) -> None:
    # Given a malformed copy of the imported file
    payload = _payload()
    change(payload)
    # When the draft boundary parses it
    with pytest.raises((ValidationError, ValueError), match=error):
        _ = DraftCatalog.model_validate_json(json.dumps(payload))


def test_reject_unsupported_version_when_importing(tmp_path: Path) -> None:
    # Given an unsupported version on disk
    payload = _payload()
    payload["version"] = 2
    path = tmp_path / "draft.json"
    _ = path.write_text(json.dumps(payload), encoding="utf-8")
    # When imported, then no catalog is returned
    with pytest.raises(ValidationError, match="version"):
        _ = load_drafts(path)


def test_draft_cannot_be_promoted_to_snapshot_by_conversion() -> None:
    # Given a draft with no reviewed release or published support references
    draft = load_drafts(DATA).occupations[0]
    # When inspecting its typed surface
    # Then it offers neither a published release nor conversion into one
    assert not hasattr(draft, "editorial_baseline")
    assert not hasattr(draft, "support_refs")
    assert not hasattr(draft, "to_published_snapshot")
    # When an unreviewed release is passed to the published snapshot boundary
    with pytest.raises(UnusableReleaseError):
        _ = PublishedCorpusSnapshot(
            occupation_id=draft.occupation_id,
            basis_version="draft-v1",
            release=EditorialReleaseMetadata(None, ReleaseState.UNPUBLISHED, None),
            is_fixture=False,
            capability_entries=(),
            allowed_experience_codes=frozenset(),
            requirements=(),
            templates=(),
        )


def test_cost_zero_is_not_unknown_when_explicitly_known() -> None:
    # Given an explicitly known zero-cost activity instead of the default unknown cost
    payload = DATA.read_text(encoding="utf-8").replace(
        '"cost": {"kind": "UNKNOWN"}',
        '"cost": {"kind": "KNOWN_KRW", "krw": 0}',
        1,
    )
    # When the edited draft is parsed and projected
    catalog = DraftCatalog.model_validate_json(payload)
    response = DraftReadService(catalog).get_occupation("AI_ENGINEER")
    # Then zero remains known, while the unedited activity stays unknown
    assert response is not None
    assert response.activities[0].cost_status == "KNOWN_KRW"
    assert response.activities[0].known_cost_krw == 0
    assert response.activities[1].cost_status == "UNKNOWN"
    assert response.activities[1].known_cost_krw is None
    assert catalog.content_sha256 != load_drafts(DATA).content_sha256
