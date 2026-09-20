from typing import Protocol

from jobtology_be.contracts import AnalysisPreview, NormalizedProfile


class GapAnalyzer(Protocol):
    def analyze(
        self, profile: NormalizedProfile, occupation_id: str, basis_version: str
    ) -> AnalysisPreview: ...
