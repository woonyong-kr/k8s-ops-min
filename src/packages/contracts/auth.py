from __future__ import annotations

from dataclasses import dataclass

from packages.contracts.identity import ServiceRole


@dataclass(frozen=True)
class Actor:
    """요청 주체. 로그인 구현과 독립적인 내부 표현."""

    user_id: str
    roles: tuple[str, ...] = (ServiceRole.SERVICE_ADMIN.value,)

    def to_body(self) -> dict[str, object]:
        return {"user_id": self.user_id, "roles": list(self.roles)}
