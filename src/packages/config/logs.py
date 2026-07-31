"""구조적(JSON) 로깅.

print 대신 한 줄=JSON 1개. 로그 수집기(Loki/ES/CloudWatch)에서
correlation_id 같은 필드로 질의·필터·정렬 가능. 전 서비스 동일 형식 →
pod 경계 넘는 흐름 추적.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

CONTEXT_KEY = "context"


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname.lower(),
            "service": self.service,
            "action": record.getMessage(),
        }
        context = getattr(record, CONTEXT_KEY, None)
        if isinstance(context, dict):
            data.update(context)
        if record.exc_info:
            data["error"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False)


def configure_logging(service: str, level: int = logging.INFO) -> None:
    """프로세스 시작 시 한 번 호출. 루트 로거를 JSON 출력으로 교체."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
