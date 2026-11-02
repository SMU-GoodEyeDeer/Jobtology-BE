"""High entropy invitation codes; raw values are only disclosed at creation."""

from base64 import b32encode
from hashlib import sha256
from re import fullmatch
from secrets import token_bytes


def normalize_invitation(value: str) -> str:
    normalized = value.strip().replace("-", "").replace(" ", "").upper()
    if fullmatch(r"[A-Z2-7]{24}", normalized) is None:
        raise ValueError("Invalid invitation code")
    return normalized


def invitation_digest(value: str) -> bytes:
    return sha256(normalize_invitation(value).encode("ascii")).digest()


def new_invitation_code() -> str:
    raw = b32encode(token_bytes(15)).decode("ascii")
    return "-".join(raw[i : i + 4] for i in range(0, len(raw), 4))
