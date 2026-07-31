"""LLM 노출 텍스트 카탈로그 — 로케일별 번역의 단일 출처.

사용자에게 노출되는 문자열(프롬프트/응답 대체 텍스트/실패 사유)은 코드에 직접 쓰지 않고
여기 키로 등록. 새 언어 추가 = 각 키에 로케일 항목 1줄(로직 수정 없음).
미등록 키는 즉시 예외(fail-fast), 미등록 로케일은 기본 로케일로 폴백.
"""

from __future__ import annotations

DEFAULT_LOCALE = "en"

_CATALOG: dict[str, dict[str, str]] = {
    "chat.system_prompt": {
        "en": (
            "You are an operations assistant for a Kubernetes event-driven platform.\n"
            "Answer with concise operational reasoning and concrete next checks."
        ),
        "ko": (
            "당신은 Kubernetes 이벤트 기반 플랫폼의 운영 어시스턴트입니다.\n"
            "간결한 운영 관점 추론과 구체적인 다음 점검 항목으로 답하세요."
        ),
    },
    "chat.empty_response": {
        "en": "No response generated.",
        "ko": "생성된 응답이 없습니다.",
    },
    "chat.failure.rate_limited.reason": {
        "en": "AI provider rate limit reached",
        "ko": "AI 공급자 요청 한도에 도달했습니다",
    },
    "chat.failure.rate_limited.fallback": {
        "en": (
            "The AI provider is temporarily rate-limited, so no diagnosis was generated. "
            "Your request is preserved. Review it and retry in a moment."
        ),
        "ko": (
            "AI 공급자의 요청 한도에 일시적으로 도달해 진단을 생성하지 못했습니다. "
            "질문은 보존했습니다. 내용을 확인한 뒤 잠시 후 다시 시도하세요."
        ),
    },
    "chat.failure.unavailable.reason": {
        "en": "AI provider is unavailable",
        "ko": "AI 공급자를 사용할 수 없습니다",
    },
    "chat.failure.unavailable.fallback": {
        "en": (
            "The AI provider is unavailable, so no diagnosis was generated. "
            "Your request is preserved. Review it before retrying."
        ),
        "ko": (
            "AI 공급자를 사용할 수 없어 진단을 생성하지 못했습니다. "
            "질문은 보존했습니다. 다시 시도하기 전에 내용을 확인하세요."
        ),
    },
}


def text(key: str, locale: str | None = None, **kwargs: object) -> str:
    """카탈로그 텍스트 조회. 키 미등록은 예외, 로케일 미등록은 기본 로케일 폴백."""
    try:
        by_locale = _CATALOG[key]
    except KeyError as exc:
        raise KeyError(f"unknown message key: {key}") from exc
    template = by_locale.get(locale or DEFAULT_LOCALE) or by_locale[DEFAULT_LOCALE]
    return template.format(**kwargs) if kwargs else template


def registered_message_keys() -> tuple[str, ...]:
    """조회 관례(registered_*) — 등록된 메시지 키 전체."""
    return tuple(sorted(_CATALOG))
