from __future__ import annotations

import pytest
from fastapi import HTTPException

from domains.manifest_editor.router import ensure_source_is_current
from domains.manifest_editor.source_revision import SourceRevision, SourceRevisionCodec
from domains.manifest_editor.validation import manifest_sha256


def revision(**overrides: str) -> SourceRevision:
    values = {
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "resource_id": "resource-a",
        "application_id": "application-a",
        "repository_ref": "owner/repository",
        "branch": "main",
        "binding_manifest_path": "deploy/overlays/production",
        "resolved_manifest_path": "deploy/base/api-server.yaml",
        "base_sha": "a" * 40,
        "source_sha256": f"sha256:{'b' * 64}",
    }
    values.update(overrides)
    return SourceRevision(**values)


def test_source_revision_round_trip_is_scope_bound() -> None:
    codec = SourceRevisionCodec("s" * 32, now=lambda: 100)
    expected = revision()

    token = codec.encode(expected)

    assert codec.decode(token, expected=expected) == expected
    with pytest.raises(ValueError, match="scope changed"):
        codec.decode(token, expected=revision(resource_id="resource-b"))


def test_source_revision_rejects_tampering_and_expiry() -> None:
    encoder = SourceRevisionCodec("s" * 32, ttl_seconds=10, now=lambda: 100)
    token = encoder.encode(revision())

    with pytest.raises(ValueError, match="invalid"):
        encoder.inspect(f"{token[:-1]}x")

    expired = SourceRevisionCodec("s" * 32, ttl_seconds=10, now=lambda: 111)
    with pytest.raises(ValueError, match="expired"):
        expired.inspect(token)


def test_manifest_edit_allows_unrelated_branch_head_change() -> None:
    content = "apiVersion: v1\nkind: Service\nmetadata:\n  name: api\n"

    ensure_source_is_current(
        "a" * 40,
        manifest_sha256(content),
        "b" * 40,
        content,
    )


def test_manifest_edit_rejects_change_to_the_resolved_file() -> None:
    original = "apiVersion: v1\nkind: Service\nmetadata:\n  name: api\n"
    changed = "apiVersion: v1\nkind: Service\nmetadata:\n  name: api-v2\n"

    with pytest.raises(HTTPException) as caught:
        ensure_source_is_current(
            "a" * 40,
            manifest_sha256(original),
            "b" * 40,
            changed,
        )

    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "manifest_source_stale"
