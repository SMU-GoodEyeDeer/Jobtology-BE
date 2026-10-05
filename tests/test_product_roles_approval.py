import json
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from jobtology_be.product_roles.builder import ProductRoleBuildError, build_product_roles
from jobtology_be.product_roles.models import ArtifactApproval, ProductRoleInputs, ProductRolePolicy

POLICY = Path(__file__).resolve().parents[1] / "config/product_roles/policy.v1.json"


def _inputs(
    *, hours: int = 20, run_id: str = "ncs-1", qualification_name: str = "Q",
) -> ProductRoleInputs:
    return ProductRoleInputs.model_validate_json(json.dumps({
        "contract_version": "jobtology-product-role-inputs-v1",
        "sources": [{"source_id": "ncs_competency", "run_id": run_id}],
        "units": [{"code": "2001020211_24v1", "base_code": "2001020211",
                   "name": "서버프로그램 구현", "level": 4, "occupation_code": "20010202",
                   "occupation_name": "Backend"}],
        "qualifications": [{"competency_code": "2001020211_24v1",
                            "qualification_code": "Q1", "qualification_name": qualification_name,
                            "minimum_training_hours": hours, "total_training_hours": hours}],
        "evidence": [],
    }))


def _approval(digest: str) -> ArtifactApproval:
    return ArtifactApproval.model_validate({
        "sha256": digest, "approved_by": "test owner",
        "reviewed_at": datetime.fromisoformat("2026-10-05T09:00:00+00:00"),
    })


def test_rule_approval_only_generates_draft_until_exact_artifact_is_approved() -> None:
    # Given approved generation rules but no approval of their generated output
    policy = ProductRolePolicy.model_validate_json(POLICY.read_text())
    draft = build_product_roles(_inputs(), policy)
    # When reviewing its canonical document, then it has no published reader or review time
    assert len(draft.digest) == 64
    assert "PUBLISHED" not in draft.document
    assert "reviewed_at" not in draft.document
    assert "approved_at" not in POLICY.read_text()
    with pytest.raises(ProductRoleBuildError):
        draft.publish(_approval("0" * 64))
    # Then only an exact, separate artifact approval can produce a published reader
    published = draft.publish(_approval(draft.digest))
    assert published.reader.snapshots[0].release.state.value == "PUBLISHED"
    assert published.reader.snapshots[0].release.release_id == f"product-roles-v1-{draft.digest}"
    assert published.reader.snapshots[0].release.reviewed_at == datetime.fromisoformat(
        "2026-10-05T09:00:00+00:00"
    )


def test_review_digest_covers_names_aliases_metadata_sources_and_snapshot() -> None:
    # Given independently changed official inputs and policy presentation data
    policy = ProductRolePolicy.model_validate_json(POLICY.read_text())
    base = build_product_roles(_inputs(), policy).digest
    changed_name = ProductRolePolicy.model_validate_json(POLICY.read_text().replace(
        "백엔드 개발자", "백엔드 엔지니어",
    ))
    changed_alias = ProductRolePolicy.model_validate_json(POLICY.read_text().replace(
        '"FastAPI"', '"FastAPI", "API 서버"',
    ))
    # When recomputed, then every consumed/displayed difference needs separate approval
    assert len({
        base,
        build_product_roles(_inputs(hours=21), policy).digest,
        build_product_roles(_inputs(run_id="ncs-2"), policy).digest,
        build_product_roles(_inputs(qualification_name="Q updated"), policy).digest,
        build_product_roles(_inputs(), changed_name).digest,
        build_product_roles(_inputs(), changed_alias).digest,
    }) == 6


def test_artifact_approval_requires_timezone_aware_review_time() -> None:
    # Given a record lacking an actual timezone
    # When parsed, then it cannot approve a release
    with pytest.raises(ValidationError):
        ArtifactApproval.model_validate({
            "sha256": "a" * 64, "approved_by": "test owner",
            "reviewed_at": datetime.fromisoformat("2026-10-05T09:00:00"),
        })
