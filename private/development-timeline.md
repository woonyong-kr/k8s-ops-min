[← Kyro로 돌아가기](../README.md)

# 커밋으로 복원한 판단 변화

| 시점 | 관찰한 문제 | 변경한 기준 | 코드·커밋 근거 | 결과와 한계 |
|---|---|---|---|---|
| 06-29 | 가짜 telemetry가 수집 실패를 정상 결과처럼 보이게 함 | fixture를 제품 경로에서 제거 | `chore: fake telemetry 제거` | 실패가 빈 결과로 드러남. 운영 실패율은 별도 계측하지 못함 |
| 07-06 | 디버깅용 raw payload가 소비 계약으로 유출됨 | 화면·원인 분석에 필요한 projection만 반환 | `fix: 원시 텔레메트리 / payload 제거` | 응답 노출 축소. 저장된 snapshot의 원문은 남음 |
| 07-11 04:28 | metadata provider마다 한도 로직을 복제 | 각 수집기의 중복 한도를 우선 적용 | [`a0f6d99f7`](https://github.com/minmings111/Kyro-jungle-final/commit/a0f6d99f7) | 중복 로직이 다음 수집기 확장의 비용임을 확인 |
| 07-11 05:27 | 같은 로직을 네 번째로 반복할 상황 | 공통 모듈을 먼저 테스트하고 원천별 adapter에 적용 | [`7cc428e4f`](https://github.com/minmings111/Kyro-jungle-final/commit/7cc428e4f74a6c8b8fc0759616dc5632f5abbf8f) | Kubernetes·Prometheus에 공통 한도 적용 |
| 07-11 05:39 | 기능 성공만 확인해 경계 계약이 약함 | 개수·byte 한도와 잘림 metadata를 테스트로 고정 | [`f533762a5`](https://github.com/minmings111/Kyro-jungle-final/commit/f533762a55f9b0e6e9c560cc13b057d6b0bb0d5e) | 반환 크기 제한. 운영 분포 기반 한도 조정은 남음 |
| 07-11 06:01 | Loki·Tempo가 다른 제한 규칙을 사용 | 같은 계약을 네 수집기에 적용 | [`d42d8c019`](https://github.com/minmings111/Kyro-jungle-final/commit/d42d8c01972cdc5501dae8a492701ce6471ae92f) | 93분·4커밋 동안 중복 구현을 공통 계약으로 전환 |
| 07-22 | 잘린 목록이 전체 목록으로 소비될 수 있음 | 수집 completeness와 deletion authority를 분리 | [`d29d3c429`](https://github.com/minmings111/Kyro-jungle-final/commit/d29d3c42963335756cf14212f05533e9ea54e57b) | 불완전한 결과를 삭제 판정에서 제외. 다른 소비자는 강제하지 못함 |
| 07-23 | manifest 전체 조회가 설정값을 과도하게 노출 | ConfigMap·Secret 참조 관계만 allowlist projection | [`05c60fdd9`](https://github.com/minmings111/Kyro-jungle-final/commit/05c60fdd9bfd4a6c42f59cbcb33b22d037dd5577) | FastAPI와 16개 경계 테스트 구현. pagination은 없음 |
| 07-23, 초기 기능 커밋 39분 뒤 | 참조 수·문자열·reason code의 상한이 빠짐 | 입력과 응답의 세 경계를 계약에 추가 | [`6c082d12a`](https://github.com/minmings111/Kyro-jungle-final/commit/6c082d12af40bc4c97bb08df503434b17d4fb860) | 과대 입력 방어. 실제 공격 부하 테스트는 미수행 |

## 현재 작업 기준

1. 빈 결과에는 실제 부재인지 조회 실패인지 표시합니다.
2. 응답을 자를 때 원래 범위와 잘림 사유를 함께 전달합니다.
3. 되돌리기 어려운 판단은 소비자의 주의가 아니라 코드로 차단합니다.
4. 원본 전체를 반환하지 않고 필요한 필드만 새 계약으로 투영합니다.
5. 기능 성공 뒤에 입력·응답·부분 실패 경계를 별도로 검증합니다.

커밋 수와 변경 줄 수는 성과로 사용하지 않습니다. 판단이 바뀐 지점과 해당 변경을 고정한 테스트만 근거로 사용합니다.
