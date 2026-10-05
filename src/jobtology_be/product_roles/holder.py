from importlib.resources.abc import Traversable

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from jobtology_be.application.m5_queries import M5DataUnavailableError
from jobtology_be.application.requirement_metadata import RequirementMetadata
from jobtology_be.corpus.snapshot import (
    CorpusSnapshotError,
    PublishedCorpusSnapshot,
    PublishedSnapshotSelection,
    PublishedSnapshotUnavailableError,
)
from jobtology_be.modules.analyses.editorial_models import EditorialBaselineError
from jobtology_be.planning.candidate_models import CandidateTemplateError
from jobtology_be.product_roles.builder import (
    BuiltProductRoles,
    ProductRoleBuildError,
    ProductRoleDraft,
    build_product_roles,
)
from jobtology_be.product_roles.models import ArtifactApproval, ProductRoleInputs, ProductRolePolicy


class ProductRoleHolder:
    """Load one immutable role release at startup; remain unavailable on read failure."""

    def __init__(
        self, engine: AsyncEngine, policy_path: Traversable,
        approval_path: Traversable | None = None,
    ) -> None:
        self.engine: AsyncEngine = engine
        self.policy_path: Traversable = policy_path
        self.approval_path: Traversable | None = approval_path
        self._built: BuiltProductRoles | None = None
        self._draft: ProductRoleDraft | None = None

    @property
    def draft(self) -> ProductRoleDraft | None:
        return self._draft

    @property
    def snapshots(self) -> tuple[PublishedCorpusSnapshot, ...]:
        if self._built is None:
            raise M5DataUnavailableError(resource="occupation catalog")
        return self._built.reader.snapshots

    @property
    def available(self) -> bool:
        return self._built is not None

    async def load(self) -> bool:
        self._built = None
        self._draft = None
        try:
            policy = ProductRolePolicy.model_validate_json(self.policy_path.read_text())
            async with self.engine.connect() as connection, connection.begin():
                _ = await connection.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ ONLY"))
                result = await connection.execute(
                    text("SELECT CAST(catalog.product_role_inputs_v1(CAST(:codes AS text[])) AS text)"),
                    {"codes": list(policy.requested_occupation_codes)},
                )
                payload = result.scalar_one()
            if not isinstance(payload, str):
                return False
            self._draft = build_product_roles(ProductRoleInputs.model_validate_json(payload), policy)
            if self.approval_path is None or not self.approval_path.is_file():
                return False
            approval = ArtifactApproval.model_validate_json(self.approval_path.read_text())
            self._built = self._draft.publish(approval)
        except (SQLAlchemyError, ValidationError, ProductRoleBuildError,
                CorpusSnapshotError, EditorialBaselineError, CandidateTemplateError,
                OSError, TimeoutError, ValueError):
            return False
        return True

    async def get_snapshot(self, selection: PublishedSnapshotSelection) -> PublishedCorpusSnapshot:
        if self._built is None:
            raise PublishedSnapshotUnavailableError(selection)
        return await self._built.reader.get_snapshot(selection)

    def display_name(self, occupation_id: str) -> str | None:
        return self._built.names.get(occupation_id) if self._built is not None else None

    def lookup(self, requirement_key: str) -> RequirementMetadata | None:
        return self._built.metadata.get(requirement_key) if self._built is not None else None
