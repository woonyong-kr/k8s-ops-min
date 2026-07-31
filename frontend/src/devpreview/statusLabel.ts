// UI-PHASE2-001 P1(9): 관측된 영문 상태/이벤트 토큰을 사용자 친화 한글로 표기한다.
// 매핑에 없는 값은 원문을 그대로 반환한다(지어내지 않음 — honest).

const STATUS_KO: Record<string, string> = {
  // 연결/헬스
  healthy: "정상",
  ok: "정상",
  ready: "준비됨",
  online: "연결됨",
  connected: "연결됨",
  agent_connected: "에이전트 연결됨",
  snapshot_received: "인벤토리 수신됨",
  awaiting_install: "설치 대기",
  expired: "설치 만료",
  install_expired: "설치 만료",
  degraded: "저하",
  warning: "주의",
  warn: "주의",
  pending: "대기",
  waiting_for_approval: "승인 대기",
  provisioning: "예약 중",
  progressing: "진행 중",
  critical: "위험",
  error: "오류",
  failed: "실패",
  failure: "실패",
  failedscheduling: "스케줄링 실패",
  unhealthy: "비정상",
  notready: "준비 안 됨",
  "not-ready": "준비 안 됨",
  offline: "연결 끊김",
  disconnected: "연결 끊김",
  unavailable: "관측 안 됨",
  available: "사용 가능",
  partial: "부분 관측",
  stale: "지연됨",
  live: "실시간",
  cordoned: "차단됨",
  unknown: "관측 안 됨",
  // 파드 phase
  running: "실행 중",
  succeeded: "완료",
  completed: "완료",
  terminating: "종료 중",
  crashloopbackoff: "재시작 반복",
  imagepullbackoff: "이미지 수신 실패",
  errimagepull: "이미지 수신 실패",
  containercreating: "컨테이너 생성 중",
  containersnotready: "컨테이너 준비 안 됨",
  containersready: "컨테이너 준비",
  // 이벤트/인시던트
  incident_resolved: "인시던트 해결",
  incident_open: "인시던트 발생",
  incident_detected: "인시던트 감지",
  resolved: "해결됨",
  acknowledged: "승인됨",
  open: "발생",
  active: "활성",
  firing: "발생 중",
  approval_recommended: "승인 권장",
  // 배송/동기화
  synced: "동기화됨",
  outofsync: "동기화 안 됨",
  drift: "드리프트",
  refresh: "새로고침",
  trusted_proxy: "상위 프록시 인증",
  service_admin: "서비스 관리자",
};

const REASON_KO: Record<string, string> = {
  checks_observation_unavailable: "점검 관측 데이터가 아직 없습니다.",
  checks_definition_unavailable: "점검 정의가 아직 준비되지 않았습니다.",
  checks_observation_stale: "점검 관측 데이터가 오래되었습니다.",
  checks_observation_partial: "점검 데이터가 일부 범위에서만 수집되었습니다.",
  checks_observation_clock_skew: "점검 관측 시각에 편차가 있습니다.",
  checks_namespace_scope_partial: "일부 네임스페이스만 점검 범위에 포함되었습니다.",
  checks_catalog_conflict: "점검 카탈로그 정의가 충돌합니다.",
  cost_observation_unavailable: "비용 관측 데이터가 아직 없습니다.",
  cost_observation_not_integrated: "비용 관측 기능이 아직 연결되지 않았습니다.",
  node_pricing_observation_not_integrated: "노드 단가 관측 기능이 아직 연결되지 않았습니다.",
  application_bindings_incomplete: "애플리케이션 연결 정보가 아직 완전하지 않습니다.",
  source_labels_incomplete: "일부 소스 라벨이 누락되었습니다.",
  source_labels_truncated: "소스 라벨 일부가 수집 한도로 생략되었습니다.",
  source_resources_incomplete: "일부 소스 리소스가 아직 수집되지 않았습니다.",
  topology_projection_unavailable: "현재 데이터로 서비스 관계도를 구성할 수 없습니다.",
  traffic_observation_not_integrated: "트래픽 관측 기능이 아직 연결되지 않았습니다.",
  traffic_observation_unavailable: "트래픽 관측 데이터가 아직 없습니다.",
  inventory_snapshot_unavailable: "인벤토리 스냅샷을 아직 확인할 수 없습니다.",
  inventory_snapshot_partial: "인벤토리 스냅샷이 일부만 수집되었습니다.",
  "cluster_id is already registered": "이미 등록된 클러스터입니다.",
  "cluster_id is required": "클러스터 이름을 입력해 주세요.",
  "cluster_id must use lowercase letters, numbers, and hyphens": "클러스터 이름에는 영문 소문자, 숫자, 하이픈만 사용할 수 있습니다.",
  "test target registrations are not allowed": "테스트용 클러스터 이름은 등록할 수 없습니다.",
  "no exact gitops source binding was found for this live resource.": "이 라이브 리소스와 정확히 일치하는 GitOps 원본 연결을 찾지 못했습니다.",
  // GitHub/GitOps 저장소 디스커버리 — M24 구(영문 메시지)·신(structured code) 응답을 모두 매핑한다.
  // 429(요청 한도)와 403(권한)을 서로 다른 honest 안내로 구분한다.
  "github authentication failed or lacks repository access": "GitHub 인증에 실패했거나 저장소 접근 권한이 없습니다. 토큰의 저장소(repo) 권한을 확인해 주세요.",
  "github api request failed": "GitHub에 연결하지 못했습니다. 주소·네트워크를 확인하고 잠시 후 다시 시도해 주세요.",
  github_authentication_failed: "GitHub 인증에 실패했습니다. 토큰이 유효한지, 저장소 접근 권한이 있는지 확인해 주세요.",
  github_unauthorized: "GitHub 인증에 실패했습니다(401). 토큰을 다시 확인해 주세요.",
  github_forbidden: "GitHub 접근이 거부되었습니다(403). 토큰의 저장소 권한을 확인해 주세요.",
  github_rate_limited: "GitHub 요청 한도를 초과했습니다(429). 잠시 후 다시 시도해 주세요.",
  github_rate_limit_exceeded: "GitHub 요청 한도를 초과했습니다(429). 잠시 후 다시 시도해 주세요.",
  github_not_found: "GitHub 저장소를 찾을 수 없습니다. 주소와 접근 권한을 확인해 주세요.",
  github_api_request_failed: "GitHub에 연결하지 못했습니다. 주소·네트워크를 확인하고 잠시 후 다시 시도해 주세요.",
  github_response_invalid: "GitHub 응답 형식을 해석하지 못했습니다. 잠시 후 다시 시도해 주세요.",
  repository_probe_failed: "저장소 확인에 실패했습니다. 주소와 접근 권한을 확인해 주세요.",
  repository_unreachable: "저장소에 연결하지 못했습니다. 주소·네트워크를 확인해 주세요.",
  provider_not_configured: "이 제공자는 아직 서버에 구성되지 않았습니다.",
  provider_unavailable: "이 제공자는 현재 사용할 수 없습니다.",
  provider_disabled: "이 제공자는 현재 비활성화되어 있습니다.",
};

const MESSAGE_KO: Record<string, string> = {
  "git change confirmed": "Git 변경 확인",
  "git change confirmed; rendering manifest": "Git 변경 확인 · 매니페스트 반영 중",
  "manifest rendered": "매니페스트 렌더링 완료",
  "desired diff detected": "원하는 상태와 차이 감지",
  "pod readiness failure": "파드 준비 상태 실패",
  "readiness probe response failure": "준비 상태 확인 응답 실패",
};

/** 상태 토큰(단일 단어/스네이크)을 한글로. 매핑에 없으면 원문 유지. */
export function statusLabel(raw: string | null | undefined): string {
  if (raw === null || raw === undefined || raw === "") return "관측 안 됨";
  const key = raw.trim().toLowerCase().replace(/\s+/g, "_");
  return STATUS_KO[key] ?? STATUS_KO[key.replace(/_/g, "")] ?? raw;
}

/**
 * 백엔드 reason code/문장을 사용자에게 보여줄 때만 사용하는 표시 매퍼다.
 * 호출자는 원본 값을 상태·로그·감사 데이터에 그대로 보존해야 한다. 매핑되지 않은
 * 내부 코드는 그대로 노출하지 않고 정직한 일반 안내로 닫는다.
 */
export function reasonLabel(raw: string | null | undefined): string {
  if (raw === null || raw === undefined || raw.trim() === "") return "상세 사유가 제공되지 않았습니다.";
  const normalized = raw.trim().toLowerCase();
  const code = normalized.split(":")[0] ?? normalized;
  return REASON_KO[normalized] ?? REASON_KO[code] ?? "현재 관측 범위에서 상세 사유를 확인할 수 없습니다.";
}

/** 알려진 운영 메시지만 한글로 표시하고, 데이터 원문 자체는 바꾸지 않는다. */
export function operationalMessageLabel(raw: string | null | undefined): string {
  if (raw === null || raw === undefined || raw.trim() === "") return "관측 메시지 없음";
  const trimmed = raw.trim();
  const validatedRepository = /^validated (\d+) repository resources at ([0-9a-f]+); no cluster mutation$/i.exec(trimmed);
  if (validatedRepository) {
    return `저장소 리소스 ${validatedRepository[1]}개 검증 · ${validatedRepository[2]} · 클러스터 변경 없음`;
  }
  return MESSAGE_KO[trimmed.toLowerCase()] ?? statusLabel(trimmed);
}

/** 상태 토큰이 위험/실패 계열인지(색상 판정용, 지어내지 않고 관측값 기반). */
export function isCriticalStatus(raw: string | null | undefined): boolean {
  if (!raw) return false;
  return /crit|fail|unhealthy|crashloop|imagepull|error|offline|disconnect|notready|not-ready/i.test(raw);
}
