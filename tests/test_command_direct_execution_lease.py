from domains.command.models import AgentCommand
from domains.command.repository import leased_agent_command_columns


def test_agent_lease_includes_server_authorized_direct_execution_flag() -> None:
    columns = leased_agent_command_columns(AgentCommand.__table__)

    assert "direct_execution" in {column.key for column in columns}
