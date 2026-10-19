from typing import Protocol
from uuid import UUID


class AccountService(Protocol):
    async def delete_account(self, user_id: UUID) -> None: ...
