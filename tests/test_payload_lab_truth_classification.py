from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.payload_lab.runner import source_truth, transformation_truth_failed  # noqa: E402


def test_missing_source_is_not_mislabeled_as_transform_loss() -> None:
    source = {"failed_scheduling": False, "oom_signal": False}
    normalized = {"failed_scheduling": False, "oom_signal": False}

    assert transformation_truth_failed(source, normalized) is False


def test_present_source_lost_during_transform_is_a_transform_loss() -> None:
    source = {"failed_scheduling": True, "oom_signal": True}
    normalized = {"failed_scheduling": False, "oom_signal": False}

    assert transformation_truth_failed(source, normalized) is True


def test_present_source_retained_by_transform_is_not_a_failure() -> None:
    source = {"failed_scheduling": True, "oom_signal": True}
    normalized = {"failed_scheduling": True, "oom_signal": True}

    assert transformation_truth_failed(source, normalized) is False


def test_prometheus_string_sample_is_detected_at_source() -> None:
    raw = {"data": {"result": [{"metric": {"job": "payload"}, "value": [1_700_000_000, "99"]}]}}

    assert source_truth("prometheus", raw)["metric_99"] is True


def test_loki_raw_phrase_maps_to_normalized_reason_code() -> None:
    raw = {
        "data": {
            "result": [{
                "stream": {"namespace": "payload-bench"},
                "values": [["1", "level=ERROR dependency timeout trace_id=4bf92f3577b34da6a3ce929d0e0e4736 token=super-secret"]],
            }]
        }
    }

    assert all(source_truth("loki", raw).values())
