import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError

from jobtology_be.product_roles.builder import build_product_roles
from jobtology_be.product_roles.models import ArtifactApproval, ProductRoleInputs, ProductRolePolicy

POLICY = Path(__file__).resolve().parents[1] / "config/product_roles/policy.v1.json"


def _approved(inputs: ProductRoleInputs, policy: ProductRolePolicy):
    draft = build_product_roles(inputs, policy)
    return draft.publish(ArtifactApproval(
        sha256=draft.digest, approved_by="synthetic test owner",
        reviewed_at=datetime(2026, 10, 5, 9, tzinfo=UTC),
    ))


def test_builder_selects_latest_unit_and_uses_official_hours() -> None:
    # Given approved role policy and versioned official units
    policy = ProductRolePolicy.model_validate_json(POLICY.read_text())
    inputs = ProductRoleInputs.model_validate_json(json.dumps({
        "contract_version": "jobtology-product-role-inputs-v1",
        "sources": [{"source_id": "ncs_competency", "run_id": "ncs-1"}],
        "units": [
            {"code": "2001020211_20v1", "base_code": "2001020211", "name": "Old",
             "level": 4, "occupation_code": "20010202", "occupation_name": "Backend"},
            {"code": "2001020211_24v2", "base_code": "2001020211", "name": "서버프로그램 구현",
             "level": 6, "occupation_code": "20010202", "occupation_name": "Backend"},
            {"code": "2001020212_24v1", "base_code": "2001020212", "name": "인터페이스 구현(구버전)",
             "level": 3, "occupation_code": "20010202", "occupation_name": "Backend"},
        ],
        "qualifications": [{"competency_code": "2001020211_24v2", "qualification_code": "Q1",
                            "qualification_name": "Q", "minimum_training_hours": 42,
                            "total_training_hours": 90}],
        "evidence": [{"competency_code": "2001020211_20v1", "postings": 3, "links": 4,
                      "publication_ids": ["pub-1"]}],
    }))
    # When the role document is built and parsed by the real snapshot reader
    built = _approved(inputs, policy)
    snapshot = next(s for s in built.reader.snapshots if s.occupation_id == "BACKEND_DEVELOPER")
    # Then latest non-obsolete version wins, old evidence makes it required, and hours are official
    assert len(snapshot.requirements) == 1
    requirement = snapshot.requirements[0]
    assert requirement.requirement_key == "BACKEND_DEVELOPER:2001020211"
    assert requirement.necessity.value == "REQUIRED"
    assert "ncs:2001020211_24v2@ncs-1" in requirement.support_refs
    assert "evidence:pub-1:2001020211_20v1" in requirement.support_refs
    assert snapshot.templates[0].estimated_hours == 42
    assert snapshot.capability_entries[0].aliases >= {"서버", "FastAPI", "2001020211"}
    assert snapshot.release.release_id is not None
    assert snapshot.release.release_id.startswith("product-roles-v1-")
    assert snapshot.release.state.value == "PUBLISHED"
    assert snapshot.is_fixture is False
    assert built.metadata[requirement.requirement_key].ncs_level == 6
    assert built.metadata[requirement.requirement_key].demand_pct is None
    assert built.metadata[requirement.requirement_key].hours_basis == "OFFICIAL"


def test_builder_estimates_hours_and_filters_role_local_aliases() -> None:
    # Given a single frontend unit with no qualification hours
    policy = ProductRolePolicy.model_validate_json(POLICY.read_text())
    inputs = ProductRoleInputs.model_validate_json(json.dumps({
        "contract_version": "jobtology-product-role-inputs-v1",
        "sources": [{"source_id": "ncs_competency", "run_id": "ncs-1"}],
        "units": [{"code": "2001020225_24v1", "base_code": "2001020225",
                   "name": "화면 구현", "level": 7, "occupation_code": "20010202",
                   "occupation_name": "UI"},
                  {"code": "2001020211_20v1", "base_code": "2001020211",
                   "name": "서버프로그램 구현", "level": 4, "occupation_code": "20010202",
                   "occupation_name": "Backend"},
                  {"code": "2001020211_24v2", "base_code": "2001020211",
                   "name": "서버프로그램 구현(구버전)", "level": 4,
                   "occupation_code": "20010202", "occupation_name": "Backend"}],
        "qualifications": [], "evidence": [],
    }))
    # When built twice, then deterministic content selects exactly the explicit frontend unit
    first = _approved(inputs, policy)
    second = _approved(inputs, policy)
    snapshot = next(s for s in first.reader.snapshots if s.occupation_id == "FRONTEND_DEVELOPER")
    assert snapshot.release.release_id == second.reader.snapshots[0].release.release_id
    assert snapshot.requirements[0].necessity.value == "PREFERRED"
    assert snapshot.templates[0].estimated_hours == 20
    assert "React" in snapshot.capability_entries[0].aliases
    assert "SQL" not in snapshot.capability_entries[0].aliases
    assert "BACKEND_DEVELOPER:2001020211" not in first.metadata
    assert first.metadata["FRONTEND_DEVELOPER:2001020225"].hours_basis == "ESTIMATED"
    assert json.loads(first.document)["snapshots"][0]["release"]["state"] == "PUBLISHED"


def test_historical_source_provenance_changes_full_draft_digest() -> None:
    policy = ProductRolePolicy.model_validate_json(POLICY.read_text())
    source = {"source_id": "link_publication", "posting_source": "job_alio",
              "publication_id": "legacy-job", "created_at": "2026-09-16T00:00:00Z",
              "run_id": "old-job-run", "is_latest_publication": False}
    base = {"contract_version": "jobtology-product-role-inputs-v1",
            "sources": [{"source_id": "ncs_competency", "run_id": "ncs-1"}, source],
            "units": [{"code": "2001020211_24v1", "base_code": "2001020211",
                       "name": "서버프로그램 구현", "level": 6,
                       "occupation_code": "20010202", "occupation_name": "Backend"}],
            "qualifications": [], "evidence": [
                {"competency_code": "2001020211_24v1", "postings": 1,
                 "links": 1, "publication_ids": ["legacy-job"]}]}
    historical = build_product_roles(ProductRoleInputs.model_validate_json(json.dumps(base)), policy)
    newer = build_product_roles(ProductRoleInputs.model_validate_json(json.dumps({
        **base, "sources": [base["sources"][0], {**source, "is_latest_publication": True}],
    })), policy)
    assert historical.digest == sha256(historical.document.encode()).hexdigest()
    assert historical.digest != newer.digest
    assert next(source for source in json.loads(historical.document)["sources"]
                if source["source_id"] == "link_publication")["run_id"] == "old-job-run"
    assert "release" not in historical.document
    with pytest.raises(ValidationError):
        ProductRoleInputs.model_validate_json(json.dumps({
            **base, "sources": [base["sources"][0], {**source, "is_latest_publication": "false"}],
        }))
