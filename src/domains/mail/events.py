from __future__ import annotations

from dataclasses import dataclass

from packages.contracts.event_bus.bodies.base import EventBody
from packages.contracts.event_bus.registry import event
from packages.contracts.event_bus.subjects import EventSubject


@event(EventSubject.EMAIL_VERIFICATION_REQUESTED)
@dataclass(frozen=True)
class EmailVerificationRequestedBody(EventBody):
    email: str
    verification_url: str
    expires_in_seconds: int


@event(EventSubject.EMAIL_VERIFICATION_SENT)
@dataclass(frozen=True)
class EmailVerificationSentBody(EventBody):
    email: str
    mode: str


@event(EventSubject.EMAIL_VERIFICATION_FAILED)
@dataclass(frozen=True)
class EmailVerificationFailedBody(EventBody):
    email: str
    reason: str
    reason_code: str
    mode: str
