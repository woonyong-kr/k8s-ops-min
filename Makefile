SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

IMAGE_NAME ?= service:local
MGMT_CLUSTER ?=
TARGET_CLUSTER ?=
ENV_TEMPLATE ?= config/env/app.env.example
LOCAL_TEST_ENV ?= .env.local-test
FRONTEND_PORT ?= 5173
FRONTEND_HOST ?= 0.0.0.0
FRONTEND_BACKEND_ORIGIN ?= https://k8s.woonyong.org
REFERENCE_PROVENANCE ?= references/provenance/source.json
REFERENCE_REVISION ?= $(shell node scripts/reference-provenance.mjs revision)
REFERENCE_UI_BASE_REVISION ?= $(shell node scripts/reference-provenance.mjs ui-base-revision)
REFERENCE_UPSTREAM_GIT ?= /tmp/opsia-upstream-verify
REFERENCE_UPSTREAM_REPOSITORY ?= $(shell node scripts/reference-provenance.mjs repository)

export IMAGE_NAME
export MGMT_CLUSTER
export TARGET_CLUSTER

PY ?= python3

.PHONY: catalog-up catalog-down catalog-schema catalog-seed catalog-run catalog-verify catalog-sql catalog-mcp catalog-test help setup setup-hooks env local-test-env frontend-live local-up local-smoke sync hooks doctor lint format test manifest-check product-brand-boundary-check reference-ledger reference-ledger-check reference-feature-ledger reference-feature-ledger-check reference-upstream-prepare reference-ui-delta-ledger reference-ui-delta-ledger-check reference-ui-delta-rebaseline-check reference-feature-parity-check reference-feature-web-parity-check reference-feature-post-parity-check mirror-parity-check release-governance release-governance-web release-governance-web-patch gate gate-backend gate-contract-manifest gate-deploy-smoke-backend gate-deploy-smoke-frontend gate-frontend gate-frontend-changed gate-fast events event-bus-equivalence crash-test check build-image up install-telemetry down status smoke demo scale kill-pod external-instances external-kubeconfig cluster-interactions aws-up aws-down clean

help: ## 사용 가능한 명령어 출력
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make <target>\n\nTargets:\n"} /^[a-zA-Z0-9_-]+:.*##/ {printf "  %-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: env sync setup-hooks ## 최초 개발 환경 준비

env: ## .env 파일 생성
	@if [[ -f .env ]]; then \
		echo ".env already exists"; \
	else \
		cp "$(ENV_TEMPLATE)" .env; \
		echo "created .env"; \
	fi

local-test-env: ## 로컬 smoke/Bruno 테스트용 .env.local-test 생성
	@if [[ -f "$(LOCAL_TEST_ENV)" ]]; then \
		echo "$(LOCAL_TEST_ENV) already exists"; \
	else \
		cp config/env/local-test.env.example "$(LOCAL_TEST_ENV)"; \
		echo "created $(LOCAL_TEST_ENV)"; \
	fi

frontend-live: ## dev 프론트를 실제 라이브 백엔드에 연결해 실행
	HOST="$(FRONTEND_HOST)" PORT="$(FRONTEND_PORT)" BACKEND_ORIGIN="$(FRONTEND_BACKEND_ORIGIN)" bash scripts/dev-live-frontend.sh

sync: ## Python 의존성 설치/동기화
	uv sync

doctor: ## 로컬 필수 도구 점검
	bash scripts/doctor.sh

lint: ## Ruff 린트 검사
	uv run ruff check .

format: ## Ruff 포맷 적용
	uv run ruff format .

hooks: ## git 훅 설치(pre-commit 포맷 + pre-push 빠른 게이트)
	uv run pre-commit install --hook-type pre-commit --hook-type pre-push

setup-hooks: hooks ## 커밋 메시지·포맷·pre-push 게이트 훅 설치
	@hook_path="$$(git rev-parse --git-path hooks)/commit-msg"; \
	mkdir -p "$$(dirname "$$hook_path")"; \
	printf '%s\n' '#!/usr/bin/env sh' 'exec "$$(git rev-parse --show-toplevel)/scripts/commit-msg-gate.sh" "$$1"' > "$$hook_path"; \
	chmod +x "$$hook_path"; \
	echo "installed $$hook_path"

test: ## 린트와 테스트 실행
	bash scripts/test.sh

manifest-check: ## Kubernetes manifest 렌더/파싱 확인
	bash scripts/manifest-check.sh

product-brand-boundary-check: ## 제품 표면의 이전 제품명·조직명 경계 확인
	node scripts/verify-product-brand-boundary.mjs

reference-ledger: ## 고정 원본의 해시·이식 상태 ledger 생성
	node scripts/reference-ledger.mjs --source references/upstream --revision "$(REFERENCE_REVISION)" --output docs/migration/reference-source-ledger.json

reference-ledger-check: ## 고정 원본과 ledger의 완전성 확인
	node scripts/reference-ledger.mjs --source references/upstream --revision "$(REFERENCE_REVISION)" --output docs/migration/reference-source-ledger.json --check

reference-feature-ledger: ## 원본 기능·계약 전수 ledger 생성
	node scripts/reference-feature-ledger.mjs --source docs/spec/frontend/reference-feature-inventory.md --revision "$(REFERENCE_REVISION)" --output docs/migration/reference-feature-ledger.json --contracts-output src/packages/contracts/reference_feature_catalog.json --port-map docs/migration/reference-feature-port-map.json

reference-feature-ledger-check: ## 원본 기능 ledger의 완전성 확인
	node scripts/reference-feature-ledger.mjs --source docs/spec/frontend/reference-feature-inventory.md --revision "$(REFERENCE_REVISION)" --output docs/migration/reference-feature-ledger.json --contracts-output src/packages/contracts/reference_feature_catalog.json --port-map docs/migration/reference-feature-port-map.json --check
	node --test scripts/reference-feature-ledger.test.mjs scripts/reference-feature-source-identity.test.mjs scripts/reference-resource-metrics-parity.test.mjs scripts/release-governance.test.mjs

reference-upstream-prepare: ## strict UI delta 검증용 승인 원본 Git object 준비
	@if [[ -e "$(REFERENCE_UPSTREAM_GIT)" ]]; then \
		git -C "$(REFERENCE_UPSTREAM_GIT)" rev-parse --is-inside-work-tree >/dev/null; \
	else \
		git init --quiet "$(REFERENCE_UPSTREAM_GIT)"; \
	fi
	@remote="$$(git -C "$(REFERENCE_UPSTREAM_GIT)" remote get-url origin 2>/dev/null || true)"; \
	if [[ -z "$$remote" ]]; then \
		git -C "$(REFERENCE_UPSTREAM_GIT)" remote add origin "$(REFERENCE_UPSTREAM_REPOSITORY)"; \
	elif [[ "$$remote" != "$(REFERENCE_UPSTREAM_REPOSITORY)" ]]; then \
		echo "reference upstream remote differs: $$remote" >&2; exit 1; \
	fi
	git -C "$(REFERENCE_UPSTREAM_GIT)" fetch --no-tags --depth=1 origin "$(REFERENCE_UI_BASE_REVISION)" "$(REFERENCE_REVISION)"
	git -C "$(REFERENCE_UPSTREAM_GIT)" cat-file -e "$(REFERENCE_UI_BASE_REVISION)^{tree}"
	git -C "$(REFERENCE_UPSTREAM_GIT)" cat-file -e "$(REFERENCE_REVISION)^{tree}"

reference-ui-delta-ledger: ## 최신 원본 UI delta를 pending 상태로 결정적으로 생성
	node scripts/reference-source-delta-ledger.mjs --repository "$(REFERENCE_UPSTREAM_GIT)" --base "$(REFERENCE_UI_BASE_REVISION)" --target "$(REFERENCE_REVISION)" --inventory docs/spec/frontend/reference-feature-inventory.md --feature-ledger docs/migration/reference-feature-ledger.json --classification-input docs/migration/reference-ui-delta-classifications.json --output docs/migration/reference-ui-delta-ledger.json

reference-ui-delta-ledger-check: ## UI delta의 path·blob·SHA-256 결정성 확인(분류 완료는 요구하지 않음)
	node scripts/reference-source-delta-ledger.mjs --repository "$(REFERENCE_UPSTREAM_GIT)" --base "$(REFERENCE_UI_BASE_REVISION)" --target "$(REFERENCE_REVISION)" --inventory docs/spec/frontend/reference-feature-inventory.md --feature-ledger docs/migration/reference-feature-ledger.json --classification-input docs/migration/reference-ui-delta-classifications.json --output docs/migration/reference-ui-delta-ledger.json --check

reference-ui-delta-rebaseline-check: ## 출하/재기준화용: revision 일치와 UI delta 전수 분류를 모두 요구
	node scripts/reference-source-delta-ledger.mjs --repository "$(REFERENCE_UPSTREAM_GIT)" --base "$(REFERENCE_UI_BASE_REVISION)" --target "$(REFERENCE_REVISION)" --inventory docs/spec/frontend/reference-feature-inventory.md --feature-ledger docs/migration/reference-feature-ledger.json --classification-input docs/migration/reference-ui-delta-classifications.json --output docs/migration/reference-ui-delta-ledger.json --check --require-rebased --require-classified

reference-feature-parity-check: reference-ui-delta-rebaseline-check ## 출하용: UI delta와 모든 제품 기능이 실제 구현 상태인지 확인
	node scripts/reference-feature-ledger.mjs --source docs/spec/frontend/reference-feature-inventory.md --revision "$(REFERENCE_REVISION)" --output docs/migration/reference-feature-ledger.json --contracts-output src/packages/contracts/reference_feature_catalog.json --port-map docs/migration/reference-feature-port-map.json --check --require-complete

reference-feature-web-parity-check: reference-ui-delta-rebaseline-check ## 웹 출하용: desktop-only 기능과 OS 패키징을 제외한 Python/React 동등성 확인
	node scripts/reference-feature-ledger.mjs --source docs/spec/frontend/reference-feature-inventory.md --revision "$(REFERENCE_REVISION)" --output docs/migration/reference-feature-ledger.json --contracts-output src/packages/contracts/reference_feature_catalog.json --port-map docs/migration/reference-feature-port-map.json --check --require-complete --surface web --phase baseline

reference-feature-post-parity-check: reference-ui-delta-rebaseline-check ## 기준 동등성 이후 RCA·AI 확장 기능 완료 여부 추적
	node scripts/reference-feature-ledger.mjs --source docs/spec/frontend/reference-feature-inventory.md --revision "$(REFERENCE_REVISION)" --output docs/migration/reference-feature-ledger.json --contracts-output src/packages/contracts/reference_feature_catalog.json --port-map docs/migration/reference-feature-port-map.json --check --require-complete --surface web --phase post_parity

mirror-parity-check: ## 데모↔dev↔백엔드 계약 3자 미러 정합 검증
	node scripts/mirror-parity-check.mjs

release-governance: reference-ledger-check reference-ui-delta-rebaseline-check reference-feature-parity-check ## 출하 차단용 최신 원본 동등성 gate

release-governance-web: reference-ledger-check reference-ui-delta-rebaseline-check reference-feature-web-parity-check ## 웹 운영 배포용 최신 원본 동등성 gate

release-governance-web-patch: reference-ledger-check reference-ui-delta-rebaseline-check reference-feature-ledger-check ## Dev 증분 패치용 원본 무결성·계약 구조 gate

gate: ## PR 진단용 백엔드·manifest·프론트 전체 gate
	$(MAKE) gate-backend
	$(MAKE) gate-frontend

gate-backend: ## 백엔드·manifest 전체 gate
	bash scripts/test.sh
	bash scripts/manifest-check.sh

gate-contract-manifest: ## 교차 계약·manifest 최소 gate
	bash scripts/manifest-check.sh

gate-deploy-smoke-backend: ## 배포 스모크 셸 문법 gate
	bash -n scripts/pre-deploy-smoke.sh scripts/post-deploy-smoke.sh scripts/post-deploy-console-smoke.sh scripts/post_deploy_read_smoke.sh scripts/lib/public-edge.sh scripts/lib/cluster-curl.sh

gate-deploy-smoke-frontend: ## 브라우저 배포 스모크 정적 gate
	cd frontend && npm ci --include=dev --no-audit --no-fund
	cd frontend && npm run typecheck

gate-frontend: ## 프론트 정적 검사·빌드 전체 gate
	cd frontend && npm ci --include=dev --no-audit --no-fund
	cd frontend && npm run typecheck
	cd frontend && npm run build
	test -s frontend/dist/index.html
	ls frontend/dist/assets/*.js >/dev/null

gate-frontend-changed: ## 프론트 정적 검사·빌드 gate(GATE_BASE 필수)
	test -n "$(GATE_BASE)"
	git cat-file -e "$(GATE_BASE)^{commit}"
	cd frontend && npm ci --include=dev --no-audit --no-fund
	cd frontend && npm run typecheck
	cd frontend && npm run build
	cd frontend && npm run build
	test -s frontend/dist/index.html
	ls frontend/dist/assets/*.js >/dev/null

gate-fast: ## pre-push용 빠른 정적 검사(CI의 제품 테스트와 중복 실행하지 않음)
	uv run python -m compileall -q src scripts
	@set -e; changed_files="$$(bash scripts/changed-files.sh)"; \
	if grep -Eq '^frontend/' <<<"$$changed_files"; then \
		if [[ ! -d frontend/node_modules ]] || grep -Eq '^frontend/(package.json|package-lock.json)$$' <<<"$$changed_files"; then \
			(cd frontend && npm ci --include=dev --no-audit --no-fund); \
		fi; \
		(cd frontend && npm run typecheck); \
	else \
		echo "[gate-fast] frontend 변경 없음 — 프론트 검사 생략"; \
	fi

events: ## 등록된 이벤트/구독자 한눈에 보기
	uv run python scripts/events.py

event-bus-equivalence: ## in-process/NATS 전송 결과 동등성 실측
	bash scripts/test-event-bus-equivalence.sh

services: ## 서비스 명부 한눈에 보기(src/services 자동 발견)
	uv run python scripts/services.py

check: gate ## 개발자·CI·pre-push 공통 전체 점검

build-image: ## 로컬 container image 빌드(수동 디버그용)
	bash scripts/build-image.sh

up: ## legacy local management/target cluster 실행(기본 테스트 아님)
	MGMT_CLUSTER="$${LOCAL_MGMT_CLUSTER:-management}" TARGET_CLUSTER="$${LOCAL_TARGET_CLUSTER:-target}" bash scripts/up.sh

local-up: ## .env.local-test를 source해서 로컬 management/target cluster 실행
	@test -f "$(LOCAL_TEST_ENV)" || { echo "missing $(LOCAL_TEST_ENV); run make local-test-env"; exit 1; }
	set -a; source "$(LOCAL_TEST_ENV)"; set +a; bash scripts/up.sh

install-telemetry: ## target 클러스터에 telemetry Helm charts 설치
	bash scripts/install-telemetry.sh

down: ## legacy local management/target cluster 삭제
	MGMT_CLUSTER="$${LOCAL_MGMT_CLUSTER:-management}" TARGET_CLUSTER="$${LOCAL_TARGET_CLUSTER:-target}" bash scripts/down.sh

status: ## AWS management/target 리소스 상태 확인
	bash scripts/status.sh

smoke: ## 현재 환경변수로 배포된 서비스 smoke 실행
	bash scripts/smoke.sh

demo: ## Kind에서 bad rollout → Safe PR 리뷰 병합 → 외부 GitOps 정상화 데모
	bash -c "DEMO_DRY_RUN='$(DEMO_DRY_RUN)' bash scripts/oss-demo.sh"

local-smoke: ## .env.local-test를 source해서 로컬 smoke 실행
	@test -f "$(LOCAL_TEST_ENV)" || { echo "missing $(LOCAL_TEST_ENV); run make local-test-env"; exit 1; }
	set -a; source "$(LOCAL_TEST_ENV)"; set +a; bash scripts/smoke.sh

crash-test: ## AWS management에서 아웃박스 정확히 한 번 크래시 테스트
	bash scripts/crash_test.sh

scale: ## management worker 스케일 조정. 예: make scale DEPLOYMENT=rca-worker REPLICAS=2
	@test -n "$(DEPLOYMENT)" && test -n "$(REPLICAS)"
	bash scripts/scale.sh "$(DEPLOYMENT)" "$(REPLICAS)"

kill-pod: ## management pod 삭제 후 복구 확인. 예: make kill-pod DEPLOYMENT=rca-worker
	@test -n "$(DEPLOYMENT)"
	bash scripts/kill-pod.sh "$(DEPLOYMENT)"

external-instances: ## 외부 콘솔 인스턴스별 Console/CD 상태 확인
	bash scripts/external-console-instances.sh

external-kubeconfig: ## 외부 콘솔 클러스터 kubeconfig 동기화/검증
	bash scripts/external-console-kubeconfig.sh

cluster-interactions: ## 두 클러스터 read-only 상태/서비스/Helm/event 확인
	bash scripts/cluster-interactions.sh

aws-up: ## AWS EKS management + target 2개 테스트 환경 생성
	bash scripts/aws-up.sh

aws-down: ## AWS EKS 테스트 환경 삭제
	bash scripts/aws-down.sh

clean: ## 재생성 가능한 캐시와 빌드 산출물 삭제
	# 안전 경계: .env*, outputs/, node_modules/, .venv/, tfstate, .git/은 절대 삭제하지 않는다.
	rm -rf -- .pytest_cache .ruff_cache .import_linter_cache .playwright-cli
	rm -rf -- frontend/.playwright-cli references/ui-layer-lab/.playwright-cli
	rm -rf -- frontend/dist references/ui-layer-lab/dist
	find alembic src tests scripts -type d -name __pycache__ -prune -exec rm -rf -- {} +

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

catalog-verify: ## 멱등성·부분 실패·드리프트·리니지·오탐 검증
	PYTHONPATH=src $(PY) scripts/catalog_verify.py

catalog-sql: ## 정합성 검사 SQL 실행 결과 출력
	PYTHONPATH=src $(PY) scripts/catalog_sql.py

catalog-mcp: ## MCP 도구 목록과 인자 스키마 출력
	PYTHONPATH=src $(PY) -m services.catalog_mcp.server

catalog-test: ## 카탈로그 계층 테스트
	PYTHONPATH=src $(PY) -m pytest tests/catalog -q
