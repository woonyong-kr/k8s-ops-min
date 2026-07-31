from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from domains.datacatalog.pipeline import load_archived_outcomes


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def mappings(self) -> list[dict[str, Any]]:
        return self.rows


class _Connection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.params: dict[str, str] | None = None

    def execute(self, _statement: object, params: dict[str, str]) -> _Rows:
        self.params = params
        return _Rows(self.rows)


def test_downstream_loads_the_archived_snapshot_without_source_read(tmp_path: Path) -> None:
    archive = tmp_path / "loki.json"
    payload = [{"asset_id": "logs", "row_key": "1", "payload": {"severity": "ERROR"}}]
    archive.write_text(json.dumps(payload), encoding="utf-8")
    conn = _Connection(
        [
            {
                "source_id": "loki",
                "status": "SUCCESS",
                "s3_uri": f"file://{archive}",
                "content_hash": "abc",
            },
            {
                "source_id": "tempo",
                "status": "FAILED",
                "s3_uri": None,
                "content_hash": None,
            },
        ]
    )

    outcomes = load_archived_outcomes(conn, "catalog__2026-07-31")

    assert conn.params == {"dag_run_id": "catalog__2026-07-31"}
    assert outcomes[0].payloads == payload
    assert outcomes[0].s3_uri == f"file://{archive}"
    assert outcomes[1].status == "FAILED"
    assert outcomes[1].payloads == []


def test_downstream_rejects_an_archive_backend_it_cannot_read() -> None:
    conn = _Connection(
        [
            {
                "source_id": "loki",
                "status": "SUCCESS",
                "s3_uri": "s3://bucket/loki.json",
                "content_hash": "abc",
            }
        ]
    )

    with pytest.raises(ValueError, match="unsupported archive URI"):
        load_archived_outcomes(conn, "catalog__2026-07-31")
