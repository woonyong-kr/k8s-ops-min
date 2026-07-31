from __future__ import annotations

import asyncio
import json

import pytest

from domains.identity.dependencies import ClusterAgentIdentity
from domains.integrations import prometheus


class PrometheusEnvelopeDb:
    def get_workspace_credential(
        self,
        workspace_id: str,
        provider: str,
        scope: str,
    ) -> dict[str, object]:
        return {
            "workspace_id": workspace_id,
            "provider": provider,
            "scope": scope,
            "status": "active",
            "encrypted_value": "encrypted",
            "metadata": {
                "revision": "revision-1",
                "operation_id": "operation-1",
                "address": "http://prometheus.target.svc:9090",
            },
        }

    def get_cluster_registration_install_credentials(
        self,
        workspace_id: str,
        cluster_id: str,
    ) -> dict[str, str]:
        assert workspace_id == "workspace-1"
        assert cluster_id == "cluster-1"
        return {"agent_envelope_public_key": "public-key"}

    def get_cluster_registration(self, *_args: object) -> None:
        raise AssertionError("the redacted general registration reader must not be used")


def test_agent_prometheus_envelope_uses_private_registration_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        prometheus,
        "decrypt_credential",
        lambda _value: json.dumps({"headers": {}}),
    )
    monkeypatch.setattr(
        prometheus,
        "seal_agent_payload",
        lambda payload, public_key, _context: (
            "sealed" if payload == {"headers": {}} and public_key == "public-key" else ""
        ),
    )

    envelope = asyncio.run(
        prometheus.agent_prometheus_integration(
            revision="revision-1",
            identity=ClusterAgentIdentity(
                workspace_id="workspace-1",
                cluster_id="cluster-1",
            ),
            db=PrometheusEnvelopeDb(),
        )
    )

    assert envelope.cluster_id == "cluster-1"
    assert envelope.sealed_headers == "sealed"
