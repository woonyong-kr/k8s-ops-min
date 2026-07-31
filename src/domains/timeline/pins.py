"""Authorization-safe projections of persistent Timeline pins."""

from __future__ import annotations

from domains.timeline.access import AuthorizedTimelineScope
from domains.timeline.predicate import TimelinePinMembership
from packages.contracts.timeline import (
    TimelinePin,
    TimelinePinnedApplicationSubject,
    TimelinePinnedResourceSubject,
    TimelinePinSet,
)


def visible_timeline_pin_set(
    pin_set: TimelinePinSet,
    authorized: AuthorizedTimelineScope,
) -> TimelinePinSet:
    """Hide revoked subjects without deleting or revealing their retained owner rows."""
    return TimelinePinSet(
        revision=pin_set.revision,
        pins=tuple(pin for pin in pin_set.pins if _pin_is_currently_readable(pin, authorized)),
    )


def timeline_pin_membership(pin_set: TimelinePinSet) -> TimelinePinMembership:
    """Project visible immutable subjects to exact event membership values."""
    resource_identities: set[tuple[str, str]] = set()
    application_ids: set[str] = set()
    for pin in pin_set.pins:
        if isinstance(pin.subject, TimelinePinnedResourceSubject):
            resource_identities.add((pin.subject.scope.cluster_id, pin.subject.resource.uid))
        elif isinstance(pin.subject, TimelinePinnedApplicationSubject):
            application_ids.add(pin.subject.application_id)
    return TimelinePinMembership(
        revision=pin_set.revision,
        resource_identities=frozenset(resource_identities),
        application_ids=frozenset(application_ids),
    )


def _pin_is_currently_readable(pin: TimelinePin, authorized: AuthorizedTimelineScope) -> bool:
    subject = pin.subject
    if isinstance(subject, TimelinePinnedResourceSubject):
        return (
            subject.scope.workspace_id == authorized.workspace_id
            and subject.scope.cluster_id in authorized.cluster_ids
        )
    if isinstance(subject, TimelinePinnedApplicationSubject):
        return subject.application_id in authorized.application_ids
    return False
