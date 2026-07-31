from __future__ import annotations

from services.ai.agent.defaults import ActionRoutes
from services.ai.agent.playbooks import RecoveryActionSpec, rca

routes = ActionRoutes()


@rca.recovery(
    root_causes=("oom_killed",),
    actions=(
        RecoveryActionSpec(
            action_type="rollout_restart",
            title="대상 워크로드 재시작",
            description="낮은 위험도의 임시 완화 조치로 대상 워크로드를 재시작합니다.",
            route=routes.auto,
            risk_level="low",
            score=0.58,
            blast_radius="target_workload",
            # 재시작은 되돌릴 변경이 없는 비파괴 조치 — auto route 와 일치시킴.
            approval_required=False,
            prerequisites=("대상 워크로드가 단일 namespace에 한정됨",),
            validation_checks=("재시작 후 ready replica 회복", "재시작 카운트 증가세 완화"),
            rollback_plan="재시작은 되돌릴 변경이 없으며, 실패 시 수동 조사로 전환합니다.",
            params={"command": "rollout_restart"},
            approval_required_outside_sandbox=True,
        ),
        RecoveryActionSpec(
            action_type="oom_memory",
            title="메모리 request/limit 조정 PR",
            description=(
                "관측 working set과 승인 manifest의 현재 request/limit을 기준으로 "
                "정책 상한 안의 메모리 headroom 패치를 제안합니다."
            ),
            route=routes.safe_pr,
            risk_level="medium",
            score=0.56,
            blast_radius="target_workload",
            approval_required=True,
            prerequisites=("수치형 container memory working set 근거", "GitOps 승인 snapshot"),
            validation_checks=("OOM 재발 없음", "메모리 사용률 안정", "pod ready 상태 유지"),
            rollback_plan="동반된 inverse patch로 이전 request/limit을 복원합니다.",
            params={
                "strategy": "usage_headroom",
                "headroom_ratio": 1.25,
                "max_memory": "4Gi",
            },
        ),
        RecoveryActionSpec(
            action_type="replica_scale",
            title="임시 replica 증설 PR",
            description="메모리 압박 완화를 위해 replica 증설 패치를 Safe PR로 제안합니다.",
            route=routes.safe_pr,
            risk_level="medium",
            score=0.52,
            blast_radius="target_workload",
            approval_required=True,
            prerequisites=("HPA 또는 수동 replica 정책 확인",),
            validation_checks=("에러율 감소", "메모리 사용률 하락", "pod ready 상태 유지"),
            rollback_plan="동반된 inverse patch로 replica 수를 이전 값으로 되돌립니다.",
            params={"strategy": "increment_one", "max_replicas": 10},
        ),
    ),
)
class OomKilledRecoveryActions:
    pass


@rca.recovery(
    root_causes=("application_5xx_spike",),
    actions=(
        RecoveryActionSpec(
            action_type="replica_scale",
            title="GitOps replica 증설 PR",
            description="승인 manifest의 현재 replica를 1개 늘리는 제한된 패치를 제안합니다.",
            route=routes.safe_pr,
            risk_level="medium",
            score=0.62,
            blast_radius="target_workload",
            approval_required=True,
            prerequisites=("GitOps 승인 snapshot", "replica 상한 10"),
            validation_checks=("Ready replica 증가", "5xx/timeout 감소", "리소스 여유 유지"),
            rollback_plan="동반된 inverse patch로 이전 replica 수를 복원합니다.",
            params={"strategy": "increment_one", "max_replicas": 10},
        ),
        RecoveryActionSpec(
            action_type="gitops_recovery_review",
            title="GitOps 복구 검토 PR",
            description=(
                "연결된 GitOps 레포에 복구 검토 문서를 생성해 원인, 대상, 검증 조건을 "
                "운영자가 확인한 뒤 실제 manifest 변경으로 이어가게 합니다."
            ),
            route=routes.safe_pr,
            risk_level="medium",
            score=0.35,
            blast_radius="target_workload",
            approval_required=True,
            prerequisites=(
                "대상 워크로드와 연결된 GitOps 레포가 있음",
                "변경 대상 manifest를 운영자가 확인함",
            ),
            validation_checks=(
                "RCA 근거와 대상 manifest 일치",
                "변경 전후 Ready replica 회복 기준 확인",
                "5xx 로그 감소",
            ),
            rollback_plan="생성된 PR 또는 merge commit을 revert합니다.",
            params={"document_type": "recovery_review"},
        ),
        RecoveryActionSpec(
            action_type="rollout_restart",
            title="대상 워크로드 재시작",
            description="최근 5xx/timeout을 내는 대상 워크로드를 재시작해 연결과 런타임 상태를 초기화합니다.",
            route=routes.auto,
            risk_level="low",
            score=0.5,
            blast_radius="target_workload",
            approval_required=False,
            prerequisites=("대상 워크로드가 sandbox namespace에 한정됨",),
            validation_checks=("5xx/timeout 로그 감소", "Ready replica 유지", "요청 성공률 회복"),
            rollback_plan="재시작은 되돌릴 변경이 없으며, 실패 시 scale 또는 수동 조사로 전환합니다.",
            params={"command": "rollout_restart"},
            approval_required_outside_sandbox=True,
        ),
    ),
)
class Application5xxRecoveryActions:
    pass


@rca.recovery(
    root_causes=("backend_readiness_failure", "upstream_unavailable"),
    actions=(
        RecoveryActionSpec(
            action_type="rollout_restart",
            title="대상 워크로드 재시작",
            description="최근 5xx/timeout을 내는 대상 워크로드를 재시작해 연결과 런타임 상태를 초기화합니다.",
            route=routes.auto,
            risk_level="low",
            score=0.66,
            blast_radius="target_workload",
            approval_required=False,
            prerequisites=("대상 워크로드가 sandbox namespace에 한정됨",),
            validation_checks=("5xx/timeout 로그 감소", "Ready replica 유지", "요청 성공률 회복"),
            rollback_plan="재시작은 되돌릴 변경이 없으며, 실패 시 scale 또는 수동 조사로 전환합니다.",
            params={"command": "rollout_restart"},
            approval_required_outside_sandbox=True,
        ),
    ),
)
class NetworkRecoveryActions:
    pass


@rca.recovery(
    root_causes=("lobby_capacity_saturation",),
    actions=(
        RecoveryActionSpec(
            action_type="replica_scale",
            title="로비 replicas 복구 PR",
            description=(
                "최근 배포의 축소 이력이 있으면 이전 승인 값으로 되돌리고, 이력이 없으면 "
                "정책 범위에서 한 대를 증설하는 Safe PR을 제안합니다."
            ),
            route=routes.safe_pr,
            risk_level="medium",
            score=0.72,
            blast_radius="target_workload",
            approval_required=True,
            prerequisites=(
                "대상 워크로드와 연결된 GitOps 레포가 있음",
                "축소 이전의 승인 replicas 값 확인",
            ),
            validation_checks=(
                "PR 병합 후 선언 replicas 와 실행 replicas 일치",
                "매치메이킹 실패율 하락 유지",
            ),
            rollback_plan="생성된 PR 또는 merge commit 을 revert 합니다.",
            params={
                "strategy": "last_approved_snapshot",
                "allow_bounded_scale_out": True,
                "verification_contract": "protected_workload_continuity",
            },
        ),
    ),
)
class MatchmakingSaturationRecoveryActions:
    pass


@rca.recovery(
    root_causes=("bad_image_rollout", "app_startup_failure"),
    actions=(
        RecoveryActionSpec(
            action_type="image_rollback",
            title="이전 이미지 rollback PR",
            description="최근 이미지 변경이 원인일 가능성이 높아 이전 태그로 되돌리는 PR을 제안합니다.",
            route=routes.safe_pr,
            risk_level="medium",
            score=0.74,
            blast_radius="target_workload",
            approval_required=True,
            prerequisites=("이전 정상 revision 확인", "rollback 이미지 digest 확인"),
            validation_checks=("새 pod ready", "startup error 소멸", "5xx 감소"),
            rollback_plan="rollback PR revert 또는 원래 이미지 tag 재적용",
            params={"strategy": "last_approved_snapshot"},
        ),
    ),
)
class RolloutRecoveryActions:
    pass


@rca.recovery(
    root_causes=("config_env_error",),
    actions=(
        RecoveryActionSpec(
            action_type="config_fix",
            title="설정 보정 PR",
            description="누락된 환경변수나 설정 키를 보정하는 Safe PR을 제안합니다.",
            route=routes.safe_pr,
            risk_level="medium",
            score=0.68,
            blast_radius="target_workload",
            approval_required=True,
            prerequisites=("누락 key와 기대 value 확인",),
            validation_checks=("config load error 소멸", "pod ready", "재시작 루프 중단"),
            rollback_plan="설정 보정 commit revert",
            params={"strategy": "operator_supplied_config_value"},
        ),
    ),
)
class ConfigRecoveryActions:
    pass


@rca.recovery(
    root_causes=("wrong_image_tag",),
    actions=(
        RecoveryActionSpec(
            action_type="image_tag_fix",
            title="이미지 태그 보정 PR",
            description="존재하지 않는 이미지 태그를 이전 정상 태그 또는 검증된 digest로 보정하는 PR을 제안합니다.",
            route=routes.safe_pr,
            risk_level="medium",
            score=0.7,
            blast_radius="target_workload",
            approval_required=True,
            prerequisites=("정상 이미지 태그 또는 digest 확인",),
            validation_checks=(
                "새 pod image pull 성공",
                "Ready 상태 회복",
                "ImagePullBackOff 이벤트 소멸",
            ),
            rollback_plan="이미지 태그 보정 commit revert",
            params={"strategy": "last_approved_snapshot"},
        ),
    ),
)
class ImageTagRecoveryActions:
    pass


@rca.recovery(
    root_causes=("missing_image_pull_secret",),
    actions=(
        RecoveryActionSpec(
            action_type="image_pull_secret_fix",
            title="이미지 pull Secret 보정",
            description="대상 namespace의 imagePullSecret 참조와 registry 인증 정보를 보정합니다.",
            route=routes.approval_required,
            risk_level="medium",
            score=0.66,
            blast_radius="target_namespace",
            approval_required=True,
            prerequisites=("registry 접근 권한 확인", "Secret 이름과 namespace 확인"),
            validation_checks=("image pull 성공", "Pod Ready 전환", "인증 실패 이벤트 소멸"),
            rollback_plan="변경한 Secret 참조 또는 Secret 값을 이전 상태로 되돌립니다.",
            params={"manual": True, "fix": "image_pull_secret"},
        ),
    ),
)
class ImagePullSecretRecoveryActions:
    pass


@rca.recovery(
    root_causes=("registry_unavailable",),
    actions=(
        RecoveryActionSpec(
            action_type="registry_recovery",
            title="Registry 경로 복구",
            description="registry/LB 상태를 확인하고 pull 재시도 또는 mirror 전환을 승인형 조치로 진행합니다.",
            route=routes.approval_required,
            risk_level="medium",
            score=0.62,
            blast_radius="target_namespace",
            approval_required=True,
            prerequisites=("registry 상태 확인", "mirror 또는 캐시 registry 사용 가능 여부 확인"),
            validation_checks=("registry 응답 정상", "image pull 재시도 성공", "Pending Pod 감소"),
            rollback_plan="mirror 전환 시 원 registry 참조로 되돌립니다.",
            params={"manual": True, "fix": "registry_path"},
        ),
    ),
)
class RegistryRecoveryActions:
    pass


@rca.recovery(
    root_causes=("insufficient_cpu", "insufficient_memory"),
    actions=(
        RecoveryActionSpec(
            action_type="resource_request_tuning",
            title="리소스 요청값 조정 PR",
            description="스케줄 가능한 범위로 CPU/메모리 request를 조정하거나 replica 배치를 나누는 PR을 제안합니다.",
            route=routes.safe_pr,
            risk_level="medium",
            score=0.64,
            blast_radius="target_workload",
            approval_required=True,
            prerequisites=("현재 request/limit과 노드 allocatable 확인",),
            validation_checks=(
                "Pod Scheduled 전환",
                "Ready 상태 회복",
                "FailedScheduling 이벤트 소멸",
            ),
            rollback_plan="리소스 request 조정 commit revert",
            params={"strategy": "fit_node_allocatable"},
        ),
    ),
)
class SchedulingCapacityRecoveryActions:
    pass


@rca.recovery(
    root_causes=("node_affinity_or_taint_mismatch",),
    actions=(
        RecoveryActionSpec(
            action_type="scheduling_constraint_fix",
            title="스케줄링 조건 보정 PR",
            description="nodeSelector, affinity, toleration 조건을 현재 노드 라벨과 정책에 맞게 보정합니다.",
            route=routes.safe_pr,
            risk_level="medium",
            score=0.68,
            blast_radius="target_workload",
            approval_required=True,
            prerequisites=("허용 노드 라벨과 taint/toleration 정책 확인",),
            validation_checks=(
                "Pod Scheduled 전환",
                "Ready 상태 회복",
                "affinity/taint 이벤트 소멸",
            ),
            rollback_plan="스케줄링 조건 보정 commit revert",
            params={"strategy": "match_approved_node_policy"},
        ),
    ),
)
class SchedulingConstraintRecoveryActions:
    pass


@rca.recovery(
    root_causes=("pvc_pending",),
    actions=(
        RecoveryActionSpec(
            action_type="pvc_binding_fix",
            title="PVC 바인딩 복구",
            description="PVC, StorageClass, zone binding 상태를 확인하고 바인딩 가능한 설정으로 보정합니다.",
            route=routes.approval_required,
            risk_level="medium",
            score=0.62,
            blast_radius="target_namespace",
            approval_required=True,
            prerequisites=("PVC와 StorageClass 상태 확인", "데이터 보존 정책 확인"),
            validation_checks=(
                "PVC Bound 전환",
                "Pod Scheduled 전환",
                "volume binding 이벤트 소멸",
            ),
            rollback_plan="StorageClass/PVC 설정 변경을 이전 값으로 되돌립니다.",
            params={"manual": True, "fix": "pvc_binding"},
        ),
    ),
)
class PvcBindingRecoveryActions:
    pass


@rca.recovery(
    root_causes=(
        "probe_path_wrong",
        "probe_port_wrong",
        "timeout_too_short",
        "startup_window_too_short",
    ),
    actions=(
        RecoveryActionSpec(
            action_type="probe_fix",
            title="Probe 설정 보정 PR",
            description=(
                "승인 snapshot의 probe scalar와 검증된 이전 값 또는 bounded timeout 정책을 "
                "사용해 path/port/timeout 패치를 제안합니다."
            ),
            route=routes.safe_pr,
            risk_level="medium",
            score=0.66,
            blast_radius="target_workload",
            approval_required=True,
            prerequisites=("GitOps 승인 snapshot", "probe 실패 근거"),
            validation_checks=("Probe 성공", "Pod Ready 전환", "실제 health 실패 은폐 없음"),
            rollback_plan="동반된 inverse patch로 이전 probe scalar를 복원합니다.",
            params={"strategy": "approved_value_or_bounded_timeout"},
        ),
    ),
)
class ProbeRecoveryActions:
    pass


@rca.recovery(
    root_causes=("handoff_authority_stalled",),
    actions=(
        RecoveryActionSpec(
            action_type="handoff_authority_recovery",
            title="커밋된 Candidate 권위 forward-reconcile",
            description=(
                "현재 Candidate와 Gateway가 같은 endpoint/checkpoint를 가리키는지 확인한 뒤, "
                "더 높은 fencing epoch로 권위를 승격하고 stale verification을 대체·완료합니다. "
                "probe 완화나 무조건 재시작은 수행하지 않습니다."
            ),
            route=routes.approval_required,
            risk_level="high",
            score=0.92,
            blast_radius="target_workload",
            approval_required=True,
            prerequisites=(
                "Candidate ready/caughtUp 및 checksum 확인",
                "Gateway route가 같은 endpoint와 현재 epoch를 가리킴",
                "live session 수와 stale verification operation 확인",
                "핸드오프 checksum 경합 수정 PR 검토",
            ),
            validation_checks=(
                "권위와 Gateway route가 동일한 상향 epoch로 정렬",
                "stale verification 제거 및 새 verification 완료",
                "healthz 200과 Pod Ready 회복",
                "연속 두 evidence window에서 kubernetes/metrics/logs/traces/metadata 수집 확인",
                "5~10분 재관찰 동안 readiness 503 재발 없음",
            ),
            rollback_plan=(
                "승격 전에는 freeze를 abort합니다. 승격 후에는 Candidate를 삭제하거나 단순 "
                "restart하지 않고 현재 권위를 유지한 채 별도 상향 epoch 복구로 전환합니다."
            ),
            params={
                "manual": True,
                "strategy": "forward_reconcile_committed_candidate",
                "forbid": ["probe_relaxation", "blind_rollout_restart"],
            },
        ),
    ),
)
class HandoffAuthorityRecoveryActions:
    pass


@rca.recovery(
    root_causes=("selector_label_mismatch",),
    actions=(
        RecoveryActionSpec(
            action_type="selector_fix",
            title="Deployment selector 최소 보정 PR",
            description=(
                "승인 Deployment snapshot에서 template label과 불일치한 단일 selector scalar만 "
                "보정하는 PR을 제안합니다."
            ),
            route=routes.safe_pr,
            risk_level="medium",
            score=0.68,
            blast_radius="target_workload",
            approval_required=True,
            prerequisites=("단일 selector 불일치", "GitOps 승인 snapshot"),
            validation_checks=("selector-template 일치", "Ready endpoint 회복"),
            rollback_plan="동반된 inverse patch로 이전 selector를 복원합니다.",
            params={"strategy": "match_template_label", "max_fields": 1},
        ),
    ),
)
class SelectorRecoveryActions:
    pass


@rca.recovery(
    root_causes=("app_port_bind_failed",),
    actions=(
        RecoveryActionSpec(
            action_type="container_port_review",
            title="컨테이너 포트 충돌 검토",
            description="프로세스가 바인딩하려는 포트와 manifest/service/probe 포트 설정을 함께 확인합니다.",
            route=routes.approval_required,
            risk_level="medium",
            score=0.62,
            blast_radius="target_workload",
            approval_required=True,
            prerequisites=(
                "startup log의 port bind 실패 근거",
                "컨테이너 port와 service targetPort 확인",
            ),
            validation_checks=("pod 재시작 루프 중단", "프로세스 listen 성공", "Ready 상태 회복"),
            rollback_plan="포트 설정 변경이 있었다면 manifest 변경 commit을 revert합니다.",
            params={"manual": True, "fix": "container_port"},
        ),
    ),
)
class StartupPortRecoveryActions:
    pass


@rca.recovery(
    root_causes=("permission_denied_startup",),
    actions=(
        RecoveryActionSpec(
            action_type="startup_security_context_review",
            title="Startup 권한/보안 컨텍스트 확인",
            description="permission denied 로그와 securityContext, volume mount 권한, 실행 사용자 설정을 확인합니다.",
            route=routes.approval_required,
            risk_level="medium",
            score=0.62,
            blast_radius="target_workload",
            approval_required=True,
            prerequisites=(
                "startup log의 permission denied 근거",
                "securityContext와 mount permission 확인",
            ),
            validation_checks=(
                "permission denied 로그 소멸",
                "pod Ready 회복",
                "보안 정책 위반 없음",
            ),
            rollback_plan="securityContext 또는 mount 권한 변경 commit을 revert합니다.",
            params={"manual": True, "fix": "startup_permission"},
        ),
    ),
)
class StartupPermissionRecoveryActions:
    pass


@rca.recovery(
    root_causes=("config_key_missing",),
    actions=(
        RecoveryActionSpec(
            action_type="config_key_review",
            title="ConfigMap key 누락 확인",
            description="참조한 ConfigMap key가 실제 객체에 존재하는지 확인하고 필요한 보정 값을 운영자가 결정합니다.",
            route=routes.approval_required,
            risk_level="medium",
            score=0.64,
            blast_radius="target_workload",
            approval_required=True,
            prerequisites=("누락 key 이름 확인", "ConfigMap 참조 namespace 확인"),
            validation_checks=(
                "config load error 소멸",
                "pod Ready 회복",
                "잘못된 기본값 주입 없음",
            ),
            rollback_plan="ConfigMap key 보정 또는 참조 변경 commit을 revert합니다.",
            params={"manual": True, "fix": "config_key"},
        ),
    ),
)
class ConfigKeyRecoveryActions:
    pass


@rca.recovery(
    root_causes=("missing_secret_reference",),
    actions=(
        RecoveryActionSpec(
            action_type="secret_reference_fix",
            title="Secret 참조 누락 확인",
            description="Pod가 참조하는 Secret 이름과 key를 확인하고 민감값 없이 참조 수준에서 보정합니다.",
            route=routes.approval_required,
            risk_level="medium",
            score=0.64,
            blast_radius="target_namespace",
            approval_required=True,
            prerequisites=("Secret 이름과 key reference 확인", "대상 namespace 확인"),
            validation_checks=(
                "secret not found 이벤트 소멸",
                "pod Ready 회복",
                "민감값 노출 없음",
            ),
            rollback_plan="Secret 참조 변경 commit을 revert하거나 이전 참조로 되돌립니다.",
            params={"manual": True, "fix": "secret_reference"},
        ),
    ),
)
class SecretReferenceRecoveryActions:
    pass


@rca.recovery(
    root_causes=("service_name_or_namespace_mismatch",),
    actions=(
        RecoveryActionSpec(
            action_type="service_reference_review",
            title="Service 이름/namespace 참조 확인",
            description="애플리케이션이 호출하는 service DNS 이름과 실제 Service namespace/name을 비교합니다.",
            route=routes.approval_required,
            risk_level="medium",
            score=0.62,
            blast_radius="target_workload",
            approval_required=True,
            prerequisites=("오류 로그의 service host 확인", "실제 Service name/namespace 확인"),
            validation_checks=(
                "DNS lookup 실패 소멸",
                "대상 service 연결 성공",
                "5xx/timeout 감소",
            ),
            rollback_plan="service 참조 설정 변경 commit을 revert합니다.",
            params={"manual": True, "fix": "service_reference"},
        ),
    ),
)
class ServiceReferenceRecoveryActions:
    pass


@rca.recovery(
    root_causes=("network_policy_denied",),
    actions=(
        RecoveryActionSpec(
            action_type="network_policy_review",
            title="NetworkPolicy 차단 확인",
            description="대상 namespace의 ingress/egress NetworkPolicy가 필요한 service 통신을 차단하는지 확인합니다.",
            route=routes.approval_required,
            risk_level="medium",
            score=0.62,
            blast_radius="target_namespace",
            approval_required=True,
            prerequisites=("차단된 source/destination 확인", "적용 중인 NetworkPolicy 확인"),
            validation_checks=(
                "허용 후 연결 성공",
                "불필요한 namespace 노출 없음",
                "5xx/timeout 감소",
            ),
            rollback_plan="NetworkPolicy 변경 commit을 revert합니다.",
            params={"manual": True, "fix": "network_policy"},
        ),
    ),
)
class NetworkPolicyRecoveryActions:
    pass


@rca.recovery(
    root_causes=("metrics_server_unavailable",),
    actions=(
        RecoveryActionSpec(
            action_type="autoscaling_metrics_recovery",
            title="Autoscaling metrics 경로 복구",
            description="metrics-server 또는 custom metrics adapter 상태를 확인해 HPA replica 계산 경로를 복구합니다.",
            route=routes.approval_required,
            risk_level="medium",
            score=0.62,
            blast_radius="target_namespace",
            approval_required=True,
            prerequisites=(
                "HPA FailedGetResourceMetric 근거",
                "metrics API 또는 adapter 상태 확인",
            ),
            validation_checks=("HPA metric 조회 성공", "ScalingActive 회복", "replica 계산 재개"),
            rollback_plan="metrics adapter 또는 HPA 설정 변경 commit을 revert합니다.",
            params={"manual": True, "fix": "autoscaling_metrics"},
        ),
    ),
)
class AutoscalingMetricsRecoveryActions:
    pass


@rca.recovery(
    root_causes=("missing_resource_requests",),
    actions=(
        RecoveryActionSpec(
            action_type="resource_request_tuning",
            title="HPA resource request 보정 PR",
            description="HPA 계산에 필요한 CPU/memory request를 정책 범위 안에서 보정하는 PR을 제안합니다.",
            route=routes.safe_pr,
            risk_level="medium",
            score=0.64,
            blast_radius="target_workload",
            approval_required=True,
            prerequisites=("누락된 resource request 확인", "GitOps 승인 snapshot"),
            validation_checks=("HPA metric 계산 성공", "Pod Ready 유지", "리소스 사용률 안정"),
            rollback_plan="resource request 보정 commit을 revert합니다.",
            params={"strategy": "hpa_required_requests"},
        ),
    ),
)
class AutoscalingResourceRequestRecoveryActions:
    pass


@rca.recovery(
    root_causes=("max_replica_limit_reached",),
    actions=(
        RecoveryActionSpec(
            action_type="replica_scale",
            title="HPA maxReplicas 상한 검토 PR",
            description="트래픽 증가로 maxReplicas 상한에 도달한 경우 제한된 replica 상향 PR을 제안합니다.",
            route=routes.safe_pr,
            risk_level="medium",
            score=0.62,
            blast_radius="target_workload",
            approval_required=True,
            prerequisites=("ScalingLimited=True 근거", "GitOps 승인 snapshot", "리소스 여유 확인"),
            validation_checks=("Ready replica 증가", "ScalingLimited 완화", "5xx/latency 감소"),
            rollback_plan="동반된 inverse patch로 replica 상한 또는 replica 수를 이전 값으로 되돌립니다.",
            params={"strategy": "hpa_max_replicas_review", "max_replicas": 10},
        ),
    ),
)
class AutoscalingMaxReplicaRecoveryActions:
    pass


@rca.recovery(
    root_causes=("database_connectivity_failure", "database_connection_pool_exhausted"),
    actions=(
        RecoveryActionSpec(
            action_type="dependency_connection_review",
            title="DB 연결 경로 복구 검토",
            description="DB endpoint, 네트워크 경로, connection pool 상태를 확인해 외부 의존성 복구 조치를 결정합니다.",
            route=routes.approval_required,
            risk_level="medium",
            score=0.62,
            blast_radius="target_workload",
            approval_required=True,
            prerequisites=(
                "DB connection error 로그 또는 trace 근거",
                "DB endpoint와 pool 설정 확인",
            ),
            validation_checks=("DB 연결 성공", "pool exhausted 로그 감소", "요청 성공률 회복"),
            rollback_plan="DB connection 설정 변경 commit을 revert합니다.",
            params={"manual": True, "fix": "db_connectivity"},
        ),
    ),
)
class DatabaseConnectivityRecoveryActions:
    pass


@rca.recovery(
    root_causes=("database_credential_or_config_error",),
    actions=(
        RecoveryActionSpec(
            action_type="dependency_config_review",
            title="DB 인증/설정 참조 확인",
            description="DB host, database name, credential Secret/ConfigMap 참조를 민감값 없이 확인합니다.",
            route=routes.approval_required,
            risk_level="medium",
            score=0.62,
            blast_radius="target_workload",
            approval_required=True,
            prerequisites=("DB credential/config error 근거", "Secret/ConfigMap reference 확인"),
            validation_checks=("DB 인증 성공", "설정 오류 로그 소멸", "민감값 노출 없음"),
            rollback_plan="DB 설정 참조 변경 commit을 revert합니다.",
            params={"manual": True, "fix": "db_config"},
        ),
    ),
)
class DatabaseConfigRecoveryActions:
    pass


@rca.fallback(
    actions=(
        RecoveryActionSpec(
            action_type="manual_analysis",
            title="수동 RCA 분석 요청",
            description="자동 복구 후보가 충분하지 않아 운영자 검토가 필요합니다.",
            route=routes.approval_required,
            risk_level="unknown",
            score=0.0,
            blast_radius="unknown",
            approval_required=True,
            prerequisites=("운영자 RCA 검토",),
            validation_checks=("원인 rule 추가 여부 검토",),
            rollback_plan="자동 변경 없음",
            params={"manual": True},
        ),
    ),
)
class FallbackRecoveryActions:
    pass
