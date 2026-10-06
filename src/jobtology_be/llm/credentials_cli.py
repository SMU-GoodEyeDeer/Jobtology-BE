"""Manage the server's ChatGPT OAuth credentials.

    python -m jobtology_be.llm.credentials_cli generate-key
    python -m jobtology_be.llm.credentials_cli import < auth.json
    python -m jobtology_be.llm.credentials_cli status

`import` and `status` read JOBTOLOGY_DATABASE_URL and JOBTOLOGY_CREDENTIAL_ENCRYPTION_KEY.
Secrets are never printed.
"""

import base64
import json
import sys
import time

import anyio
from pydantic import ValidationError

from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.llm.credentials import (
    CredentialStoreError,
    DatabaseCredentialStore,
    OAuthCredentials,
    generate_encryption_key,
    parse_encryption_key,
)
from jobtology_be.settings import Settings


def _expiry_minutes(access_token: str) -> int | None:
    try:
        payload = access_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return round((float(claims["exp"]) - time.time()) / 60)
    except (IndexError, ValueError, KeyError):
        return None


async def _run(command: str) -> int:
    settings = Settings()
    if settings.database_url is None or settings.credential_encryption_key is None:
        print("JOBTOLOGY_DATABASE_URL and JOBTOLOGY_CREDENTIAL_ENCRYPTION_KEY are required", file=sys.stderr)
        return 2
    database = Database.create(settings.database_url)
    store = DatabaseCredentialStore(
        database, parse_encryption_key(settings.credential_encryption_key.get_secret_value())
    )
    try:
        if command == "import":
            try:
                credentials = OAuthCredentials.model_validate_json(sys.stdin.read())
            except ValidationError:
                print("stdin is not a Codex auth.json with tokens.access_token and tokens.account_id", file=sys.stderr)
                return 2
            await store.save(credentials)
            print("stored")
        loaded = await store.load()
        if loaded is None:
            print("status: not configured")
            return 1
        minutes = _expiry_minutes(loaded.tokens.access_token)
        print(
            "status: configured;"
            f" access token expires in {minutes} min;"
            f" refresh token {'present' if loaded.tokens.refresh_token else 'missing'}"
        )
        return 0
    except CredentialStoreError as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        await database.dispose()


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "generate-key":
        print(generate_encryption_key())
        return 0
    if command not in {"import", "status"}:
        print(__doc__, file=sys.stderr)
        return 2
    return anyio.run(_run, command)


if __name__ == "__main__":
    raise SystemExit(main())
