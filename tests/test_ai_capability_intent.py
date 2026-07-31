from domains.ai.context_facade import _is_capability_question


def test_capability_intent_accepts_natural_korean_word_order() -> None:
    assert _is_capability_question("너가 할 수 있는게 뭐야")
    assert _is_capability_question("Kyro가 지원하는 기능을 설명해 줘")
    assert _is_capability_question("AI가 어떤 일을 도와줄 수 있어?")


def test_capability_intent_does_not_capture_cluster_state_question() -> None:
    assert not _is_capability_question("현재 클러스터 상태가 뭐야?")
    assert not _is_capability_question("Kyro에서 지원 중인 서비스가 왜 장애야?")
    assert not _is_capability_question("AI가 분석 가능한 현재 장애가 뭐야?")
