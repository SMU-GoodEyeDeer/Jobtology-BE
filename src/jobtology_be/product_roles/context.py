from dataclasses import dataclass
from uuid import UUID

from jobtology_be.application.services.analyses import AnalysisRequestCommand
from jobtology_be.application.services.analysis_context import (
    ContextSnapshotConfiguration,
    SnapshotBackedAnalysisContextFactory,
)
from jobtology_be.application.services.analysis_inputs import (
    AnalysisContextInputsUnavailableError,
    PostgresAnalysisContextInputSource,
)
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.product_roles.holder import ProductRoleHolder
from jobtology_be.workers.context import RecomputeContextDocument


@dataclass(frozen=True, slots=True)
class ProductRoleAnalysisContextFactory:
    database: Database
    holder: ProductRoleHolder
    capability_list_authoritative: bool = False

    async def create_context(
        self, user_id: UUID, command: AnalysisRequestCommand,
    ) -> RecomputeContextDocument:
        if not self.holder.available:
            raise AnalysisContextInputsUnavailableError("product roles are unavailable")
        snapshots = self.holder.snapshots
        release_id = snapshots[0].release.release_id
        if release_id is None:
            raise AnalysisContextInputsUnavailableError("product role release is unavailable")
        return await SnapshotBackedAnalysisContextFactory(
            source=PostgresAnalysisContextInputSource(
                self.database, capability_list_authoritative=self.capability_list_authoritative,
            ),
            snapshot_reader=self.holder,
            configuration=ContextSnapshotConfiguration(
                basis_version="product-roles-v1",
                release_id=release_id,
            ),
        ).create_context(user_id, command)
