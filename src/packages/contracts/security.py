from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SecretRef:
    """Raw secret 대신 경계를 지나는 참조값."""

    value: str


class SecretVaultPort(Protocol):
    def read_secret(self, ref: SecretRef) -> str: ...


class TokenVaultPort(Protocol):
    def read_token(self, ref: SecretRef) -> str: ...
