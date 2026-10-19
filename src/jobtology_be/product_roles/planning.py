"""Versioned product behavior applied after the source catalog has been verified.

The source release continues to describe reviewed competencies and effort. This policy
models unpriced self-study at zero cost and selects a practical delivery format; it
does not rewrite that release or invent learning prerequisite relationships.
"""

from dataclasses import dataclass, replace
from typing import Final

from jobtology_be.corpus.snapshot import PublishedCorpusSnapshot
from jobtology_be.planning.candidate_models import ActivityTemplate, KnownKrwCost
from jobtology_be.planning.contracts import PlanningConstraints

PRODUCT_PLANNING_POLICY_VERSION: Final = 'PRODUCT_ROLE_PLANNING_V1'


@dataclass(frozen=True, slots=True)
class RuntimePlanningCatalog:
    templates: tuple[ActivityTemplate, ...]
    policy_version: str | None = None
    decisions: tuple[str, ...] = ()
    foundational_action_ids: frozenset[str] = frozenset()
    portfolio_action_ids: frozenset[str] = frozenset()
    action_id_mapping: tuple[tuple[str, str], ...] = ()


def product_planning_catalog(
    snapshot: PublishedCorpusSnapshot, constraints: PlanningConstraints,
) -> RuntimePlanningCatalog:
    if (snapshot.basis_version != 'product-roles-v1'
            or not (snapshot.release.release_id or '').startswith('product-roles-v1-')):
        return RuntimePlanningCatalog(templates=snapshot.templates)
    decisions = [('MODELED_ZERO_COST: Pricing is not modeled; product self-study costs are zero, '
                  'not verified market prices.')]
    if constraints.needs_portfolio:
        decisions.append('PREFER_PORTFOLIO_PROJECTS: Use practical projects with a concrete '
                         'portfolio deliverable for unmet competencies, preserving reviewed effort.')
    if constraints.career_switch:
        decisions.append('PRIORITIZE_UNMET_FOUNDATIONS: Prefer earlier foundational learning '
                         'without adding prerequisite relationships or repeating satisfied skills.')
    action_ids = {
        template.action_id: f'portfolio:{template.action_id}' if constraints.needs_portfolio
        else template.action_id for template in snapshot.templates
    }
    templates = tuple(replace(
        template,
        action_id=action_ids[template.action_id],
        prerequisite_action_ids=tuple(action_ids[key] for key in template.prerequisite_action_ids),
        cost=KnownKrwCost(krw=0),
        title=f'{template.title.removesuffix(" 학습")} 포트폴리오 프로젝트'
        if constraints.needs_portfolio else template.title,
        completion_criteria=(*template.completion_criteria,
                             '학습 역량을 적용한 실행 가능한 프로젝트 결과물을 만든다',
                             '코드 또는 결과물, 실행 예시, 역량 적용 설명을 README와 함께 포트폴리오로 정리한다')
        if constraints.needs_portfolio else template.completion_criteria,
    ) for template in snapshot.templates)
    return RuntimePlanningCatalog(
        templates=templates, policy_version=PRODUCT_PLANNING_POLICY_VERSION,
        decisions=tuple(decisions),
        foundational_action_ids=frozenset(template.action_id for template in templates
                                          if template.is_foundational),
        portfolio_action_ids=frozenset(action_ids.values()) if constraints.needs_portfolio else frozenset(),
        action_id_mapping=tuple(sorted(action_ids.items())),
    )
