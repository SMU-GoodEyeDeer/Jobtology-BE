from typing import Protocol


class RoadmapService(Protocol):
    async def materialize(
        self, user_id: str, proposal_id: str, expected_profile_version: int
    ) -> str: ...

    async def activate(
        self,
        user_id: str,
        roadmap_id: str,
        expected_roadmap_version: int,
        expected_profile_version: int,
    ) -> None: ...
