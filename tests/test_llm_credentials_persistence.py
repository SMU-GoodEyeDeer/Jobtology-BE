import io
import sys
from pathlib import Path

import anyio
import pytest
from sqlalchemy import insert, select
from test_chat import _token

from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import llm_credentials
from jobtology_be.llm import credentials_cli
from jobtology_be.llm.credentials import (
    CredentialStoreError,
    DatabaseCredentialStore,
    OAuthCredentials,
    generate_encryption_key,
    parse_encryption_key,
)

pytest_plugins = ("test_acceptance_m5_lifecycle",)


async def _exercise_store(database_url: str) -> None:
    database = Database.create(database_url)
    key = parse_encryption_key(generate_encryption_key())
    store = DatabaseCredentialStore(database, key)
    try:
        assert await store.load() is None
        first = OAuthCredentials.model_validate(
            {"tokens": {"access_token": "secret-access", "account_id": "acct", "refresh_token": "r1"}}
        )
        await store.save(first)
        rotated = first.model_copy(update={"tokens": first.tokens.model_copy(update={"refresh_token": "r2"})})
        await store.save(rotated)

        loaded = await store.load()
        assert loaded is not None
        assert loaded.tokens.refresh_token == "r2"
        async with database.sessions() as session:
            rows = (await session.execute(select(llm_credentials.c.ciphertext))).scalars().all()
        assert len(rows) == 1
        assert b"secret-access" not in rows[0]

        with pytest.raises(CredentialStoreError):
            await DatabaseCredentialStore(database, parse_encryption_key(generate_encryption_key())).load()

        async with database.sessions.begin() as session:
            await session.execute(
                insert(llm_credentials).values(provider="other", ciphertext=rows[0])
            )
        with pytest.raises(CredentialStoreError):
            await DatabaseCredentialStore(database, key, provider="other").load()
    finally:
        await database.dispose()


def test_database_credential_store_encrypts_and_rotates(acceptance_database_url: str) -> None:
    anyio.run(_exercise_store, acceptance_database_url)


def test_credentials_cli_imports_and_reports_status_without_printing_secrets(
    acceptance_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    token = _token(3600)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JOBTOLOGY_DATABASE_URL", acceptance_database_url)
    monkeypatch.setenv("JOBTOLOGY_CREDENTIAL_ENCRYPTION_KEY", generate_encryption_key())
    monkeypatch.setattr(sys, "argv", ["credentials_cli", "import"])
    monkeypatch.setattr(
        sys, "stdin",
        io.StringIO(f'{{"tokens": {{"access_token": "{token}", "account_id": "a", "refresh_token": "r"}}}}'),
    )

    assert credentials_cli.main() == 0
    output = capsys.readouterr().out
    assert "stored" in output
    assert "access token expires in" in output
    assert token not in output
