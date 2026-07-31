from __future__ import annotations

import asyncio
from types import SimpleNamespace

from domains.gitops.repository_discovery import GitHubRepositoryClient, RepositoryDiscoveryService
from domains.gitops.repository_discovery_router import wizard_discovery_service
from domains.scm import github_app_credentials


class RepositoryDb:
    def get_repository_by_ref(self, workspace_id: str, repo_ref: str) -> dict[str, str]:
        assert workspace_id == "workspace-a"
        assert repo_ref == "owner/repository"
        return {
            "repository_id": "repository-a",
            "credential_ref": "github-app-installation:12345",
        }

    def get_workspace_credential(
        self,
        workspace_id: str,
        provider: str,
        scope: str,
    ) -> None:
        raise AssertionError("GitHub App references must resolve before PAT credential fallback")


def test_wizard_discovery_resolves_stored_github_app_reference(monkeypatch) -> None:
    async def resolve_token(
        db: object,
        workspace_id: str,
        installation_id: str,
    ) -> str:
        assert isinstance(db, RepositoryDb)
        assert workspace_id == "workspace-a"
        assert installation_id == "12345"
        return "installation-token"

    monkeypatch.setattr(github_app_credentials, "resolve_installation_token", resolve_token)
    fallback = RepositoryDiscoveryService(GitHubRepositoryClient(token=None))

    service = asyncio.run(
        wizard_discovery_service(
            RepositoryDb(),
            SimpleNamespace(workspace_id="workspace-a"),
            "owner/repository",
            fallback,
        )
    )

    assert service.client.token == "installation-token"
