SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

# `uv sync --all-groups`가 만든 프로젝트 환경을 모든 실행 타깃의 단일 기준으로 사용한다.
# 임의의 파이썬으로 검증하려면 `make test PY=/path/to/python`처럼 덩어쓴다.
PY ?= .venv/bin/python

.PHONY: help sync lint format test \
        catalog-up catalog-down catalog-schema catalog-seed catalog-run \
        catalog-verify catalog-sql catalog-mcp catalog-test \
        controller-check benchmark-event-bus benchmark-event-bus-compare \
        evidence-screenshots portfolio-verify clean

help: ## 사용 가능한 명령어 출력
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make <target>\n\nTargets:\n"} /^[a-zA-Z0-9_-]+:.*##/ {printf "  %-16s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

sync: ## Python 의존성 설치/동기화
	uv sync --all-groups

lint: ## Ruff 린트 검사
	$(PY) -m ruff check .

format: ## Ruff 포맷 적용
	$(PY) -m ruff format .

test: ## 담당 범위 테스트 실행
	PYTHONPATH=src $(PY) -m pytest tests -q

# 데이터 파운데이션 계층 -----------------------------------------------------

catalog-up: ## 카탈로그 로컬 스택 기동 (PostgreSQL + MinIO + Airflow)
	docker compose -f docker-compose.catalog.yml up -d
	@echo "Airflow UI: http://localhost:8080  MinIO: http://localhost:9001"

catalog-down: ## 카탈로그 로컬 스택 종료
	docker compose -f docker-compose.catalog.yml down -v

catalog-schema: ## 카탈로그 테이블 생성
	PYTHONPATH=src $(PY) scripts/catalog_schema.py

catalog-seed: ## 검증용 fixture 적재
	PYTHONPATH=src $(PY) scripts/catalog_run.py --logical-date $(shell date -u +%F) --fixture normal

catalog-run: ## 배치 1회 실행. DATE=YYYY-MM-DD 로 날짜 지정 가능
	PYTHONPATH=src $(PY) scripts/catalog_run.py --logical-date $(or $(DATE),$(shell date -u +%F))

catalog-verify: ## 멱등성·부분 실패·드리프트·리니지·오탐 검증 15항목
	PYTHONPATH=src $(PY) scripts/catalog_verify.py

catalog-sql: ## 정합성 검사 SQL 실행 결과 출력
	PYTHONPATH=src $(PY) scripts/catalog_sql.py

catalog-mcp: ## MCP 도구 목록과 인자 스키마 출력
	PYTHONPATH=src $(PY) -m services.catalog_mcp.server

catalog-test: ## 카탈로그 계층 테스트
	PYTHONPATH=src $(PY) -m pytest tests/catalog -q

# 실행 토폴로지 검증 --------------------------------------------------------

controller-check: ## 통합 controller가 조립하는 서비스 수와 이벤트 버스 모드 확인
	PYTHONPATH=src $(PY) src/entrypoints/app.py --check

benchmark-event-bus: ## 프로세스 내부 이벤트 전달 벤치마크
	PYTHONPATH=src $(PY) benchmarks/event_bus_transport.py --mode inprocess

benchmark-event-bus-compare: ## 내부 전달과 NATS 비교 (NATS_URL 필요)
	@test -n "$${NATS_URL:-}" || { echo "NATS_URL을 지정하세요"; exit 2; }
	PYTHONPATH=src $(PY) benchmarks/event_bus_transport.py --mode both

evidence-screenshots: ## AWS·Git·벤치마크 증거판 SVG 생성
	$(PY) docs/evidence/network-cost/render_evidence.py

portfolio-verify: ## 공개 문서 링크·수치·식별자·기여 경계 검사
	$(PY) scripts/portfolio_verify.py

clean: ## 캐시 삭제
	find src tests scripts dags benchmarks -type d -name __pycache__ -prune -exec rm -rf -- {} +
