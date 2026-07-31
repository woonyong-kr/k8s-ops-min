[← Kyro로 돌아가기](../../README.md) · [← 06](06-sql-quality-checks.md)

# 07. 카탈로그 조회 API와 MCP의 현재 상태

> **⑥ 자료 목록 관리** · 프로젝트 종료 후 검증 · 개인 성과와 분리

## 결론

카탈로그 조회 router와 MCP 도구 계약 코드는 있습니다. 그러나 router는 실행 앱에 연결되지 않았고, MCP는 실제 HTTP 호출이나 MCP transport를 구현하지 않았습니다. 현재 단계는 **API 함수와 도구 경계의 초안**이며 “카탈로그 API/MCP를 완성했다”고 말할 수 없습니다.

## 구현된 API 함수

→ [`src/domains/datacatalog/router.py`](../../src/domains/datacatalog/router.py)

```text
GET  /v1/catalog/sources
GET  /v1/catalog/assets
GET  /v1/catalog/assets/{id}
GET  /v1/catalog/assets/{id}/schema
GET  /v1/catalog/assets/{id}/lineage
GET  /v1/catalog/quality/issues
GET  /v1/catalog/runs
```

각 함수는 SQLAlchemy `Connection`을 받아 PostgreSQL을 조회하고, 다음 envelope을 반환하도록 작성돼 있습니다.

```json
{
  "data": [],
  "page": {
    "limit": 50,
    "returned_count": 0,
    "total_estimated": 0,
    "truncated": false
  },
  "evidence": {
    "run_id": null,
    "logical_date": null,
    "run_status": "NEVER_RUN",
    "checked_at": null,
    "reason_codes": [
      {"code": "NEVER_RUN"}
    ],
    "reason_codes_truncated": false
  }
}
```

`evidence`를 붙인 이유는 “품질 이슈 0건”과 “검사가 아직 한 번도 돌지 않음”을 구분하기 위해서입니다. 최근 DAG가 `PARTIAL`이면 조회 결과도 일부 source를 보지 못한 결과임을 함께 전달합니다.

목록 API에는 limit과 cursor가 있고, 상한 너머 결과에 도달할 수 있습니다. cursor는 현재 offset을 base64로 감싼 형태라 정렬 기준이 실행 중 바뀌면 중복·누락이 생길 수 있습니다. 안정적인 keyset pagination은 아닙니다.

## 아직 API가 아닌 이유

router의 `get_connection()`은 다음 상태입니다.

```python
def get_connection() -> Connection:
    raise NotImplementedError("애플리케이션 배선에서 오버라이드한다")
```

기존 gateway는 `domains.catalog.router`를 연결하지만 `domains.datacatalog.router`는 연결하지 않습니다. 별도 FastAPI app도 없습니다.

따라서 현재 코드로 HTTP 요청을 보내면 이 endpoint들이 존재하지 않습니다. unit test도 router의 응답·오류·pagination을 실행하지 않습니다.

완료하려면 다음이 필요합니다.

1. 별도 catalog API app을 만들거나 기존 gateway에 router를 등록합니다.
2. DB connection dependency를 실제 engine에 연결합니다.
3. 인증·인가 경계를 정합니다.
4. TestClient와 임시 PostgreSQL로 200·404·422·부분 실행 응답을 검증합니다.
5. schema migration에 catalog table을 포함합니다.

## 구현된 MCP 경계

→ [`src/services/catalog_mcp/server.py`](../../src/services/catalog_mcp/server.py)

현재 등록된 도구 계약은 6개입니다.

| 도구 | 의도한 API | 질문 |
|---|---|---|
| `list_data_sources` | `/sources` | 어떤 source가 등록됐나 |
| `search_assets` | `/assets` | 이름으로 자산 찾기 |
| `get_asset_schema` | `/assets/{id}/schema` | schema 변경 이력 |
| `get_asset_lineage` | `/assets/{id}/lineage` | upstream 경로 |
| `list_quality_issues` | `/quality/issues` | 현재 품질 이슈 |
| `get_run_status` | `/runs` | 배치 실행 상태 |

구현된 경계는 다음과 같습니다.

- 정의하지 않은 tool name 거부
- `additionalProperties=false`에 해당하는 알 수 없는 인자 거부
- enum·문자열 길이·정수 범위 검증
- 읽기 도구만 등록
- 최대 50개·64KB로 응답 제한
- 잘림 여부와 원래 개수 보존
- 원천 통제 문자열을 `untrusted` 블록으로 분리

→ [`tests/catalog/test_mcp_boundary.py`](../../tests/catalog/test_mcp_boundary.py)

9개 테스트가 위 경계를 검증합니다.

## 아직 MCP 서버가 아닌 이유

현재 모듈의 `main()`은 tool 목록 JSON을 표준 출력에 인쇄할 뿐입니다.

다음은 구현돼 있지 않습니다.

- MCP protocol transport
- tool call을 받는 request loop
- FastAPI catalog endpoint를 호출하는 HTTP client
- 사용자 token 전달 또는 token exchange
- 인증·인가 실패 처리
- session call budget
- audit log
- 실제 API 응답을 `bound_response()`로 연결하는 dispatch

`SESSION_CALL_BUDGET = 200` 상수는 있지만 이를 감소시키거나 초과를 거부하는 코드가 없습니다. API path가 tool 계약에 적혀 있어도 그 path를 호출하는 코드는 없습니다.

따라서 지금 말할 수 있는 것은 “MCP 도구의 입력·출력 경계를 설계하고 단위 테스트했다”까지입니다. “MCP 서버를 구현해 AI가 카탈로그를 조회했다”는 말은 사실이 아닙니다.

## 팀 프로젝트 MCP와 구분

원본 팀 프로젝트의 `src/services/mcp/internal_control/`에는 server, API client, gateway·AI runtime 연결 이력과 직접 구현 근거가 있다. 다만 최종 Golden Path와 사용자 검증에서는 제외됐다.

후속 catalog MCP는 그 경험을 더 작은 읽기 전용 범위로 다시 설계한 초안입니다. 둘을 합쳐 “카탈로그 MCP를 완성했다”고 만들지 않습니다.

## 완료 조건

1. catalog FastAPI app을 실제로 실행합니다.
2. MCP SDK transport와 tool dispatch를 구현합니다.
3. DB가 아니라 API만 호출하도록 합니다.
4. 호출자의 인증 정보를 API까지 전달하고 401·403을 검증합니다.
5. 큰 응답, pagination, 부분 실행 evidence를 end-to-end로 검증합니다.
6. tool routing 평가 질문을 만들고 예상 tool·arguments와 대조합니다.
7. 개인 성과로 사용하려면 직접 수정·실행한 변경 이력을 남긴다.

완료 후에 사용할 수 있는 문장은 다음입니다.

> 카탈로그의 자산·schema·lineage·품질 조회 API를 FastAPI로 제공하고, 동일한 권한 경계를 통과하는 읽기 전용 MCP 도구 6개를 연결했습니다. 알 수 없는 인자와 쓰기 도구를 서버에서 차단하고, 부분 실행과 응답 잘림 상태를 AI에 함께 전달했습니다.

현재는 이 문장을 사용하지 않습니다.

---

[다음: 기술 리서치 →](08-tech-research.md)
