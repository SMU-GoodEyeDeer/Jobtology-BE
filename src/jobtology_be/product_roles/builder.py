import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from types import MappingProxyType

from pydantic import JsonValue, TypeAdapter

from jobtology_be.application.requirement_metadata import RequirementMetadata
from jobtology_be.corpus.local_snapshot import LocalJsonPublishedCorpusSnapshotReader
from jobtology_be.product_roles.models import (
    ArtifactApproval,
    InputUnit,
    ProductRoleInputs,
    ProductRolePolicy,
    RolePolicy,
)

DEMAND_MIN_BASE_POSTINGS = 5


@dataclass(frozen=True, slots=True)
class BuiltProductRoles:
    document: str
    reader: LocalJsonPublishedCorpusSnapshotReader
    names: Mapping[str, str]
    metadata: Mapping[str, RequirementMetadata]


@dataclass(frozen=True, slots=True)
class ProductRoleDraft:
    document: str
    digest: str
    _snapshot_json: str
    names: Mapping[str, str]
    metadata: Mapping[str, RequirementMetadata]

    def publish(self, approval: ArtifactApproval) -> BuiltProductRoles:
        if approval.sha256 != self.digest:
            raise ProductRoleBuildError("artifact approval does not match the generated draft")
        snapshots = TypeAdapter(list[dict[str, JsonValue]]).validate_json(self._snapshot_json)
        release_id = f"product-roles-v1-{self.digest}"
        for snapshot in snapshots:
            snapshot["release"] = {
                "release_id": release_id, "state": "PUBLISHED",
                "reviewed_at": approval.reviewed_at.isoformat(),
            }
        document = json.dumps({"snapshots": snapshots}, sort_keys=True, ensure_ascii=False)
        reader = LocalJsonPublishedCorpusSnapshotReader.from_json(document)
        return BuiltProductRoles(
            document=document, reader=reader, names=self.names, metadata=self.metadata,
        )


@dataclass(frozen=True, slots=True)
class ProductRoleBuildError(Exception):
    reason: str


def build_product_roles(inputs: ProductRoleInputs, policy: ProductRolePolicy) -> ProductRoleDraft:
    ncs_runs = [source.run_id for source in inputs.sources
                if source.source_id == "ncs_competency" and source.run_id]
    if len(ncs_runs) != 1:
        raise ProductRoleBuildError("exactly one NCS source run is required")
    run_id = ncs_runs[0]
    metadata: dict[str, RequirementMetadata] = {}
    snapshots: list[dict[str, JsonValue]] = [document for role in policy.roles
                 if (document := _role_document(role, inputs, run_id, metadata)) is not None]
    if not snapshots:
        raise ProductRoleBuildError("no matching official units")
    names = {role.occupation_id: role.name for role in policy.roles
             if any(snapshot["occupation_id"] == role.occupation_id for snapshot in snapshots)}
    artifact = {
        "contract_version": "jobtology-product-role-draft-v1",
        "policy_version": policy.version,
        "rule_approval": policy.rule_approval.model_dump(mode="json"),
        "sources": sorted(
            (source.model_dump(mode="json") for source in inputs.sources),
            key=lambda source: (source["source_id"], source["run_id"] or "",
                                source["publication_id"] or ""),
        ),
        "input_units": [unit.model_dump(mode="json")
                        for unit in sorted(inputs.units, key=lambda unit: unit.code)],
        "input_qualifications": [qualification.model_dump(mode="json")
                                 for qualification in sorted(
                                     inputs.qualifications,
                                     key=lambda item: (item.competency_code, item.qualification_code),
                                 )],
        "input_evidence": [evidence.model_dump(mode="json")
                           for evidence in sorted(inputs.evidence,
                                                  key=lambda item: item.competency_code)],
        "snapshots": snapshots,
        "input_linked_postings": [posting.model_dump(mode="json")
                                  for posting in sorted(inputs.linked_postings,
                                                        key=lambda item: item.posting_key)]
        if inputs.linked_postings is not None else None,
        "demand_rule": {"basis": "DISTINCT_LINKED_POSTINGS_PER_ROLE",
                        "min_base_postings": DEMAND_MIN_BASE_POSTINGS},
        "display_names": names,
        "requirement_metadata": {key: asdict(value) for key, value in sorted(metadata.items())},
    }
    document = json.dumps(artifact, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return ProductRoleDraft(
        document=document,
        digest=hashlib.sha256(document.encode()).hexdigest(),
        _snapshot_json=json.dumps(snapshots, sort_keys=True, ensure_ascii=False),
        names=MappingProxyType(names),
        metadata=MappingProxyType(metadata),
    )


def _role_document(
    role: RolePolicy, inputs: ProductRoleInputs, run_id: str,
    metadata: dict[str, RequirementMetadata],
) -> dict[str, JsonValue] | None:
    selected: dict[str, InputUnit] = {}
    for unit in inputs.units:
        if unit.occupation_code not in role.occupation_codes and unit.base_code not in role.additional_base_codes:
            continue
        if unit.base_code not in selected or unit.code > selected[unit.base_code].code:
            selected[unit.base_code] = unit
    selected = {code: unit for code, unit in selected.items() if "(구버전)" not in unit.name}
    if not selected:
        return None
    demand = _role_demand(inputs, frozenset(selected))
    requirements: list[JsonValue] = []
    capabilities: list[JsonValue] = []
    templates: list[JsonValue] = []
    for base_code, unit in sorted(selected.items()):
        key = f"{role.occupation_id}:{base_code}"
        evidence = [item for item in inputs.evidence
                    if any(candidate.base_code == base_code and candidate.code == item.competency_code
                           for candidate in inputs.units)]
        evidence_count = sum(item.postings for item in evidence)
        required = (unit.level is not None and unit.level <= 5) or evidence_count > 0
        refs: list[JsonValue] = [f"ncs:{unit.code}@{run_id}"]
        refs.extend(sorted({f"evidence:{publication_id}:{item.competency_code}"
                            for item in evidence for publication_id in item.publication_ids}))
        hours = [item.minimum_training_hours for item in inputs.qualifications
                 if item.competency_code == unit.code and item.minimum_training_hours is not None
                 and item.minimum_training_hours > 0]
        duration = min(hours) if hours else 20
        demand_postings, demand_base = demand(base_code)
        metadata[key] = RequirementMetadata(
            ncs_level=unit.level,
            demand_pct=round(100 * demand_postings / demand_base)
            if demand_postings is not None and demand_base is not None
            and demand_base >= DEMAND_MIN_BASE_POSTINGS else None,
            estimated_hours=duration,
            hours_basis="OFFICIAL" if hours else "ESTIMATED",
            demand_postings=demand_postings, demand_base=demand_base,
        )
        requirement: dict[str, JsonValue] = {
            "requirement_key": key, "label": unit.name,
            "necessity": "REQUIRED" if required else "PREFERRED",
            "entity_id": f"ncs:unit:{base_code}", "support_refs": refs,
        }
        aliases: list[JsonValue] = []
        aliases.extend(sorted({unit.name, base_code, *role.aliases.get(base_code, ())}))
        capability: dict[str, JsonValue] = {
            "entity_id": f"ncs:unit:{base_code}",
            "aliases": aliases,
        }
        outcome_keys: list[JsonValue] = [key]
        completion: list[JsonValue] = [
            f"NCS 능력단위 '{unit.name}'의 수행준거를 학습하고 실습 결과물 1건을 정리한다"
        ]
        cost: dict[str, JsonValue] = {"kind": "UNKNOWN"}
        template: dict[str, JsonValue] = {
            "action_id": f"study:{base_code}", "revision": 1, "title": f"{unit.name} 학습",
            "estimated_hours": duration, "outcome_requirement_keys": outcome_keys,
            "prerequisite_action_ids": [],
            "completion_criteria": completion,
            "support_refs": refs, "cost": cost,
            "is_foundational": unit.level is not None and unit.level <= 3,
        }
        requirements.append(requirement)
        capabilities.append(capability)
        templates.append(template)
    return {
        "occupation_id": role.occupation_id, "basis_version": "product-roles-v1",
        "is_fixture": False, "allowed_experience_codes": [],
        "capability_entries": capabilities, "requirements": requirements, "templates": templates,
    }


def _role_demand(
    inputs: ProductRoleInputs, base_codes: frozenset[str],
) -> Callable[[str], tuple[int | None, int | None]]:
    if inputs.linked_postings is None:
        return lambda _base_code: (None, None)
    base_of = {unit.code: unit.base_code for unit in inputs.units if unit.base_code in base_codes}
    posting_bases = {
        posting.posting_key: bases
        for posting in inputs.linked_postings
        if (bases := {base_of[code] for code in posting.competency_codes if code in base_of})
    }
    total = len(posting_bases)

    def demand(base_code: str) -> tuple[int | None, int | None]:
        return sum(base_code in bases for bases in posting_bases.values()), total

    return demand
