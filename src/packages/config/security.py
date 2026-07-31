"""위험한 테스트 기능의 명시적 capability 설정."""

from __future__ import annotations

from packages.config.settings import env

RCA_TEST_RUNS_ENABLED_ENV = "RCA_TEST_RUNS_ENABLED"
RCA_TEST_RUNS_DISABLED_MESSAGE = "RCA test commands require RCA_TEST_RUNS_ENABLED=1"
RCA_TEST_TARGET_ENVIRONMENTS = frozenset({"test", "aws-test"})
TEST_FIXTURE_PURGE_ENABLED_ENV = "TEST_FIXTURE_PURGE_ENABLED"
TEST_FIXTURE_ENVIRONMENT = "test"
TRUE_ENV_VALUES = frozenset({"1", "true", "yes", "on"})


def env_enabled(name: str) -> bool:
    return env(name, "").strip().lower() in TRUE_ENV_VALUES


def rca_test_runs_enabled() -> bool:
    """장애 주입 명령의 관리 plane·target agent 공통 fail-closed 조건."""
    return env_enabled(RCA_TEST_RUNS_ENABLED_ENV)


def test_fixture_purge_enabled() -> bool:
    """물리 삭제가 필요한 테스트 fixture 정리 capability."""
    return env_enabled(TEST_FIXTURE_PURGE_ENABLED_ENV)
