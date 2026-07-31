// ⚠ 데모 · 통합 리소스 — 리소스 종류 인덱스(전체 택소노미) + 종류별 표 + 물리/관계 관점.
// 병합 규칙: 좌측 = 무엇을(종류) · 상단 관점 = 어떻게(물리/관계/목록).
// 워크로드·노드 계열은 관점 전환이 가능하고, 나머지는 종류별 전용 표로 정보를 잃지 않게 표시.
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  Server, FileCog, Network, Globe, Search, KeyRound,
  Rocket, Database, Boxes, Copy, LayoutGrid, Play, Timer, Plug, DoorOpen, ShieldCheck, MoveDiagonal,
  HardDrive, Cpu, Folder, Activity, UserCog, Eye, Radio, ChevronDown, Pin,
  Home, ListTree, AlertTriangle, Clock, Coins, Settings, Sparkles, PanelLeftClose, PanelLeftOpen,
  Bell, Pencil, Check, Hourglass, Webhook, SignalHigh, Building2, LogOut, RefreshCw,
} from "lucide-react";
import { HomeClustersWidget, OpsiaMap } from "./devpreview-opsia";
import { DASHBOARD_WIDGET_GRID_CLASS, DASHBOARD_WIDGET_GRID_ITEM_CLASS, WidgetFrame, RatioBar, Donut, RankList, MultiLine, MiniTimeline, dashboardWidgetGridStyle, dashboardWidgetItemStyle, type DashboardWidgetSpan } from "./devpreview/widgets";
import { DeploySurface, IssuesSurface, TimelineSurface, ChecksSurface, CostSurface, SettingsSurface, AlertsSurface, AiHistorySurface, IssueDetail, type RcaIncident } from "./devpreview-surfaces";
import type { RecoveryProgressOverride } from "./devpreview/recoveryProgress";
import { AiPanel, type RecoveryReviewState } from "./devpreview-ai";
import type { AiRecoveryHandoff } from "./features/ai-assistant/aiRecoveryHandoff";
import { isSafePrRoute } from "./devpreview/recoveryRoute";
import { onAction, type DemoAction } from "./devpreview/bus";
import {
  ConnectWizard,
  type RepositoryConnectionContext,
  type ResumeClusterConnection,
} from "./devpreview-connect";
import { TopologyView } from "./devpreview-topology";
import { GithubIcon } from "./devpreview/brandIcons";
import { DevpreviewContractProvider, useDevpreviewContracts } from "./devpreview/contracts";
import { ClusterLifecycleControl, toHomeClusterChoice } from "./devpreview/ClusterLifecycleControl";
import { ConnectionControlCenter } from "./devpreview/ConnectionControlCenter";
import { ClusterDisconnectDialog } from "./pages/clusters/ClusterDisconnectDialog";
import { createClusterDisconnectPort } from "./app/composition/surfaces/clusters";
import { DetailDrawer, DetailDrawerTabs } from "./devpreview/DetailDrawer";
import {
  SIDE_PANEL_ENTER_TRANSITION,
  SIDE_PANEL_EXIT_TRANSITION,
  SIDE_PANEL_SURFACE_STYLE,
  SIDE_PANEL_WIDTH_TRANSITION,
  SidePanelResizeHandle,
  SidePanelWindowControls,
  SIDE_PANEL_CONTENT_HOST_STYLE,
  clampSidePanelWidth,
  sidePanelWidthFromKeyboard,
} from "./devpreview/SidePanelShell";
import { useFleetSummaryFeed } from "./devpreview/fleetSummaryFeed";
import { fleetHeaderGroups } from "./devpreview/fleetSummaryPresentation";
import { type ProductSurfaceId } from "./devpreview/realtimeContractMatrix";
import { parseShellRoute, updateShellRouteSearch } from "./devpreview/shellRoute";

// 목록(⋮ 메뉴) 연결 해제도 상세 뷰와 같은 캐논 계약 port 를 공유한다 —
// 두 번째 unregister 구현이 생기지 않게 하는 ClusterLifecycleControl 원칙 준수.
const LIST_CLUSTER_DISCONNECT_PORT = createClusterDisconnectPort();

function recoveryReviewStatusLabel(state: RecoveryReviewState, route?: string | null): string {
  if (state === "reviewing") return "복구 플랜 검토 중";
  if (state === "ready") return "복구 플랜 검토 완료";
  if (state === "executing") return "복구 요청 중";
  if (state === "executed") {
    if (route === "auto") return "자동 복구 요청됨";
    if (isSafePrRoute(route)) return "PR 생성 요청됨";
    return "복구 요청됨";
  }
  if (state === "error") return "복구 플랜 검토 실패";
  return "복구 플랜 검토";
}
import {
  PENDING_CLUSTER_STORAGE_KEY,
  reconcilePendingClusters,
  removePendingClusterReferences,
} from "./devpreview/pendingClusterState";
import { AuthSessionGateProvider } from "./features/auth/AuthSessionGate";
import { I18nProvider } from "./shared/i18n";
import { activeIncidentClusterIds, useRcaIssues } from "./devpreview/rcaIssuesFeed";
import {
  rcaIssuePinGroupKey,
  rcaIssuePinStorageKey,
  readStoredRcaIssuePins,
  upsertStoredRcaIssuePin,
  writeStoredRcaIssuePins,
  type StoredRcaIssuePin,
} from "./devpreview/rcaPinnedIssueStore";
import {
  useRcaIssueDetails,
  type RcaIssueDetailView,
} from "./devpreview/rcaDetailFeed";
import { useCostOverview } from "./devpreview/costFeed";
import { useSession, sessionInitial } from "./devpreview/sessionFeed";
import {
  useInventoryNamespaces,
  type InventoryNamespacesView,
} from "./devpreview/inventoryNamespacesFeed";
import { useChangeTimeline } from "./devpreview/changeTimelineFeed";
import { useApplications, type ApplicationsFeed } from "./devpreview/deployFeed";
import {
  alertEventOccurrenceKey,
  isIncidentNotification,
  useAlertEvents,
  type AlertEventsFeed,
  type AlertEventView,
} from "./devpreview/alertsFeed";
import { alertEventPresentation, strongestAlertEventPresentation, type AlertEventIcon } from "./devpreview/alertEventPresentation";
import {
  alertIncidentClusterIds,
  alertIncidentPollMs,
  incidentFromAlertEvent,
  incidentFromRcaIssue,
  promoteAlertIncident,
} from "./devpreview/alertIncident";
import { acknowledgeAlertEvent } from "./api/alert-events";
import { useRelationTopology, type RelationNodeView } from "./devpreview/relationTopologyFeed";
import { logout as logoutApi } from "./devpreview/sessionFeed";
import { useInventoryResourcesAcrossClusters, useInventoryKindCounts, kindToResourceType } from "./devpreview/inventoryResourcesFeed";
import { useWorkloadDetail } from "./devpreview/workloadDetailFeed";
import { useResourceUsageSeries, type ResourceUsagePoint } from "./devpreview/resourceUsageFeed";
import { useResourceAccess } from "./devpreview/resourceAccessFeed";
import { ResourceAccessPanel } from "./devpreview/resourceAccessPanel";
import {
  ResourceAuxiliaryHeader,
  ResourceAuxiliaryPanel,
  ResourceAuxiliaryRow,
  ResourceAuxiliarySection,
  resourceAuxiliaryFooterButtonStyle,
  resourceAuxiliaryViewportHeight,
} from "./devpreview/ResourceAuxiliaryPanel";
import { SegmentedControl } from "./devpreview/SegmentedControl";
import { EventMessageText } from "./devpreview/EventMessageText";
import { usePodResourceDetail } from "./devpreview/podResourceDetailFeed";
import { projectPodOperationalCause } from "./devpreview/podOperationalCause";
import { PodContainerDetail } from "./devpreview/PodContainerDetail";
import { ResourceConditionsPanel } from "./devpreview/ResourceConditionsPanel";
import {
  EMPTY_RESOURCE_CONDITIONS_VIEW,
  useResourceConditions,
  type ResourceConditionsView,
} from "./devpreview/resourceConditionsFeed";
import { useResourceEvents } from "./devpreview/resourceEventsFeed";
import { useResourceIdentity } from "./devpreview/resourceIdentityFeed";
import { getRepositoryConnectionStatus } from "./api/repository-connection";
import { useNarrowViewport } from "./devpreview/useNarrowViewport";
import { operationalMessageLabel, reasonLabel, statusLabel, isCriticalStatus } from "./devpreview/statusLabel";
import { LiveResourceManifestEditor } from "./devpreview/resourceManifestEditor";
import type { ResourceManifestSourceEndpoint } from "./devpreview/resourceManifestFeed";
import { groupApplicationsByRepository } from "./devpreview/repositoryRegistry";
import {
  activeAlertRows,
  applicationAttentionRows,
  applicationHealthItems,
} from "./devpreview/homeWidgetPresentation";
import { podsForNode, useClusterTopology } from "./devpreview/inventoryTopologyFeed";
import { UI, BLUE, BLUE2, HP, INTERACTION, TINT, MONO, TYPE, SOFT, SPRING, PRESENT_SCALE, DUR, RADIUS, SPACE, RESOURCE_LAYOUT, inkA, blueA, MARK, cardA, GLASS, critA } from "./devpreview/theme";
import "./styles/tokens.css";
import "./styles/foundation.css";


// ── 파생 헬퍼 (가짜 rng/합성 행 제거 — 표는 라이브 인벤토리 계약으로 배선) ──
// nsFor/clusterOf/contractClusterOf 제거: 이름 해싱으로 네임스페이스·클러스터를 지어내던
// 합성 파생을 없앴다. 스코프 필터는 행의 관측된 cluster 필드만 사용하고(없으면 필터하지 않음),
// 상세 진입 fallback은 { name }만 넘긴다(네임스페이스를 이름에서 유추하지 않는다).
// imgFor 합성 이미지 identity 제거 — DetailOverlay가 유일 사용처였고(가짜 이미지 표기),
// 이제 관측된 row.img가 없으면 "관측 안 됨"으로 표기한다.

type Cell = { t: "text" } | { t: "mono" } | { t: "ns" } | { t: "ready" } | { t: "badge"; tone?: "blue" | "green" | "gray" | "purple" }
  | { t: "meter" } | { t: "dots" } | { t: "num" } | { t: "status" };
type Col = { k: string; label: string; w?: string; cell: Cell };
type Row = Record<string, unknown>;
// 리소스 본문의 유일한 sticky 기준선. 관점 전환 바가 이 높이를 차지하고,
// 표 헤더와 보조 패널은 정확히 그 아래에서 고정된다.
const RESOURCE_VIEW_STICKY_TOP = RESOURCE_LAYOUT.viewSwitcherHeight;
const RESOURCE_AUX_STICKY_TOP = RESOURCE_LAYOUT.viewSwitcherHeight + RESOURCE_LAYOUT.stickyGap;

const COLUMN_LABEL_KO: Record<string, string> = {
  "ACCESS MODES": "접근 모드",
  ACTIVE: "활성",
  ADDRESS: "주소",
  ADDRESSTYPE: "주소 종류",
  AGE: "경과",
  "ALLOWED DISRUPTIONS": "허용 중단",
  ATTACHER: "연결자",
  AUTOMOUNT: "자동 마운트",
  AVAILABLE: "사용 가능",
  CAPACITY: "용량",
  CLASS: "클래스",
  COMPLETIONS: "완료",
  CONTAINERS: "컨테이너",
  COUNT: "횟수",
  CPU: "CPU",
  DESCRIPTION: "설명",
  DESIRED: "요청",
  DURATION: "소요 시간",
  ENDPOINTS: "엔드포인트",
  EXPIRES: "만료",
  EXTERNAL: "외부 주소",
  GENERATORS: "생성기",
  "GLOBAL-DEFAULT": "전역 기본",
  HANDLER: "핸들러",
  HEALTH: "상태",
  HOLDER: "보유자",
  HOSTS: "호스트",
  IMAGES: "이미지",
  INSTANCE: "인스턴스",
  KEYS: "키",
  "LAST SCHEDULE": "최근 실행",
  "MAX UNAVAILABLE": "최대 비가용",
  MAXPODS: "최대 파드",
  MEMORY: "메모리",
  MESSAGE: "메시지",
  "MIN AVAILABLE": "최소 가용",
  MINPODS: "최소 파드",
  NAME: "이름",
  NAMESPACE: "네임스페이스",
  NODE: "노드",
  OBJECT: "대상",
  OWNER: "소유자",
  "POD SELECTOR": "파드 셀렉터",
  PODS: "파드",
  PORTS: "포트",
  PROVISIONER: "프로비저너",
  PV: "PV",
  READY: "준비",
  REASON: "사유",
  RECLAIMPOLICY: "회수 정책",
  REFERENCE: "대상",
  REPLICAS: "복제본",
  REVISION: "리비전",
  ROLE: "역할",
  RULES: "규칙",
  SCHEDULE: "스케줄",
  SECRETS: "시크릿",
  SELECTOR: "셀렉터",
  SERVICE: "서비스",
  SIZE: "크기",
  STATUS: "상태",
  STORAGECLASS: "스토리지 클래스",
  SUBJECTS: "주체",
  SUSPEND: "중지",
  SYNC: "동기화",
  TARGETS: "목표",
  TIMEZONE: "시간대",
  TYPE: "종류",
  TYPES: "정책 종류",
  "UP-TO-DATE": "최신",
  VALUE: "값",
  VOLUME: "볼륨",
  VOLUMEATTRIBUTESCLASS: "볼륨 속성 클래스",
  VOLUMEBINDINGMODE: "바인딩 모드",
  WEBHOOKS: "웹훅",
  ZONE: "영역",
};

// 종류별 컬럼 정의 — 레퍼런스 표 기준, 캡처 없는 종류는 같은 관점으로 추론
const SPEC: Record<string, { cols: Col[] }> = {
  Deployment: {
    cols: [{ k: "name", label: "NAME", w: "minmax(180px,1.6fr)", cell: { t: "text" } }, { k: "ns", label: "NAMESPACE", w: "150px", cell: { t: "ns" } },
      { k: "ready", label: "READY", w: "72px", cell: { t: "ready" } }, { k: "utd", label: "UP-TO-DATE", w: "96px", cell: { t: "num" } },
      { k: "avail", label: "AVAILABLE", w: "90px", cell: { t: "num" } }, { k: "img", label: "IMAGES", w: "minmax(140px,1fr)", cell: { t: "mono" } }, { k: "age", label: "AGE", w: "60px", cell: { t: "text" } }],
  },
  DaemonSet: {
    cols: [{ k: "name", label: "NAME", w: "minmax(180px,1.6fr)", cell: { t: "text" } }, { k: "ns", label: "NAMESPACE", w: "150px", cell: { t: "ns" } },
      { k: "desired", label: "DESIRED", w: "80px", cell: { t: "num" } }, { k: "ready", label: "READY", w: "72px", cell: { t: "ready" } },
      { k: "utd", label: "UP-TO-DATE", w: "96px", cell: { t: "num" } }, { k: "avail", label: "AVAILABLE", w: "90px", cell: { t: "num" } },
      { k: "img", label: "IMAGES", w: "minmax(140px,1fr)", cell: { t: "mono" } }, { k: "age", label: "AGE", w: "60px", cell: { t: "text" } }],
  },
  StatefulSet: {
    cols: [{ k: "name", label: "NAME", w: "minmax(180px,1.6fr)", cell: { t: "text" } }, { k: "ns", label: "NAMESPACE", w: "150px", cell: { t: "ns" } },
      { k: "ready", label: "READY", w: "72px", cell: { t: "ready" } }, { k: "utd", label: "UP-TO-DATE", w: "96px", cell: { t: "num" } },
      { k: "img", label: "IMAGES", w: "minmax(140px,1fr)", cell: { t: "mono" } }, { k: "age", label: "AGE", w: "60px", cell: { t: "text" } }],
  },
  Pod: {
    cols: [{ k: "name", label: "NAME", w: "minmax(200px,1.8fr)", cell: { t: "text" } }, { k: "ns", label: "NAMESPACE", w: "140px", cell: { t: "ns" } },
      { k: "ctr", label: "CONTAINERS", w: "92px", cell: { t: "dots" } }, { k: "status", label: "STATUS", w: "92px", cell: { t: "status" } },
      { k: "cpu", label: "CPU", w: "128px", cell: { t: "meter" } }, { k: "mem", label: "MEMORY", w: "128px", cell: { t: "meter" } }, { k: "age", label: "AGE", w: "56px", cell: { t: "text" } }],
    // 파드는 맵과 같은 인벤토리에서 파생 — 드릴 맵에 보이는 파드가 곧 이 표의 파드다
    // 정렬도 맵과 동일 규칙: 임계 → 실행 중 → 대기
  },
  ReplicaSet: {
    cols: [{ k: "name", label: "NAME", w: "minmax(200px,1.7fr)", cell: { t: "text" } }, { k: "ns", label: "NAMESPACE", w: "150px", cell: { t: "ns" } },
      { k: "ready", label: "READY", w: "72px", cell: { t: "ready" } }, { k: "owner", label: "OWNER", w: "minmax(150px,1fr)", cell: { t: "mono" } },
      { k: "st", label: "STATUS", w: "76px", cell: { t: "badge", tone: "blue" } }, { k: "age", label: "AGE", w: "56px", cell: { t: "text" } }],
  },
  Job: {
    cols: [{ k: "name", label: "NAME", w: "minmax(220px,2fr)", cell: { t: "text" } }, { k: "ns", label: "NAMESPACE", w: "170px", cell: { t: "ns" } },
      { k: "st", label: "STATUS", w: "92px", cell: { t: "badge", tone: "blue" } }, { k: "comp", label: "COMPLETIONS", w: "108px", cell: { t: "ready" } },
      { k: "dur", label: "DURATION", w: "84px", cell: { t: "text" } }, { k: "age", label: "AGE", w: "56px", cell: { t: "text" } }],
  },
  // 아래 5종은 Kubernetes 표준 출력 컬럼 기준 — 추론이 아니라 정본
  CronJob: {
    cols: [{ k: "name", label: "NAME", w: "minmax(180px,1.6fr)", cell: { t: "text" } }, { k: "ns", label: "NAMESPACE", w: "140px", cell: { t: "ns" } },
      { k: "sched", label: "SCHEDULE", w: "100px", cell: { t: "mono" } }, { k: "tz", label: "TIMEZONE", w: "92px", cell: { t: "text" } },
      { k: "susp", label: "SUSPEND", w: "78px", cell: { t: "text" } }, { k: "active", label: "ACTIVE", w: "68px", cell: { t: "num" } },
      { k: "last", label: "LAST SCHEDULE", w: "104px", cell: { t: "text" } }, { k: "age", label: "AGE", w: "56px", cell: { t: "text" } }],
  },
  Service: {
    cols: [{ k: "name", label: "NAME", w: "minmax(180px,1.5fr)", cell: { t: "text" } }, { k: "ns", label: "NAMESPACE", w: "150px", cell: { t: "ns" } },
      { k: "type", label: "TYPE", w: "92px", cell: { t: "badge", tone: "blue" } }, { k: "sel", label: "SELECTOR", w: "minmax(140px,1fr)", cell: { t: "mono" } },
      { k: "ep", label: "ENDPOINTS", w: "92px", cell: { t: "badge", tone: "green" } }, { k: "ports", label: "PORTS", w: "120px", cell: { t: "mono" } }, { k: "ext", label: "EXTERNAL", w: "76px", cell: { t: "text" } }],
  },
  Ingress: {
    cols: [{ k: "name", label: "NAME", w: "minmax(180px,1.5fr)", cell: { t: "text" } }, { k: "ns", label: "NAMESPACE", w: "150px", cell: { t: "ns" } },
      { k: "class", label: "CLASS", w: "96px", cell: { t: "badge", tone: "gray" } }, { k: "hosts", label: "HOSTS", w: "minmax(140px,1fr)", cell: { t: "mono" } },
      { k: "addr", label: "ADDRESS", w: "130px", cell: { t: "mono" } }, { k: "ports", label: "PORTS", w: "80px", cell: { t: "mono" } }, { k: "age", label: "AGE", w: "56px", cell: { t: "text" } }],
    // 외부 노출은 gateway 하나 — 맵의 gateway 서비스(platform)와 일치
  },
  NetworkPolicy: {
    cols: [{ k: "name", label: "NAME", w: "minmax(220px,1.8fr)", cell: { t: "text" } }, { k: "ns", label: "NAMESPACE", w: "140px", cell: { t: "ns" } },
      { k: "types", label: "TYPES", w: "92px", cell: { t: "text" } }, { k: "sel", label: "POD SELECTOR", w: "minmax(150px,1fr)", cell: { t: "mono" } },
      { k: "rules", label: "RULES", w: "84px", cell: { t: "mono" } }, { k: "age", label: "AGE", w: "56px", cell: { t: "text" } }],
  },
  EndpointSlice: {
    cols: [{ k: "name", label: "NAME", w: "minmax(200px,1.6fr)", cell: { t: "text" } }, { k: "ns", label: "NAMESPACE", w: "150px", cell: { t: "ns" } },
      { k: "at", label: "ADDRESSTYPE", w: "108px", cell: { t: "badge", tone: "gray" } }, { k: "ports", label: "PORTS", w: "110px", cell: { t: "mono" } },
      { k: "ep", label: "ENDPOINTS", w: "minmax(120px,1fr)", cell: { t: "mono" } }, { k: "age", label: "AGE", w: "56px", cell: { t: "text" } }],
    // EndpointSlice는 Service마다 1개 — Service 표와 같은 시드로 파생해 이름·포트가 항상 일치
  },
  ConfigMap: {
    cols: [{ k: "name", label: "NAME", w: "minmax(200px,1.8fr)", cell: { t: "text" } }, { k: "ns", label: "NAMESPACE", w: "180px", cell: { t: "ns" } },
      { k: "keys", label: "KEYS", w: "minmax(160px,1fr)", cell: { t: "mono" } }, { k: "size", label: "SIZE", w: "76px", cell: { t: "text" } }, { k: "age", label: "AGE", w: "56px", cell: { t: "text" } }],
  },
  Secret: {
    cols: [{ k: "name", label: "NAME", w: "minmax(220px,1.8fr)", cell: { t: "text" } }, { k: "ns", label: "NAMESPACE", w: "150px", cell: { t: "ns" } },
      { k: "type", label: "TYPE", w: "104px", cell: { t: "badge", tone: "purple" } }, { k: "keys", label: "KEYS", w: "72px", cell: { t: "num" } },
      { k: "exp", label: "EXPIRES", w: "84px", cell: { t: "text" } }, { k: "age", label: "AGE", w: "56px", cell: { t: "text" } }],
  },
  HPA: {
    cols: [{ k: "name", label: "NAME", w: "minmax(170px,1.5fr)", cell: { t: "text" } }, { k: "ns", label: "NAMESPACE", w: "140px", cell: { t: "ns" } },
      { k: "ref", label: "REFERENCE", w: "minmax(150px,1fr)", cell: { t: "mono" } }, { k: "targets", label: "TARGETS", w: "110px", cell: { t: "mono" } },
      { k: "min", label: "MINPODS", w: "78px", cell: { t: "num" } }, { k: "max", label: "MAXPODS", w: "78px", cell: { t: "num" } },
      { k: "reps", label: "REPLICAS", w: "82px", cell: { t: "num" } }, { k: "age", label: "AGE", w: "56px", cell: { t: "text" } }],
    // HPA 현재 복제 수·사용률은 파드 인벤토리에서 계산 — 맵의 shop-api 파드 수와 일치
  },
  PVC: {
    cols: [{ k: "name", label: "NAME", w: "minmax(160px,1.4fr)", cell: { t: "text" } }, { k: "ns", label: "NAMESPACE", w: "130px", cell: { t: "ns" } },
      { k: "st", label: "STATUS", w: "86px", cell: { t: "badge", tone: "green" } }, { k: "vol", label: "VOLUME", w: "minmax(130px,1fr)", cell: { t: "mono" } },
      { k: "cap", label: "CAPACITY", w: "84px", cell: { t: "text" } }, { k: "am", label: "ACCESS MODES", w: "108px", cell: { t: "mono" } },
      { k: "sc", label: "STORAGECLASS", w: "108px", cell: { t: "badge", tone: "gray" } }, { k: "vac", label: "VOLUMEATTRIBUTESCLASS", w: "150px", cell: { t: "text" } },
      { k: "age", label: "AGE", w: "56px", cell: { t: "text" } }],
  },
  ClusterRole: {
    cols: [{ k: "name", label: "NAME", w: "minmax(260px,3fr)", cell: { t: "text" } }, { k: "rules", label: "RULES", w: "84px", cell: { t: "num" } }, { k: "age", label: "AGE", w: "60px", cell: { t: "text" } }],
  },
  ClusterRoleBinding: {
    cols: [{ k: "name", label: "NAME", w: "minmax(240px,2.4fr)", cell: { t: "text" } }, { k: "role", label: "ROLE", w: "minmax(160px,1.2fr)", cell: { t: "mono" } },
      { k: "subj", label: "SUBJECTS", w: "100px", cell: { t: "mono" } }, { k: "age", label: "AGE", w: "60px", cell: { t: "text" } }],
  },
  Role: {
    cols: [{ k: "name", label: "NAME", w: "minmax(240px,2.4fr)", cell: { t: "text" } }, { k: "ns", label: "NAMESPACE", w: "150px", cell: { t: "ns" } },
      { k: "rules", label: "RULES", w: "84px", cell: { t: "num" } }, { k: "age", label: "AGE", w: "60px", cell: { t: "text" } }],
  },
  RoleBinding: {
    cols: [{ k: "name", label: "NAME", w: "minmax(220px,2.2fr)", cell: { t: "text" } }, { k: "ns", label: "NAMESPACE", w: "140px", cell: { t: "ns" } },
      { k: "role", label: "ROLE", w: "minmax(150px,1.2fr)", cell: { t: "mono" } }, { k: "subj", label: "SUBJECTS", w: "92px", cell: { t: "mono" } }, { k: "age", label: "AGE", w: "56px", cell: { t: "text" } }],
  },
  ServiceAccount: {
    cols: [{ k: "name", label: "NAME", w: "minmax(240px,2.4fr)", cell: { t: "text" } }, { k: "ns", label: "NAMESPACE", w: "150px", cell: { t: "ns" } },
      { k: "auto", label: "AUTOMOUNT", w: "110px", cell: { t: "badge", tone: "gray" } }, { k: "sec", label: "SECRETS", w: "84px", cell: { t: "num" } }, { k: "age", label: "AGE", w: "56px", cell: { t: "text" } }],
  },
  Event: {
    cols: [{ k: "name", label: "NAME", w: "minmax(180px,1.5fr)", cell: { t: "text" } }, { k: "ns", label: "NAMESPACE", w: "120px", cell: { t: "ns" } },
      { k: "type", label: "TYPE", w: "84px", cell: { t: "badge", tone: "green" } }, { k: "reason", label: "REASON", w: "120px", cell: { t: "text" } },
      { k: "msg", label: "MESSAGE", w: "minmax(200px,1.6fr)", cell: { t: "text" } }, { k: "obj", label: "OBJECT", w: "minmax(140px,1fr)", cell: { t: "mono" } }, { k: "cnt", label: "COUNT", w: "64px", cell: { t: "num" } }],
  },
  Namespace: {
    cols: [{ k: "name", label: "NAME", w: "minmax(240px,2.4fr)", cell: { t: "text" } }, { k: "ns", label: "NAMESPACE", w: "140px", cell: { t: "text" } },
      { k: "st", label: "STATUS", w: "92px", cell: { t: "badge", tone: "green" } }, { k: "age", label: "AGE", w: "60px", cell: { t: "text" } }],
  },
  // 추가 종류 — Kubernetes 표준 출력 컬럼
  Endpoints: { cols: [{ k: "name", label: "NAME", w: "minmax(200px,1.8fr)", cell: { t: "text" } }, { k: "ns", label: "NAMESPACE", w: "160px", cell: { t: "ns" } }, { k: "eps", label: "ENDPOINTS", w: "minmax(160px,1fr)", cell: { t: "mono" } }, { k: "age", label: "AGE", w: "56px", cell: { t: "text" } }], },
  PodDisruptionBudget: { cols: [{ k: "name", label: "NAME", w: "minmax(180px,1.6fr)", cell: { t: "text" } }, { k: "ns", label: "NAMESPACE", w: "150px", cell: { t: "ns" } }, { k: "min", label: "MIN AVAILABLE", w: "108px", cell: { t: "mono" } }, { k: "max", label: "MAX UNAVAILABLE", w: "120px", cell: { t: "mono" } }, { k: "dis", label: "ALLOWED DISRUPTIONS", w: "140px", cell: { t: "num" } }, { k: "age", label: "AGE", w: "56px", cell: { t: "text" } }],
    },
  Lease: { cols: [{ k: "name", label: "NAME", w: "minmax(220px,2fr)", cell: { t: "text" } }, { k: "ns", label: "NAMESPACE", w: "160px", cell: { t: "ns" } }, { k: "holder", label: "HOLDER", w: "minmax(160px,1fr)", cell: { t: "mono" } }, { k: "age", label: "AGE", w: "56px", cell: { t: "text" } }], },
  MutatingWebhookConfiguration: { cols: [{ k: "name", label: "NAME", w: "minmax(240px,2.4fr)", cell: { t: "text" } }, { k: "hooks", label: "WEBHOOKS", w: "92px", cell: { t: "num" } }, { k: "age", label: "AGE", w: "56px", cell: { t: "text" } }], },
  ValidatingWebhookConfiguration: { cols: [{ k: "name", label: "NAME", w: "minmax(240px,2.4fr)", cell: { t: "text" } }, { k: "hooks", label: "WEBHOOKS", w: "92px", cell: { t: "num" } }, { k: "age", label: "AGE", w: "56px", cell: { t: "text" } }], },
  PriorityClass: { cols: [{ k: "name", label: "NAME", w: "minmax(220px,2fr)", cell: { t: "text" } }, { k: "value", label: "VALUE", w: "110px", cell: { t: "num" } }, { k: "gd", label: "GLOBAL-DEFAULT", w: "120px", cell: { t: "text" } }, { k: "age", label: "AGE", w: "56px", cell: { t: "text" } }], },
  RuntimeClass: { cols: [{ k: "name", label: "NAME", w: "minmax(220px,2fr)", cell: { t: "text" } }, { k: "handler", label: "HANDLER", w: "minmax(140px,1fr)", cell: { t: "mono" } }, { k: "age", label: "AGE", w: "56px", cell: { t: "text" } }], },
  StorageClass: { cols: [{ k: "name", label: "NAME", w: "minmax(200px,1.8fr)", cell: { t: "text" } }, { k: "prov", label: "PROVISIONER", w: "minmax(150px,1fr)", cell: { t: "mono" } }, { k: "rec", label: "RECLAIMPOLICY", w: "114px", cell: { t: "badge", tone: "gray" } }, { k: "vbm", label: "VOLUMEBINDINGMODE", w: "140px", cell: { t: "text" } }, { k: "age", label: "AGE", w: "56px", cell: { t: "text" } }],
    },
  VolumeAttachment: { cols: [{ k: "name", label: "NAME", w: "minmax(240px,2.4fr)", cell: { t: "text" } }, { k: "att", label: "ATTACHER", w: "minmax(130px,1fr)", cell: { t: "mono" } }, { k: "pv", label: "PV", w: "minmax(130px,1fr)", cell: { t: "mono" } }, { k: "node", label: "NODE", w: "150px", cell: { t: "mono" } }, { k: "age", label: "AGE", w: "56px", cell: { t: "text" } }], },
  Application: { cols: [{ k: "name", label: "NAME", w: "minmax(200px,1.8fr)", cell: { t: "text" } }, { k: "ns", label: "NAMESPACE", w: "120px", cell: { t: "ns" } }, { k: "sync", label: "SYNC", w: "96px", cell: { t: "badge", tone: "green" } }, { k: "health", label: "HEALTH", w: "96px", cell: { t: "badge", tone: "green" } }, { k: "rev", label: "REVISION", w: "100px", cell: { t: "mono" } }, { k: "age", label: "AGE", w: "56px", cell: { t: "text" } }],
    },
  ApplicationSet: { cols: [{ k: "name", label: "NAME", w: "minmax(220px,2fr)", cell: { t: "text" } }, { k: "ns", label: "NAMESPACE", w: "140px", cell: { t: "ns" } }, { k: "gens", label: "GENERATORS", w: "110px", cell: { t: "mono" } }, { k: "age", label: "AGE", w: "56px", cell: { t: "text" } }],
    },
  AppProject: { cols: [{ k: "name", label: "NAME", w: "minmax(220px,2fr)", cell: { t: "text" } }, { k: "ns", label: "NAMESPACE", w: "140px", cell: { t: "ns" } }, { k: "desc", label: "DESCRIPTION", w: "minmax(150px,1fr)", cell: { t: "text" } }, { k: "age", label: "AGE", w: "56px", cell: { t: "text" } }],
    },
  CNINode: { cols: [{ k: "name", label: "NAME", w: "minmax(220px,2fr)", cell: { t: "text" } }, { k: "age", label: "AGE", w: "56px", cell: { t: "text" } }],
    // CNINode는 클러스터에 조인된 노드마다 1개 — 프로비저닝 중인 노드에는 아직 없다
    },
  APIService: { cols: [{ k: "name", label: "NAME", w: "minmax(240px,2.4fr)", cell: { t: "text" } }, { k: "svc", label: "SERVICE", w: "minmax(140px,1fr)", cell: { t: "mono" } }, { k: "avail", label: "AVAILABLE", w: "96px", cell: { t: "badge", tone: "green" } }, { k: "age", label: "AGE", w: "56px", cell: { t: "text" } }],
    },
  Node: {
    cols: [{ k: "name", label: "NAME", w: "minmax(200px,1.6fr)", cell: { t: "text" } }, { k: "st", label: "STATUS", w: "96px", cell: { t: "status" } },
      { k: "inst", label: "INSTANCE", w: "96px", cell: { t: "mono" } }, { k: "cpu", label: "CPU", w: "150px", cell: { t: "meter" } },
      { k: "mem", label: "MEMORY", w: "150px", cell: { t: "meter" } }, { k: "pods", label: "PODS", w: "140px", cell: { t: "meter" } }, { k: "zone", label: "ZONE", w: "76px", cell: { t: "mono" } }],
    // 노드도 맵과 같은 인벤토리 — 맵의 노드 카드와 이 표의 행이 1:1, 정렬도 동일 규칙(가동→예약→차단)
  },
};

// ── 종류 인덱스 (레퍼런스 구조 그대로) ─────────────────────────────
type Kind = { id: string; label: string; icon: typeof Rocket; group: string };
// 그룹·종류·개수는 계약 fixture의 관측 인벤토리와 같은 분류를 사용한다.
const GROUPS = ["워크로드", "네트워킹", "구성", "스토리지", "접근 제어", "클러스터", "ARGO", "AWS VPC CNI", "API 등록"] as const;
const BASE_KINDS: Kind[] = [
  { id: "CronJob", label: "CronJob", icon: Timer, group: "워크로드" },
  { id: "DaemonSet", label: "DaemonSet", icon: LayoutGrid, group: "워크로드" },
  { id: "Deployment", label: "Deployment", icon: Rocket, group: "워크로드" },
  { id: "Job", label: "Job", icon: Play, group: "워크로드" },
  { id: "Pod", label: "Pod", icon: Boxes, group: "워크로드" },
  { id: "ReplicaSet", label: "ReplicaSet", icon: Copy, group: "워크로드" },
  { id: "StatefulSet", label: "StatefulSet", icon: Database, group: "워크로드" },
  { id: "Endpoints", label: "Endpoints", icon: Radio, group: "네트워킹" },
  { id: "EndpointSlice", label: "EndpointSlice", icon: Radio, group: "네트워킹" },
  { id: "Ingress", label: "Ingress", icon: DoorOpen, group: "네트워킹" },
  { id: "NetworkPolicy", label: "NetworkPolicy", icon: ShieldCheck, group: "네트워킹" },
  { id: "Service", label: "Service", icon: Plug, group: "네트워킹" },
  { id: "ConfigMap", label: "ConfigMap", icon: FileCog, group: "구성" },
  { id: "HPA", label: "HorizontalPodAutoscaler", icon: MoveDiagonal, group: "구성" },
  { id: "Lease", label: "Lease", icon: Hourglass, group: "구성" },
  { id: "MutatingWebhookConfiguration", label: "MutatingWebhookConfiguration", icon: Webhook, group: "구성" },
  { id: "PodDisruptionBudget", label: "PodDisruptionBudget", icon: ShieldCheck, group: "구성" },
  { id: "PriorityClass", label: "PriorityClass", icon: SignalHigh, group: "구성" },
  { id: "RuntimeClass", label: "RuntimeClass", icon: Cpu, group: "구성" },
  { id: "Secret", label: "Secret", icon: KeyRound, group: "구성" },
  { id: "ValidatingWebhookConfiguration", label: "ValidatingWebhookConfiguration", icon: Webhook, group: "구성" },
  { id: "PVC", label: "PersistentVolumeClaim", icon: HardDrive, group: "스토리지" },
  { id: "StorageClass", label: "StorageClass", icon: HardDrive, group: "스토리지" },
  { id: "VolumeAttachment", label: "VolumeAttachment", icon: HardDrive, group: "스토리지" },
  { id: "ClusterRole", label: "ClusterRole", icon: ShieldCheck, group: "접근 제어" },
  { id: "ClusterRoleBinding", label: "ClusterRoleBinding", icon: ShieldCheck, group: "접근 제어" },
  { id: "Role", label: "Role", icon: ShieldCheck, group: "접근 제어" },
  { id: "RoleBinding", label: "RoleBinding", icon: ShieldCheck, group: "접근 제어" },
  { id: "ServiceAccount", label: "ServiceAccount", icon: UserCog, group: "접근 제어" },
  { id: "Event", label: "Event", icon: Activity, group: "클러스터" },
  { id: "Namespace", label: "Namespace", icon: Folder, group: "클러스터" },
  { id: "Node", label: "Node", icon: Cpu, group: "클러스터" },
  { id: "Application", label: "Application", icon: Rocket, group: "ARGO" },
  { id: "ApplicationSet", label: "ApplicationSet", icon: Copy, group: "ARGO" },
  { id: "AppProject", label: "AppProject", icon: Folder, group: "ARGO" },
  { id: "CNINode", label: "CNINode", icon: Network, group: "AWS VPC CNI" },
  { id: "APIService", label: "APIService", icon: Plug, group: "API 등록" },
];
// 사이드바 카운트는 라이브 인벤토리 요약에서 파생(useInventoryKindCounts) —
// 모듈 로드 시 가짜 count를 만들지 않는다. 렌더 시점에 counts 맵을 주입한다.
const KINDS: Kind[] = BASE_KINDS;
export function resolveTrafficResourceKindId(kind: string): string | null {
  return KINDS.find((item) => item.id.toLowerCase() === kind.toLowerCase())?.id ?? null;
}
const GROUP_TOTAL = (g: string, counts: Record<string, number>) =>
  KINDS.filter((k) => k.group === g).reduce((s, k) => s + (counts[k.id] ?? 0), 0);

// ── 셀 렌더러 ─────────────────────────────
function CellView({ cell, v, bad }: { cell: Cell; v: unknown; bad?: boolean }) {
  // 계약이 노출하지 않는 필드는 값이 없다 — "undefined"/NaN 대신 관측 안 됨("–")을 낸다.
  if (v === undefined || v === null || v === "") {
    return <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>–</span>;
  }
  if (cell.t === "meter") {
    const m = v as { used: string; lim: string; pct: number };
    const c = m.pct >= 90 ? HP.crit : m.pct >= 60 ? HP.warn : HP.ok;
    return (
      <span style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0 }}>
        <span style={{ fontSize: TYPE.caption, fontVariantNumeric: "tabular-nums", color: UI.ink2, whiteSpace: "nowrap" }}>{m.used} / {m.lim} <span style={{ color: UI.ink3 }}>{m.pct}%</span></span>
        <span style={{ height: 3, borderRadius: 999, background: inkA(0.07), overflow: "hidden" }}>
          <span style={{ display: "block", height: "100%", width: `${m.pct}%`, background: c, borderRadius: 999 }} />
        </span>
      </span>
    );
  }
  if (cell.t === "dots") {
    const n = v as number;
    return <span style={{ display: "flex", gap: 3 }}>{Array.from({ length: n }).map((_, i) => <span key={i} style={{ width: 7, height: 7, borderRadius: 999, background: bad && i === 0 ? HP.crit : i === 0 && n > 1 ? HP.pending : HP.ok }} />)}</span>;
  }
  if (cell.t === "ready") return <span style={{ fontSize: TYPE.label, fontVariantNumeric: "tabular-nums", fontWeight: 600, color: bad ? HP.crit : TINT.ok.fg }}>{String(v)}</span>;
  if (cell.t === "status") {
    const s = String(v);
    const tone = bad || isCriticalStatus(s) || /Crash|OOM|Fail|Error|Evict/.test(s) ? "red" : /Pending|Provisioning/.test(s) ? "blue" : /Cordoned|Suspend|Terminat/.test(s) ? "gray" : "green";
    return <Badge text={statusLabel(s)} tone={tone} />;
  }
  if (cell.t === "badge") return <Badge text={statusLabel(String(v))} tone={bad ? "red" : cell.tone ?? "gray"} />;
  if (cell.t === "num") return <span style={{ fontSize: TYPE.label, fontVariantNumeric: "tabular-nums", color: UI.ink2 }}>{String(v)}</span>;
  if (cell.t === "ns") return <span style={{ fontSize: TYPE.label, color: UI.ink2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{String(v)}</span>;
  if (cell.t === "mono") return <span style={{ fontSize: TYPE.label, fontFamily: MONO, color: UI.ink3, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{String(v)}</span>;
  return <span style={{ fontSize: TYPE.label, color: UI.ink2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{String(v)}</span>;
}
function Badge({ text, tone }: { text: string; tone: "blue" | "green" | "gray" | "purple" | "red" }) {
  const S = { blue: [TINT.blue.fg, TINT.blue.bg, TINT.blue.bd], green: [TINT.ok.fg, TINT.ok.bg, TINT.ok.bd], gray: [UI.ink2, TINT.gray.bg, TINT.gray.bd], purple: [TINT.purple.fg, TINT.purple.bg, TINT.purple.bd], red: [TINT.crit.fg, TINT.crit.bg, TINT.crit.bd] }[tone];
  return <span style={{ display: "inline-block", fontSize: TYPE.caption, fontWeight: 600, color: S[0], background: S[1], border: `1px solid ${S[2]}`, borderRadius: 5, padding: "1.5px 7px", whiteSpace: "nowrap" }}>{text}</span>;
}

// ── 종류별 표 ─────────────────────────────
// 검색어 매치 하이라이트 — 무엇이 걸렸는지 눈으로 바로 보인다
function Hi({ text, q }: { text: string; q: string }) {
  if (!q) return <>{text}</>;
  const i = text.toLowerCase().indexOf(q.toLowerCase());
  if (i < 0) return <>{text}</>;
  return <>{text.slice(0, i)}<span style={{ background: MARK, borderRadius: 3, padding: "0 1px" }}>{text.slice(i, i + q.length)}</span>{text.slice(i + q.length)}</>;
}

function ResourceTable({ kind, rows, q, filterDesc = "", onClearFilter, onOpen }: { kind: Kind; rows: Row[]; q: string; filterDesc?: string; onClearFilter?: () => void; onOpen: (r: Row) => void }) {
  const spec = SPEC[kind.id];
  if (!spec) return null;
  const filtered = rows;
  /* 확대/좁은 화면에서도 모든 열이 카드 안에 남도록 최소 폭을 0으로 열어 둔다.
     셀과 헤더는 말줄임 처리되어 인접 열의 텍스트를 침범하지 않는다. */
  const grid = spec.cols.map((c) => {
    const width = c.w ?? "1fr";
    if (width.endsWith("px")) {
      const proportionalWidth = Math.max(0.5, Number.parseFloat(width) / 100);
      return `minmax(0, ${proportionalWidth}fr)`;
    }
    return width.replace(/^minmax\(\d+px,/, "minmax(0,");
  }).join(" ");
  return (
    /* 표는 셸 본문과 함께 스크롤한다. 카드 내부에 두 번째 세로 스크롤을 만들지 않는다. */
    <div data-resource-table="true" style={{ background: UI.card, border: `1px solid ${UI.line}`, borderRadius: RADIUS.card, overflow: "visible" }}>
    <div>
      <div data-resource-table-header="true" style={{ display: "grid", gridTemplateColumns: grid, gap: 14, padding: "11px 16px", borderBottom: `1px solid ${UI.line}`, background: UI.bg2, borderRadius: `${RADIUS.card}px ${RADIUS.card}px 0 0`, position: "sticky", top: RESOURCE_VIEW_STICKY_TOP, zIndex: 6, boxShadow: `0 5px 10px -10px ${inkA(0.35)}` }}>
        {/* 정렬 미구현 — 동작 없는 정렬 셰브론을 그리지 않는다(가짜 컨트롤 금지) */}
        {spec.cols.map((c) => (
          <span key={c.k} title={COLUMN_LABEL_KO[c.label] ?? c.label} style={{ display: "block", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", fontSize: TYPE.caption, fontWeight: 600, letterSpacing: "0.05em", color: UI.ink3, whiteSpace: "nowrap" }}>
            {COLUMN_LABEL_KO[c.label] ?? c.label}
          </span>
        ))}
      </div>
      {filtered.length === 0 ? (
        <div style={{ padding: "40px 18px", textAlign: "center", fontSize: TYPE.body, color: UI.ink3 }}>
          <span>{q ? `"${q}" 검색 결과가 없습니다` : `${filterDesc} ${kind.label} 리소스가 없습니다`}</span>
          {onClearFilter && (q || filterDesc) ? (
            <button className="product-focusable product-control" onClick={onClearFilter} style={{ display: "block", margin: "10px auto 0", border: `1px solid ${UI.line}`, background: UI.card, borderRadius: 8, padding: "5px 12px", fontSize: TYPE.label, fontWeight: 600, color: BLUE, cursor: "pointer" }}>필터 해제</button>
          ) : null}
        </div>
      ) : filtered.map((row, i) => (
        <motion.div key={`${kind.id}-${String(row.cluster ?? "")}-${String(row._key ?? row.name)}`} role="button" tabIndex={0} aria-label={`${kind.label} ${String(row.name ?? row._key ?? "")}`} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onOpen(row); } }} initial={{ opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SOFT, delay: Math.min(i, 10) * 0.022 }}
          className="rrow" onClick={() => onOpen(row)} style={{ display: "grid", gridTemplateColumns: grid, gap: 14, alignItems: "center", padding: "12px 16px", borderTop: i ? `1px solid ${UI.line2}` : "none", cursor: "pointer" }}>
          {spec.cols.map((c, ci) => (
            <span key={c.k} style={{ minWidth: 0, fontWeight: ci === 0 ? 600 : 400, color: ci === 0 ? UI.ink : undefined, fontSize: ci === 0 ? 12 : undefined, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: ci === 0 ? "nowrap" : undefined }}>
              {ci === 0 ? <Hi text={String(row[c.k] ?? "")} q={q} /> : <CellView cell={c.cell} v={row[c.k]} bad={row.bad as boolean} />}
            </span>
          ))}
        </motion.div>
      ))}
    </div>
    </div>
  );
}

// ── 상세 오버레이 (최상위 레이어) ─────────────────────────────
// 탭 구성은 리소스 상세 드로어 기준: 개요 · YAML · 관련 리소스 · 이벤트 · 로그 · 권한(RBAC)
const DETAIL_TABS = [
  { id: "overview", label: "개요" }, { id: "yaml", label: "YAML" }, { id: "events", label: "이벤트" }, { id: "logs", label: "로그" }, { id: "rbac", label: "권한" },
] as const;
type DetailTab = (typeof DETAIL_TABS)[number]["id"];
export const TABS_FOR = (kindId: string): DetailTab[] => {
  if (kindId === "Pod") return ["overview", "yaml", "events", "logs", "rbac"];
  if (["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job", "CronJob"].includes(kindId)) return ["overview", "yaml", "events", "logs", "rbac"];
  if (["ServiceAccount", "Role", "ClusterRole", "RoleBinding", "ClusterRoleBinding", "Namespace"].includes(kindId)) return ["overview", "yaml", "events", "rbac"];
  if (kindId === "Node") return ["overview", "yaml", "events"];
  return ["overview", "yaml", "events"];
};

// 섹션 래퍼 — 접기 가능한 리소스 상세 드로어 구조
function Sec({ title, icon: I, right, children, defaultOpen = true }: { title: string; icon?: typeof Rocket; right?: React.ReactNode; children: React.ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section style={{ borderBottom: `1px solid ${UI.line2}`, padding: "14px 0" }}>
      <button className="product-focusable product-control" aria-expanded={open} onClick={() => setOpen(!open)} style={{ display: "flex", alignItems: "center", gap: 7, width: "100%", border: "none", background: "transparent", cursor: "pointer", padding: 0, marginBottom: open ? 10 : 0 }}>
        <ChevronDown size={12} style={{ color: UI.ink3, transform: open ? "none" : "rotate(-90deg)", transition: "transform .15s" }} />
        {I && <I size={12} style={{ color: UI.ink3 }} />}
        <span style={{ fontSize: TYPE.body, fontWeight: 600, color: UI.heading }}>{title}</span>
        <span style={{ marginLeft: "auto" }}>{right}</span>
      </button>
      {open && children}
    </section>
  );
}
// Chip 제거 — 합성 레이블/RBAC 규칙 칩을 렌더하던 DetailOverlay 섹션이 "관측 안 됨"으로 바뀌며 미사용.

function KV({ k, v, mono, tone }: { k: string; v: string; mono?: boolean; tone?: string }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "132px 1fr", gap: 12, padding: "7px 0", borderBottom: `1px solid ${UI.line2}` }}>
      <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>{k}</span>
      <span style={{ fontSize: TYPE.label, color: tone ?? UI.ink, fontFamily: mono ? MONO : undefined, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{v}</span>
    </div>
  );
}

// 관측 계약이 없는 섹션의 일관된 빈 상태 — 지어내지 않고 정직하게 비운다(원본의 깔끔한 empty-state 톤).
function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, border: `1px dashed ${UI.line}`, background: UI.bg2, borderRadius: 10, padding: "10px 12px", fontSize: TYPE.label, color: UI.ink3, lineHeight: 1.5 }}>
      <Eye size={13} style={{ color: UI.ink3, flexShrink: 0 }} />
      <span>{children}</span>
    </div>
  );
}


// RelGraph/TINT_G 제거 — 합성 소유·참조 관계도(rs/deploy/svc/pod/cm/netpol 이름 지어내기)를
// 렌더하던 DetailOverlay '관련 리소스' 섹션이 "관측 안 됨"으로 바뀌며 미사용.
// NS_OPTIONS 하드코딩 목록 제거 — 네임스페이스 셀렉트는 useInventoryNamespaces 관측 네임스페이스로 구동.

const TOPBAR_H = 57; // 상단 크롬 높이 — 오버레이는 이 아래부터 시작한다

type ResourceMetricKey = "cpu" | "memory";

const RESOURCE_METRICS = {
  cpu: {
    label: "CPU",
    value: (point: ResourceUsagePoint) => point.cpuMcores,
    unit: "mcores",
  },
  memory: {
    label: "메모리",
    value: (point: ResourceUsagePoint) => point.memMib,
    unit: "MiB",
  },
} satisfies Record<ResourceMetricKey, {
  label: string;
  value: (point: ResourceUsagePoint) => number | null;
  unit: string;
}>;

function metricValue(value: number, unit: string): string {
  const digits = value >= 100 ? 0 : value >= 10 ? 1 : 2;
  return `${value.toFixed(digits)} ${unit}`;
}

function metricTime(value: string | null): string {
  if (!value) return "관측 시각 없음";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "관측 시각 없음";
  return new Intl.DateTimeFormat("ko-KR", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

// 실제 표본만 시각화한다. null 표본은 선을 끊고, 임의 보간이나 합성값은 만들지 않는다.
function ResourceMetricsChart({ points }: { points: ResourceUsagePoint[] }) {
  const [selected, setSelected] = useState<ResourceMetricKey>("cpu");
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const gradientId = useId().replace(/:/g, "");
  const metric = RESOURCE_METRICS[selected];
  const values = useMemo(() => points.map(metric.value), [metric, points]);
  const observed = useMemo(
    () => values.flatMap((value, index) => value === null ? [] : [{ value, index }]),
    [values],
  );
  const W = 520;
  const H = 176;
  const PT = 14;
  const PB = 28;
  const PL = 48;
  const PR = 14;
  const chartW = W - PL - PR;
  const chartH = H - PT - PB;
  const rawMin = observed.length > 0 ? Math.min(...observed.map((point) => point.value)) : 0;
  const rawMax = observed.length > 0 ? Math.max(...observed.map((point) => point.value)) : 0;
  const padding = rawMax === rawMin ? Math.max(rawMax * 0.12, 1) : (rawMax - rawMin) * 0.12;
  const min = Math.max(0, rawMin - padding);
  const max = Math.max(rawMax + padding, min + 1);
  const x = useCallback(
    (index: number) => PL + (values.length <= 1 ? 0 : index / (values.length - 1) * chartW),
    [chartW, values.length],
  );
  const y = useCallback(
    (value: number) => PT + (1 - (value - min) / (max - min)) * chartH,
    [chartH, max, min],
  );
  const paths = useMemo(() => {
    const result: string[] = [];
    let current = "";
    values.forEach((value, index) => {
      if (value === null) {
        if (current) result.push(current.trim());
        current = "";
        return;
      }
      current += `${current ? "L" : "M"} ${x(index).toFixed(2)} ${y(value).toFixed(2)} `;
    });
    if (current) result.push(current.trim());
    return result;
  }, [values, x, y]);
  const gapPaths = useMemo(() => {
    const result: string[] = [];
    for (let index = 1; index < observed.length; index += 1) {
      const previous = observed[index - 1]!;
      const current = observed[index]!;
      if (current.index - previous.index <= 1) continue;
      result.push(
        `M ${x(previous.index).toFixed(2)} ${y(previous.value).toFixed(2)} `
        + `L ${x(current.index).toFixed(2)} ${y(current.value).toFixed(2)}`,
      );
    }
    return result;
  }, [observed, x, y]);
  const hovered = hoverIndex === null || values[hoverIndex] === null
    ? null
    : { index: hoverIndex, value: values[hoverIndex] as number };
  const current = observed.length > 0 ? observed[observed.length - 1] : undefined;
  const average = observed.length > 0
    ? observed.reduce((sum, point) => sum + point.value, 0) / observed.length
    : null;
  const peak = observed.length > 0 ? rawMax : null;
  const observedAt = hovered
    ? points[hovered.index]?.sampledAt ?? null
    : current ? points[current.index]?.sampledAt ?? null : null;
  const metricCounts = {
    cpu: points.filter((point) => point.cpuMcores !== null).length,
    memory: points.filter((point) => point.memMib !== null).length,
  };
  const displayValue = hovered?.value ?? current?.value ?? null;
  const hoveredX = hovered ? x(hovered.index) : 0;
  const hoveredY = hovered ? y(hovered.value) : 0;
  const tooltipWidth = 126;
  const tooltipHeight = 38;
  const tooltipX = hovered
    ? Math.max(PL + 4, Math.min(hoveredX + 8, W - PR - tooltipWidth))
    : 0;
  const tooltipY = hovered
    ? hoveredY - tooltipHeight - 8 >= PT
      ? hoveredY - tooltipHeight - 8
      : hoveredY + 10
    : 0;

  return (
    <div>
      <div role="tablist" aria-label="메트릭 종류" style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 3, padding: 3, borderRadius: RADIUS.control, background: UI.bg2, marginBottom: 14 }}>
        {(Object.keys(RESOURCE_METRICS) as ResourceMetricKey[]).map((key) => {
          const active = selected === key;
          return (
            <button
              type="button"
              role="tab"
              aria-selected={active}
              className="product-focusable product-control"
              key={key}
              onClick={() => { setSelected(key); setHoverIndex(null); }}
              style={{
                border: "none",
                borderRadius: RADIUS.control - 2,
                background: active ? UI.card : "transparent",
                boxShadow: active ? `0 1px 3px ${inkA(0.12)}` : "none",
                color: active ? UI.ink : UI.ink3,
                cursor: "pointer",
                fontSize: TYPE.label,
                fontWeight: active ? 700 : 600,
                padding: "7px 10px",
              }}
            >
              {RESOURCE_METRICS[key].label}
              <span style={{ marginLeft: 5, color: active ? UI.ink3 : inkA(0.32), fontWeight: 500 }}>
                {metricCounts[key]}/{points.length}
              </span>
            </button>
          );
        })}
      </div>

      {observed.length === 0 ? (
        <Empty>{metric.label} 시계열이 관측되지 않았습니다.</Empty>
      ) : (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 12, marginBottom: 10 }}>
            {([
              [hovered ? "선택" : "현재", displayValue],
              ["평균", average],
              ["최대", peak],
            ] as const).map(([label, value]) => (
              <div key={label}>
                <div style={{ fontSize: TYPE.caption, color: UI.ink3, marginBottom: 2 }}>{label}</div>
                <div style={{ color: label === "현재" || label === "선택" ? BLUE : UI.ink, fontSize: TYPE.body, fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>
                  {value === null ? "관측 안 됨" : metricValue(value, metric.unit)}
                </div>
              </div>
            ))}
          </div>

          <div style={{ border: `1px solid ${UI.line2}`, borderRadius: RADIUS.card, background: UI.bg2, padding: "6px 4px 2px" }}>
            <svg
              viewBox={`0 0 ${W} ${H}`}
              width="100%"
              role="img"
              aria-label={`${metric.label} 사용량 추이`}
              style={{ display: "block", cursor: "crosshair" }}
              onMouseMove={(event) => {
                const rect = event.currentTarget.getBoundingClientRect();
                const cursor = (event.clientX - rect.left) / rect.width * W;
                const targetIndex = Math.max(
                  0,
                  Math.min(
                    values.length - 1,
                    Math.round((cursor - PL) / chartW * Math.max(values.length - 1, 0)),
                  ),
                );
                const nearest = observed.reduce(
                  (candidate, point) => (
                    candidate === null
                    || Math.abs(point.index - targetIndex) < Math.abs(candidate.index - targetIndex)
                      ? point
                      : candidate
                  ),
                  null as { value: number; index: number } | null,
                );
                setHoverIndex(nearest?.index ?? null);
              }}
              onMouseLeave={() => setHoverIndex(null)}
            >
              <defs>
                <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={BLUE} stopOpacity="0.18" />
                  <stop offset="100%" stopColor={BLUE} stopOpacity="0.02" />
                </linearGradient>
              </defs>
              {[0, 0.5, 1].map((ratio) => {
                const lineY = PT + ratio * chartH;
                const lineValue = max - ratio * (max - min);
                return (
                  <g key={ratio}>
                    <line x1={PL} x2={W - PR} y1={lineY} y2={lineY} stroke={UI.line} strokeDasharray="3 5" />
                    <text x={PL - 7} y={lineY + 3.5} textAnchor="end" fontSize="9.5" fill={UI.ink3} fontFamily={MONO}>
                      {lineValue.toFixed(lineValue >= 100 ? 0 : 1)}
                    </text>
                  </g>
                );
              })}
              {paths.map((path, index) => (
                <motion.path
                  key={`${selected}-${index}`}
                  initial={{ pathLength: 0, opacity: 0.4 }}
                  animate={{ pathLength: 1, opacity: 1 }}
                  transition={{ duration: 0.55, ease: "easeOut" }}
                  d={path}
                  fill="none"
                  stroke={BLUE}
                  strokeWidth={2}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                  vectorEffect="non-scaling-stroke"
                />
              ))}
              {gapPaths.map((path, index) => (
                <motion.path
                  key={`${selected}-gap-${index}`}
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ duration: 0.35, delay: 0.15 }}
                  d={path}
                  fill="none"
                  stroke={BLUE}
                  strokeDasharray="4 5"
                  strokeOpacity={0.42}
                  strokeWidth={1.5}
                  vectorEffect="non-scaling-stroke"
                />
              ))}
              {observed.map((point) => (
                <circle
                  key={`${selected}-point-${point.index}`}
                  cx={x(point.index)}
                  cy={y(point.value)}
                  r={1.8}
                  fill={BLUE}
                  opacity={0.74}
                />
              ))}
              {hovered && (
                <g>
                  <line x1={hoveredX} x2={hoveredX} y1={PT} y2={H - PB} stroke={UI.ink3} strokeDasharray="2 3" />
                  <circle cx={hoveredX} cy={hoveredY} r={4} fill={UI.card} stroke={BLUE} strokeWidth={2} />
                  <rect
                    x={tooltipX}
                    y={tooltipY}
                    width={tooltipWidth}
                    height={tooltipHeight}
                    rx={6}
                    fill={UI.card}
                    stroke={UI.line2}
                  />
                  <text
                    x={tooltipX + 9}
                    y={tooltipY + 15}
                    fontSize="10"
                    fontWeight="700"
                    fill={UI.ink}
                    fontFamily={MONO}
                  >
                    {metricValue(hovered.value, metric.unit)}
                  </text>
                  <text
                    x={tooltipX + 9}
                    y={tooltipY + 29}
                    fontSize="9"
                    fill={UI.ink3}
                  >
                    {metricTime(points[hovered.index]?.sampledAt ?? null)}
                  </text>
                </g>
              )}
              <text x={PL} y={H - 8} textAnchor="start" fontSize="9.5" fill={UI.ink3}>
                {metricTime(points[0]?.sampledAt ?? null)}
              </text>
              <text x={W - PR} y={H - 8} textAnchor="end" fontSize="9.5" fill={UI.ink3}>
                {metricTime(points.length > 0 ? points[points.length - 1]?.sampledAt ?? null : null)}
              </text>
            </svg>
          </div>

          <div style={{ display: "flex", justifyContent: "space-between", gap: 12, marginTop: 7, fontSize: TYPE.caption, color: UI.ink3 }}>
            <span>
              표본 {observed.length}/{points.length}
              {observed.length < points.length ? " · 부분 관측" : ""}
            </span>
            <span style={{ fontVariantNumeric: "tabular-nums" }}>{metricTime(observedAt)}</span>
          </div>
          {observed.length < points.length && (
            <div style={{ marginTop: 5, fontSize: TYPE.caption, color: UI.ink3 }}>
              점선은 관측되지 않은 구간 사이의 추세 연결입니다.
            </div>
          )}
        </>
      )}
    </div>
  );
}
// RetryNote — 일시적 오류(요청 실패)를 "관측 안 됨(데이터 없음)"과 구분해 표시하고
// 재조회 버튼을 제공한다. 오류를 unavailable로 위장하지 않는다(M13/M14/M16).
function RetryNote({ onRetry, label }: { onRetry: () => void; label?: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "12px 0", flexWrap: "wrap" }}>
      <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>{label ?? "일시적 오류로 관측값을 불러오지 못했습니다."}</span>
      <button type="button" className="product-focusable product-control" onClick={onRetry} style={{ border: `1px solid ${UI.line}`, background: UI.card, color: BLUE, borderRadius: 8, padding: "5px 11px", fontSize: TYPE.label, fontWeight: 600, cursor: "pointer" }}>다시 시도</button>
    </div>
  );
}

// hlYaml/YAML_FONT 제거 — 합성 YAML 매니페스트를 렌더하던 DetailOverlay YAML 탭이
// 관측 전용("관측 안 됨")으로 바뀌면서 더 이상 쓰이지 않는다.

function DetailOverlay({ kind, row, onClose, onOpenRef: _onOpenRef, onShowPods, onConnectRepository, onRequestManifestAccess, onOpenDeploySurface, manifestRefreshKey = 0, forceFull = false, rightInset = 0, leftInset = 0, topInset = TOPBAR_H, viewportW = 1280 }: { kind: Kind; row: Row; onClose: () => void; onToast?: (t: { title: string; sub: string; tone: "ok" | "crit" }) => void; onOpenRef?: (kindId: string, name: string) => void; onShowPods?: (base: string) => void; onConnectRepository?: (context: RepositoryConnectionContext) => void; onRequestManifestAccess?: () => void; onOpenDeploySurface?: () => void; manifestRefreshKey?: number; forceFull?: boolean; rightInset?: number; leftInset?: number; topInset?: number; viewportW?: number }) {
  const tabs = TABS_FOR(kind.id);
  const [tab, setTab] = useState<DetailTab>(tabs[0]);
  const [fullSelf, setFull] = useState(false);   // 전체 화면 (원본 레퍼런스의 ⤢)
  const [yamlEditorOpen, setYamlEditorOpen] = useState(false);
  const [yamlEditorExpanded, setYamlEditorExpanded] = useState(false);
  const [yamlEditorWidth, setYamlEditorWidth] = useState(() =>
    Math.max(380, Math.min(560, (viewportW - leftInset - rightInset) * 0.52))
  );
  const [yamlEditorDragging, setYamlEditorDragging] = useState(false);
  const yamlEditorDragCleanupRef = useRef<() => void>(() => undefined);
  const [manifestSource, setManifestSource] = useState<ResourceManifestSourceEndpoint | null>(null);
  // AI 또는 YAML 보조 편집 패널이 열리면 읽기 전용 상세도 남은 영역을 채운다.
  // 편집 패널로 교체하지 않고 두 화면을 나란히 비교하기 위한 확장이다.
  const full = forceFull || fullSelf || yamlEditorOpen;
  const yamlEditorAvailableWidth = Math.max(
    0,
    viewportW - leftInset - rightInset,
  );
  const yamlEditorRenderedWidth = yamlEditorExpanded
    ? yamlEditorAvailableWidth
    : clampSidePanelWidth(
        yamlEditorWidth,
        380,
        yamlEditorAvailableWidth,
      );
  const closeYamlEditor = useCallback(() => {
    setYamlEditorOpen(false);
    setYamlEditorExpanded(false);
  }, [setYamlEditorExpanded, setYamlEditorOpen]);
  useEffect(
    () => () => yamlEditorDragCleanupRef.current(),
    [],
  );
  useEffect(() => {
    if (!forceFull) return;
    const timer = window.setTimeout(closeYamlEditor, 0);
    return () => window.clearTimeout(timer);
  }, [closeYamlEditor, forceFull]);
  const switchTab = (next: DetailTab) => {
    if (next !== "yaml") closeYamlEditor();
    setTab(next);
  };
  const onYamlEditorEdgeDown = (event: React.PointerEvent) => {
    if (yamlEditorExpanded) return;
    event.preventDefault();
    setYamlEditorDragging(true);
    const move = (pointerEvent: PointerEvent) => {
      const nextWidth =
        viewportW - rightInset - pointerEvent.clientX / PRESENT_SCALE;
      setYamlEditorWidth(
        clampSidePanelWidth(nextWidth, 380, yamlEditorAvailableWidth),
      );
    };
    const cleanup = () => {
      setYamlEditorDragging(false);
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", cleanup);
      yamlEditorDragCleanupRef.current = () => undefined;
    };
    yamlEditorDragCleanupRef.current();
    yamlEditorDragCleanupRef.current = cleanup;
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", cleanup);
  };
  const onYamlEditorEdgeKeyDown = (event: React.KeyboardEvent) => {
    const nextWidth = sidePanelWidthFromKeyboard({
      currentWidth: yamlEditorWidth,
      key: event.key,
      maximumWidth: yamlEditorAvailableWidth,
      minimumWidth: 380,
      shiftKey: event.shiftKey,
    });
    if (nextWidth === null) return;
    event.preventDefault();
    setYamlEditorWidth(nextWidth);
  };
  const name = String(row.name ?? "");
  const ns = String(row.ns ?? "–");
  const bad = !!row.bad;
  // Pod placement/network facts come from resource-detail summary; unsupported fields stay honest.
  const phase = row.status != null && String(row.status) ? statusLabel(String(row.status)) : "관측 안 됨";
  const healthVal = row.health != null && String(row.health) ? statusLabel(String(row.health)) : "관측 안 됨";
  const clusterVal = row.cluster != null && String(row.cluster) ? String(row.cluster) : "관측 안 됨";
  const isPodKind = kind.id === "Pod";
  const isNodeKind = kind.id === "Node";
  const observedResourceId = row._key != null ? String(row._key) : "";
  const podResourceDetail = usePodResourceDetail(
    isPodKind,
    row.cluster != null && String(row.cluster) ? String(row.cluster) : null,
    row.ns != null && String(row.ns) ? String(row.ns) : null,
    name,
  );
  const resolvedIdentity = useResourceIdentity(
    observedResourceId === "",
    row.cluster != null && String(row.cluster) ? String(row.cluster) : null,
    row.resource_type != null && String(row.resource_type) ? String(row.resource_type) : kindToResourceType(kind.id),
    kind.id,
    row.ns != null && String(row.ns) ? String(row.ns) : null,
    name,
  );
  const resourceId = observedResourceId || resolvedIdentity.resourceId;
  // M13: 워크로드 상세는 `GET /api/workloads/{kind}/{ns}/{name}`로 실제 replicas·health·
  // labels·pods를 관측한다(이전 "계약 없음" 오판 교정). 미지원 kind/빈 스코프는 idle이라
  // 기존 honest 일반 뷰가 그대로 유지된다.
  const wd = useWorkloadDetail(
    row.cluster != null && String(row.cluster) ? String(row.cluster) : null,
    kind.id,
    row.ns != null && String(row.ns) ? String(row.ns) : null,
    name,
  );
  // M14: 파드·노드 상세는 `GET /api/clusters/{id}/usage`에서 관측된 CPU/메모리
  // 시계열을 표시한다. 지원하지 않는 kind이거나 스코프가 비면 요청하지 않는다.
  const supportsUsageSeries = isPodKind || isNodeKind;
  const usage = useResourceUsageSeries(
    supportsUsageSeries && row.cluster != null && String(row.cluster) ? String(row.cluster) : null,
    isNodeKind ? "node" : "pod",
    isPodKind && row.ns != null && String(row.ns) ? String(row.ns) : null,
    supportsUsageSeries ? name : "",
  );
  // M18: 실제 retained RBAC reverse-index 계약. ServiceAccount/Role은 정확한 주체·역할을,
  // 워크로드·파드는 리소스별 권한으로 꾸미지 않고 해당 네임스페이스 요약을 조회한다.
  const access = useResourceAccess(
    tabs.includes("rbac") && row.cluster != null && String(row.cluster) ? String(row.cluster) : null,
    kind.id,
    row.ns != null && String(row.ns) ? String(row.ns) : null,
    name,
  );
  const fallbackResourceEvents = useResourceEvents(
    !isPodKind && tab === "events" && row.cluster != null && String(row.cluster) ? String(row.cluster) : null,
    kind.id,
    row.ns != null && String(row.ns) ? String(row.ns) : null,
    name,
  );
  const resourceEvents = isPodKind ? podResourceDetail.events : fallbackResourceEvents;
  // 재시작/스케일처럼 이 드로어가 지원하지 않는 변경 컨트롤은 노출하지 않는다.
  // YAML 변경은 source/content SHA를 고정하고 권한·CSRF·감사 계약을 거치는 전용 편집기에서만 수행한다.
  const isWorkload = ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job", "CronJob"].includes(kind.id);
  const isPod = isPodKind;
  const wp = isWorkload || isPod;                 // 워크로드·파드 전용 섹션
  const resourceConditions = useResourceConditions(
    tab === "overview" && !isPodKind && isWorkload,
    row.cluster != null && String(row.cluster) ? String(row.cluster) : null,
    row.resource_type != null && String(row.resource_type) ? String(row.resource_type) : kindToResourceType(kind.id),
    kind.id,
    row.ns != null && String(row.ns) ? String(row.ns) : null,
    name,
  );
  const podConditions = useMemo<ResourceConditionsView>(() => ({
    ...EMPTY_RESOURCE_CONDITIONS_VIEW,
    status: podResourceDetail.status,
    primary: podResourceDetail.summary.conditions,
    retry: podResourceDetail.retry,
  }), [podResourceDetail.retry, podResourceDetail.status, podResourceDetail.summary.conditions]);
  const conditionView = isPodKind ? podConditions : resourceConditions;
  const operationalCause = useMemo(
    () => projectPodOperationalCause(
      { conditions: conditionView.primary },
      resourceEvents.items,
    ),
    [conditionView.primary, resourceEvents.items],
  );
  const operationalCauseStatus = isPodKind
    ? podResourceDetail.status
    : conditionView.status;
  // 합성 YAML은 만들지 않는다. 실제 인벤토리 key로 원본을 조회하고, 원본이 없는 리소스는
  // Git 바인딩 누락 상태를 명시해 잘못된 매니페스트를 편집·적용하지 않도록 한다.

  return (
    <>
      <DetailDrawer
      actions={(
        <span title="YAML 탭에서 실제 Git 소스·권한·에이전트 적용 가능성을 확인합니다" style={{ display: "flex", alignItems: "center", gap: 5, border: `1px solid ${UI.line}`, background: UI.bg2, borderRadius: 8, padding: "5px 10px", fontSize: TYPE.label, fontWeight: 600, color: UI.ink3 }}>
          소스·권한 검증
        </span>
      )}
      ariaLabel={`${kind.label} ${name} 상세`}
      expanded={fullSelf}
      forceExpanded={forceFull || yamlEditorOpen}
      forceExpandedLabel={yamlEditorOpen ? "YAML 편집 중에는 비교 화면 유지" : undefined}
      header={(
        <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
            <span style={{ width: 30, height: 30, borderRadius: 9, background: blueA(0.09), display: "grid", placeItems: "center", flexShrink: 0 }}>
              <kind.icon size={15} style={{ color: BLUE }} />
            </span>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ fontSize: TYPE.section, fontWeight: 700, fontFamily: MONO, color: UI.ink, letterSpacing: "-0.02em", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{name}</div>
              <div style={{ fontSize: TYPE.label, color: UI.ink3, marginTop: 2 }}>{kind.label} · {ns}</div>
            </div>
        </div>
      )}
      leftInset={leftInset}
      navigation={(
        <DetailDrawerTabs
          active={tab}
          indicatorId="resource-detail-tab"
          items={tabs.map((item) => ({
            id: item,
            label: DETAIL_TABS.find((candidate) => candidate.id === item)!.label,
          }))}
          onChange={switchTab}
        />
      )}
      onClose={onClose}
      onExpandedChange={setFull}
      rightInset={
        rightInset
        + (yamlEditorOpen && !yamlEditorExpanded ? yamlEditorRenderedWidth : 0)
      }
      topInset={topInset}
      viewportWidth={viewportW}
    >
        <div style={{ maxWidth: full ? 880 : "none", margin: full ? "0 auto" : 0 }}>
          {tab === "overview" && (
            <div>
              {/* 운영 이슈 */}
              {bad && (
                <Sec title="운영 이슈" icon={Activity}>
                  <div style={{ display: "flex", alignItems: "flex-start", gap: 9, border: `1px solid ${TINT.crit.bd}`, background: TINT.crit.bg, borderRadius: 10, padding: "10px 12px" }}>
                    <Badge text={statusLabel("critical")} tone="red" />
                    <div style={{ minWidth: 0, display: "grid", gap: 5 }}>
                      <span style={{ fontSize: TYPE.body, fontWeight: 600, color: UI.ink }}>
                        {phase}
                        {operationalCause?.condition?.status
                          ? ` · ${operationalCause.condition.type}=${operationalCause.condition.status}`
                          : ""}
                      </span>
                      {operationalCauseStatus === "loading" ? (
                        <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>상세 원인 확인 중…</span>
                      ) : operationalCauseStatus === "error" ? (
                        <button
                          type="button"
                          className="product-focusable product-control"
                          onClick={isPodKind ? podResourceDetail.retry : conditionView.retry}
                          style={{ justifySelf: "start", border: 0, padding: 0, background: "transparent", color: BLUE, fontSize: TYPE.label, fontWeight: 600, cursor: "pointer" }}
                        >
                          상세 원인 다시 확인
                        </button>
                      ) : operationalCause ? (
                        <>
                          <span style={{ fontSize: TYPE.label, fontWeight: 650, color: UI.ink2 }}>
                            {statusLabel(operationalCause.reason)}
                          </span>
                          {operationalCause.message && (
                            <span style={{ fontSize: TYPE.label, color: UI.ink2, lineHeight: 1.55 }}>
                              {operationalMessageLabel(operationalCause.message)}
                            </span>
                          )}
                          {operationalCause.source === "condition" && operationalCause.supportingEvent && (
                            <span style={{ fontSize: TYPE.caption, color: UI.ink3, lineHeight: 1.5 }}>
                              최근 경고 · {statusLabel(operationalCause.supportingEvent.reason)}
                              {operationalCause.supportingEvent.count != null && operationalCause.supportingEvent.count > 1
                                ? ` ×${operationalCause.supportingEvent.count}`
                                : ""}
                              {operationalCause.supportingEvent.message
                                ? ` · ${operationalMessageLabel(operationalCause.supportingEvent.message)}`
                                : ""}
                            </span>
                          )}
                        </>
                      ) : (
                        <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>
                          현재 관측에서 상세 원인 근거 없음
                        </span>
                      )}
                    </div>
                  </div>
                </Sec>
              )}

              {/* 상태 */}
              <Sec title="상태" icon={Activity}>
                {isWorkload
                  ? (() => {
                      // 관측된 값만 표기: loading은 "불러오는 중…", 값 없음/미관측은 "관측 안 됨".
                      const rep = wd.replicas;
                      const cell = (v: number | null | undefined) =>
                        wd.status === "loading" ? "불러오는 중…" : v != null ? String(v) : "관측 안 됨";
                      const wHealth = wd.status === "loading"
                        ? "불러오는 중…"
                        : wd.health != null && String(wd.health) ? statusLabel(String(wd.health)) : healthVal;
                      return ([
                        ["헬스", wHealth],
                        ["클러스터", clusterVal],
                        ["목표 복제본", cell(rep?.desired)],
                        ["준비된 복제본", cell(rep?.ready)],
                        ["가용 복제본", cell(rep?.available)],
                        ["최신 복제본", cell(rep?.updated)],
                        ["비가용 복제본", cell(rep?.unavailable)],
                      ] as const).map(([k, v]) => (
                        <KV key={k} k={k} v={v} mono tone={k === "헬스" && v !== "관측 안 됨" && v !== "불러오는 중…" ? (bad ? TINT.crit.fg : TINT.ok.fg) : undefined} />
                      ));
                    })()
                  : (() => {
                      const podSummary = podResourceDetail.summary;
                      const podCell = (value: string | null) =>
                        podSummary.status === "loading" ? "불러오는 중…" : value ?? "관측 안 됨";
                      const rows = isPodKind
                        ? [["상태", phase], ["헬스", healthVal], ["클러스터", clusterVal], ["노드", podCell(podSummary.nodeName)], ["파드 IP", podCell(podSummary.podIp)], ["호스트 IP", podCell(podSummary.hostIp)], ["QoS 클래스", "관측 안 됨"], ["ServiceAccount", podCell(podSummary.serviceAccountName)]] as const
                        : [["상태", phase], ["헬스", healthVal], ["클러스터", clusterVal], ["노드", "관측 안 됨"], ["파드 IP", "관측 안 됨"], ["호스트 IP", "관측 안 됨"], ["QoS 클래스", "관측 안 됨"], ["ServiceAccount", "관측 안 됨"]] as const;
                      return rows.map(([k, v]) => <KV key={k} k={k} v={v} mono tone={k === "상태" ? (bad ? TINT.crit.fg : v === "관측 안 됨" ? undefined : TINT.ok.fg) : undefined} />);
                    })()}
                {isWorkload && wd.coverageAvailability === "partial" && (
                  <div style={{ fontSize: TYPE.caption, color: UI.ink3, marginTop: 8 }}>일부 범위만 관측된 부분 스냅샷입니다.</div>
                )}
                {isWorkload && wd.status === "error" && <RetryNote onRetry={wd.retry} label="상세 관측값을 불러오지 못했습니다." />}
                {isPodKind && podResourceDetail.status === "error" && <RetryNote onRetry={podResourceDetail.retry} label="Pod 상세 관측값을 불러오지 못했습니다." />}
                <div style={{ display: "flex", gap: 7, marginTop: 12 }}>
                  {isWorkload && <button className="product-focusable product-control" onClick={onShowPods ? () => onShowPods(name) : undefined}
                    style={{ display: "flex", alignItems: "center", gap: 6, border: `1px solid ${UI.line}`, background: UI.card, borderRadius: 8, padding: "6px 11px", fontSize: TYPE.label, fontWeight: 600, color: BLUE, cursor: "pointer" }}><Boxes size={12} />관리 중인 파드 보기</button>}
                </div>
              </Sec>

              {/* 전략 (워크로드) — 라이브 인벤토리 계약은 배포 전략을 노출하지 않는다 */}
              {isWorkload && (
                <Sec title="전략">
                  <KV k="업데이트 전략" v="관측 안 됨" />
                </Sec>
              )}

              {/* 파드 템플릿 / 컨테이너 — Pod는 resource-detail summary를 쓰고, workload 템플릿은 기존 placeholder를 유지한다. */}
              {wp && (
              <Sec title={isWorkload ? "파드 템플릿" : "컨테이너"} icon={Boxes}>
                {isPodKind ? (
                  <PodContainerDetail summary={podResourceDetail.summary} />
                ) : (
                  <div style={{ border: `1px solid ${UI.line2}`, background: UI.bg2, borderRadius: 10, padding: "11px 13px" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span style={{ fontSize: TYPE.body, fontWeight: 600, fontFamily: MONO, color: UI.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{name}</span>
                    </div>
                    <div style={{ fontSize: TYPE.caption, color: UI.ink3, marginTop: 4 }}>이미지 관측 안 됨</div>
                    <div style={{ fontSize: TYPE.caption, color: UI.ink3, marginTop: 3 }}>포트 관측 안 됨</div>
                  </div>
                )}
              </Sec>
              )}

              {/* 환경 변수 (파드 전용) — 계약이 컨테이너 환경 변수를 노출하지 않는다 */}
              {isPod && (
                <Sec title="환경 변수" defaultOpen={false}>
                  <Empty>환경 변수 정보 없음</Empty>
                </Sec>
              )}

              {/* 컨디션 (워크로드·파드) — resource-detail conditions와 ReplicaSet 관련 Pod fallback을 표시한다. */}
              {wp && (
              <Sec title="컨디션">
                <ResourceConditionsPanel kind={kind.id} view={conditionView} />
              </Sec>
              )}

              {/* 권한 (워크로드·파드) — 실제 namespace reverse-index 계약을 개요에서도 공유한다. */}
              {wp && (
              <Sec title="ServiceAccount 권한" icon={ShieldCheck}>
                <ResourceAccessPanel view={access} />
              </Sec>
              )}

              {/* 앱 정보 (워크로드·파드) — 관측된 리소스 정체성만 표시 */}
              {wp && (
              <Sec title="앱 정보" icon={Folder}>
                <KV k="이름" v={name} mono /><KV k="종류" v={kind.label} mono /><KV k="네임스페이스" v={ns} mono /><KV k="클러스터" v={clusterVal} mono />
              </Sec>
              )}

              {/* 메트릭(M14) — 파드·노드는 관측된 CPU/메모리 시계열을 표시하고,
                  워크로드는 직접 시계열 계약이 없어 관리 파드에서 확인하도록 안내한다. */}
              {supportsUsageSeries && (
              <Sec title="메트릭" icon={Activity}>
                {usage.status === "loading" ? <Empty>불러오는 중…</Empty>
                  : usage.status === "error" ? <RetryNote onRetry={usage.retry} label="메트릭을 불러오지 못했습니다." />
                  : usage.status === "unavailable" ? <Empty>메트릭 없음</Empty>
                  : usage.status === "ready" ? <ResourceMetricsChart points={usage.points} />
                  : <Empty>메트릭 관측 안 됨</Empty>}
              </Sec>
              )}
              {isWorkload && (
              <Sec title="메트릭" icon={Activity}>
                <Empty>파드별 메트릭에서 확인</Empty>
              </Sec>
              )}

              {/* 데이터 (ConfigMap·Secret) — 계약이 데이터 항목을 노출하지 않는다 */}
              {(kind.id === "ConfigMap" || kind.id === "Secret") && (
                <Sec title="데이터" icon={FileCog}>
                  <Empty>데이터 없음</Empty>
                </Sec>
              )}

              {/* 관련 리소스 — 워크로드는 관측된 관리 파드를 표시, 그 외 kind는 계약에 없어 honest 빈상태 */}
              <Sec title="관련 리소스" icon={Copy}>
                {wd.status === "idle" ? <Empty>관련 리소스 없음</Empty>
                  : wd.status === "loading" ? <Empty>불러오는 중…</Empty>
                  : wd.status === "error" ? <RetryNote onRetry={wd.retry} label="관리 파드를 불러오지 못했습니다." />
                  : wd.status === "unavailable" ? <Empty>관리 파드가 관측되지 않았습니다.</Empty>
                  : wd.pods.length === 0 ? <Empty>관측된 관리 파드가 없습니다.{wd.podsExcludedCount > 0 ? ` (수집 한도로 ${wd.podsExcludedCount}개 생략)` : ""}</Empty>
                  : (<div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                      {wd.pods.map((p) => (
                        <div key={`${p.namespace ?? ""}/${p.name}`} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, border: `1px solid ${UI.line2}`, background: UI.bg2, borderRadius: 8, padding: "7px 11px" }}>
                          <span style={{ fontSize: TYPE.caption, fontFamily: MONO, color: UI.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.name}</span>
                          <span style={{ fontSize: TYPE.caption, color: UI.ink3, flexShrink: 0 }}>{statusLabel(p.health)}</span>
                        </div>
                      ))}
                      {wd.podsExcludedCount > 0 && <div style={{ fontSize: TYPE.caption, color: UI.ink3 }}>수집 한도로 {wd.podsExcludedCount}개 생략됨</div>}
                    </div>)}
              </Sec>

              {/* 최근 이벤트(M16) — 워크로드는 관측 이벤트를 표시, 그 외 kind는 honest 빈상태 */}
              <Sec title="최근 이벤트" icon={Activity}>
                {wd.status === "idle" ? <Empty>최근 이벤트 없음</Empty>
                  : wd.status === "loading" ? <Empty>불러오는 중…</Empty>
                  : wd.status === "error" ? <RetryNote onRetry={wd.retry} label="이벤트를 불러오지 못했습니다." />
                  : wd.events.length === 0 ? <Empty>최근 관측된 이벤트가 없습니다.</Empty>
                  : (<div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                      {wd.events.map((e, i) => (
                        <div key={`${e.reason ?? "ev"}-${i}`} style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 10, border: `1px solid ${UI.line2}`, background: UI.bg2, borderRadius: 8, padding: "7px 11px" }}>
                          <span style={{ fontSize: TYPE.caption, color: UI.ink }}>{statusLabel(e.reason)}{e.type ? ` · ${statusLabel(e.type)}` : ""}{e.count != null && e.count > 1 ? ` ×${e.count}` : ""}</span>
                          {e.lastAt && <span style={{ fontSize: TYPE.caption, color: UI.ink3, flexShrink: 0 }}>{e.lastAt.replace("T", " ").slice(0, 16)}</span>}
                        </div>
                      ))}
                    </div>)}
              </Sec>

              {/* 레이블(M13) — 워크로드는 관측 레이블을 표시, 그 외 kind는 계약에 없어 honest 빈상태 */}
              <Sec title="레이블">
                {wd.status === "ready" && wd.labels.length > 0
                  ? (<div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                      {wd.labels.map((l) => (
                        <span key={l.key} style={{ fontSize: TYPE.caption, fontFamily: MONO, color: UI.ink2, border: `1px solid ${UI.line2}`, background: UI.bg2, borderRadius: 6, padding: "3px 7px" }}>{l.key}{l.value ? `=${l.value}` : ""}</span>
                      ))}
                    </div>)
                  : wd.status === "ready" && wd.labels.length === 0 ? <Empty>관측된 레이블이 없습니다.</Empty>
                  : wd.status === "loading" ? <Empty>불러오는 중…</Empty>
                  : <Empty>레이블 없음</Empty>}
              </Sec>
              <Sec title="어노테이션" defaultOpen={false}>
                <Empty>어노테이션 없음</Empty>
              </Sec>
              <Sec title="메타데이터">
                <KV k="UID" v={row.uid != null && String(row.uid) ? String(row.uid) : "관측 안 됨"} mono />
                <KV k="Resource Version" v="관측 안 됨" mono />
                <KV k="Generation" v="관측 안 됨" mono />
                <KV k="생성 시점" v={row.created != null && String(row.created) ? String(row.created) : "관측 안 됨"} mono />
              </Sec>

              {/* 점검 결과 (워크로드·파드) — 점검(audit) 계약이 배선되어 있지 않다 */}
              {wp && (
              <Sec title="점검 결과" icon={ShieldCheck}>
                <Empty>점검 결과 없음</Empty>
              </Sec>
              )}
            </div>
          )}

          {tab === "yaml" && (
            <LiveResourceManifestEditor
              key={`${resourceId}:${manifestRefreshKey}`}
              resourceId={resourceId}
              resolving={observedResourceId === "" && resolvedIdentity.status === "loading"}
              refreshKey={manifestRefreshKey}
              mode="read"
              onSourceLoaded={setManifestSource}
              onEditRequest={() => setYamlEditorOpen(true)}
              onConnectRepository={() => onConnectRepository?.({
                clusterId: row.cluster != null && String(row.cluster) ? String(row.cluster) : undefined,
                namespace: row.ns != null && String(row.ns) ? String(row.ns) : undefined,
              })}
              onOpenDeploySurface={onOpenDeploySurface}
              onReauthenticate={() => window.location.reload()}
              onRequestAccess={onRequestManifestAccess}
            />
          )}


          {tab === "events" && (
            <div style={{ padding: "12px 0" }}>
              {/* M16: 모든 Events 탭은 resource-detail의 involved-object 이벤트 계약을 사용한다. */}
              {resourceEvents.status === "idle" ? <Empty>이 리소스의 이벤트 범위를 확인할 수 없습니다.</Empty>
                : resourceEvents.status === "loading" ? <Empty>불러오는 중…</Empty>
                : resourceEvents.status === "error" ? <RetryNote onRetry={resourceEvents.retry} label="이벤트를 불러오지 못했습니다." />
                : resourceEvents.status === "unavailable" ? <Empty>이 리소스는 현재 인벤토리에서 관측되지 않습니다.</Empty>
                : resourceEvents.items.length === 0 ? <Empty>최근 관측된 이벤트가 없습니다.</Empty>
                : (<div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                    {resourceEvents.items.map((event) => (
                      <div key={event.id} style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 10, border: `1px solid ${UI.line2}`, background: UI.bg2, borderRadius: 8, padding: "8px 12px" }}>
                        <span style={{ minWidth: 0 }}>
                          <span style={{ display: "block", fontSize: TYPE.label, color: UI.ink }}>{statusLabel(event.reason)}{event.type ? ` · ${statusLabel(event.type)}` : ""}{event.count != null && event.count > 1 ? ` ×${event.count}` : ""}</span>
                          {event.message && (
                            <EventMessageText
                              color={UI.ink3}
                              fontSize={TYPE.caption}
                              message={event.message}
                              reasonLabel={statusLabel(event.reason)}
                            />
                          )}
                        </span>
                        {event.lastAt && <span style={{ fontSize: TYPE.caption, color: UI.ink3, flexShrink: 0 }}>{event.lastAt.replace("T", " ").slice(0, 16)}</span>}
                      </div>
                    ))}
                  </div>)}
            </div>
          )}

          {tab === "logs" && (
            <div style={{ padding: "18px 0", fontSize: TYPE.label, color: UI.ink3, lineHeight: 1.6 }}>
              로그 없음
            </div>
          )}

          {tab === "rbac" && (
            <div style={{ padding: "14px 0" }}><ResourceAccessPanel view={access} /></div>
          )}
        </div>
      </DetailDrawer>
      <AnimatePresence>
        {yamlEditorOpen && tab === "yaml" && (
          <motion.aside
            key="resource-yaml-editor"
            role="dialog"
            aria-label={`${kind.label} ${name} YAML 편집`}
            initial={{ x: 36, opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            exit={{ x: 24, opacity: 0, transition: SIDE_PANEL_EXIT_TRANSITION }}
            transition={SIDE_PANEL_ENTER_TRANSITION}
            style={{
              ...SIDE_PANEL_SURFACE_STYLE,
              position: "fixed",
              top: topInset,
              right: rightInset,
              bottom: 0,
              width: yamlEditorRenderedWidth,
              maxWidth: yamlEditorAvailableWidth,
              zIndex: 72,
              transition: yamlEditorDragging
                ? "none"
                : SIDE_PANEL_WIDTH_TRANSITION,
            }}
          >
            {!yamlEditorExpanded && (
              <SidePanelResizeHandle
                ariaLabel="YAML 편집 패널 폭 조절"
                dragging={yamlEditorDragging}
                maximumWidth={yamlEditorAvailableWidth}
                minimumWidth={380}
                onKeyDown={onYamlEditorEdgeKeyDown}
                onPointerDown={onYamlEditorEdgeDown}
                value={yamlEditorRenderedWidth}
              />
            )}
            <div style={{ flexShrink: 0, display: "flex", alignItems: "flex-start", gap: 12, padding: "16px 20px", borderBottom: `1px solid ${UI.line}` }}>
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ fontSize: TYPE.section, fontWeight: 700, color: UI.ink }}>YAML 편집</div>
                <div style={{ marginTop: 2, fontSize: TYPE.label, color: UI.ink3, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {kind.label} · {name}
                </div>
              </div>
              <SidePanelWindowControls
                closeLabel="YAML 편집 패널 닫기"
                expanded={yamlEditorExpanded}
                onClose={closeYamlEditor}
                onExpandedChange={setYamlEditorExpanded}
                panelLabel="YAML 편집 패널"
              />
            </div>
            <div style={{ flex: 1, minHeight: 0, overflowY: "auto", overflowX: "hidden", overscrollBehavior: "contain", scrollbarGutter: "stable", padding: "0 20px 28px" }}>
              <LiveResourceManifestEditor
                resourceId={resourceId}
                resolving={observedResourceId === "" && resolvedIdentity.status === "loading"}
                refreshKey={manifestRefreshKey}
                mode="edit"
                initialSource={manifestSource}
                wide={yamlEditorExpanded || yamlEditorRenderedWidth >= 720}
                onOpenDeploySurface={onOpenDeploySurface}
                onReauthenticate={() => window.location.reload()}
                onRequestAccess={onRequestManifestAccess}
              />
            </div>
          </motion.aside>
        )}
      </AnimatePresence>
    </>
  );
}

// ── 종류 탐색 — 우측 패널 '리소스' 탭 내용 (보조 사이드바를 통합·대체) ─────────────────────────────

// ── 트래픽 보조 패널 — 서비스 호출 상태·포커스(D22: 세 관점 모두 같은 자리 보조 패널) ──
function TrafficPanel({ clusterIds, focus, onFocus, onOpen, stickyTop, viewportTopInset = 0, stacked = false }: {
  clusterIds: readonly string[]; focus: string | null; onFocus: (id: string | null) => void;
  onOpen: (node: RelationNodeView) => void; stickyTop: number; viewportTopInset?: number; stacked?: boolean;
}) {
  // 실 관계 토폴로지(GET /api/topology?view=relations)의 서비스/워크로드 노드 — svcCatalog fixture·합성 RPS 제거.
  // 계약이 RPS/p99를 노출하지 않으므로 호출량 수치는 표기하지 않는다(관측 안 됨).
  const topo = useRelationTopology(clusterIds);
  // M20: React key·포커스는 cluster 한정 합성 id(n.id)로 — 여러 클러스터의 동일 서비스명이
  // 충돌해 dup key가 나거나 한 행이 다른 클러스터 서비스를 가리키지 않게 한다. 표시·상세
  // 열기는 서비스 이름을 쓴다.
  const rows = useMemo(() => topo.status === "ready"
    ? topo.nodes.map((n) => ({ node: n, id: n.id, name: n.name || n.id, kind: n.kind, ns: n.namespace, cluster: n.clusterId, bad: /error|fail|crit|degrad|down|unhealthy/i.test(n.status) }))
    : [], [topo]);
  const visibleRows = rows.slice(0, 40);
  const hiddenCount = Math.max(0, rows.length - visibleRows.length);
  const panelHeight = resourceAuxiliaryViewportHeight(viewportTopInset, stickyTop);
  const clearAction = focus ? (
    <button
      type="button"
      className="product-focusable product-control"
      aria-label="서비스 포커스 해제"
      onClick={() => onFocus(null)}
      style={{ border: "none", background: inkA(0.05), color: UI.ink3, borderRadius: 999, padding: "3px 9px", fontSize: TYPE.caption, fontWeight: 600, cursor: "pointer" }}
    >
      해제
    </button>
  ) : undefined;
  return (
    <ResourceAuxiliaryPanel
      data-resource-traffic-panel="true"
      aria-label="트래픽 관계 노드"
      style={{ width: stacked ? "100%" : RESOURCE_LAYOUT.auxiliaryWidth, height: stacked ? 300 : panelHeight, maxHeight: stacked ? 300 : panelHeight, position: stacked ? "relative" : "sticky", top: stacked ? undefined : stickyTop }}
      header={<ResourceAuxiliaryHeader title="관계 노드" value={`${rows.length}개`} detail={topo.omittedNodeCount > 0 ? `${topo.omittedNodeCount}개 생략` : undefined} action={clearAction} />}
    >
      {topo.status === "loading" && <span style={{ fontSize: TYPE.caption, color: UI.ink3, padding: "6px 2px" }}>불러오는 중…</span>}
      {topo.status === "unavailable" && <span style={{ fontSize: TYPE.caption, color: UI.ink3, padding: "6px 2px" }}>관계 토폴로지 관측 안 됨</span>}
      {topo.status === "ready" && rows.length === 0 && <span style={{ fontSize: TYPE.caption, color: UI.ink3, padding: "6px 2px" }}>관측된 서비스가 없습니다</span>}
      <div style={{ display: "grid", gap: RESOURCE_LAYOUT.auxiliaryRowGap }}>
        {visibleRows.map((r) => (
          <ResourceAuxiliaryRow
            key={r.id}
            className="rrow product-control"
            ariaLabel={`${r.name} 서비스 그래프 포커스`}
            selected={focus === r.id}
            onActivate={() => onFocus(focus === r.id ? null : r.id)}
            onDoubleActivate={() => onOpen(r.node)}
            tooltip={`${r.name} · ${r.kind} · ${r.ns ?? "클러스터 범위"} · ${r.cluster}`}
            icon={<span style={{ width: 8, height: 8, borderRadius: 3, background: r.bad ? HP.crit : HP.ok }} />}
            title={r.name}
            titleFontFamily={MONO}
            meta={`${r.kind} · ${r.ns ?? "클러스터 범위"}`}
            trailing={r.bad
              ? <span style={{ fontFamily: "inherit", fontSize: TYPE.caption, fontWeight: 600, color: TINT.crit.fg, background: critA(0.09), border: `1px solid ${critA(0.3)}`, borderRadius: 999, padding: "2px 7px" }}>장애</span>
              : <span aria-label="정상" style={{ width: 6, height: 6, borderRadius: 999, background: HP.ok }} />}
          />
        ))}
      </div>
      {hiddenCount > 0 && (
        <span style={{ fontSize: TYPE.caption, color: UI.ink3, padding: "8px 9px", borderTop: `1px solid ${UI.line2}` }}>
          현재 범위의 나머지 서비스 {hiddenCount}개는 검색·범위 축소 후 표시됩니다.
        </span>
      )}
    </ResourceAuxiliaryPanel>
  );
}

function KindIndex({ sel, onPick, showEmpty, setShowEmpty, pinned, togglePin, filter, counts }: {
  sel: string; onPick: (k: Kind) => void; showEmpty: boolean; setShowEmpty: (v: boolean) => void;
  pinned: string[]; togglePin: (id: string) => void; filter: string; // 상단 ⌘K 검색이 단일 소스 — 자체 검색창 없음
  counts: Record<string, number>; // 라이브 인벤토리 요약 파생 카운트(없으면 0 = 관측 안 됨)
}) {
  const cnt = (k: Kind) => counts[k.id] ?? 0;
  const emptyCount = KINDS.filter((k) => cnt(k) === 0).length;
  const match = (k: Kind) => (k.label + k.id).toLowerCase().includes(filter.toLowerCase());
  const Row = ({ k }: { k: Kind }) => {
    const on = sel === k.id;
    return (
      <ResourceAuxiliaryRow
        className="krow product-control"
        selected={on}
        ariaLabel={`${k.label} 리소스 보기`}
        onActivate={() => onPick(k)}
        icon={<k.icon size={14} style={{ color: on ? BLUE : UI.ink3 }} />}
        title={k.label}
        trailing={<span style={{ minWidth: 24, textAlign: "center", color: cnt(k) ? (on ? BLUE : UI.ink2) : UI.ink3, background: on ? blueA(0.12) : inkA(0.05), borderRadius: 5, padding: "2px 5px" }}>{cnt(k)}</span>}
        secondaryAction={<button
          type="button"
          aria-label={`${k.label} 즐겨찾기 ${pinned.includes(k.id) ? "해제" : "추가"}`}
          aria-pressed={pinned.includes(k.id)}
          title="즐겨찾기"
          onClick={(event) => {
            event.stopPropagation();
            togglePin(k.id);
          }}
          className="kpin product-focusable product-control"
          style={{ width: 24, height: 28, border: "none", borderRadius: 6, background: "transparent", cursor: "pointer", display: "grid", placeItems: "center", opacity: pinned.includes(k.id) ? 1 : 0.4 }}
        >
          <Pin size={10} style={{ color: pinned.includes(k.id) ? BLUE : UI.ink3 }} />
        </button>}
      />
    );
  };
  return (
    <nav style={{ width: "100%", display: "flex", flexDirection: "column", gap: 10 }}>
      {pinned.length > 0 && (
      <ResourceAuxiliarySection label="즐겨찾기">
        {KINDS.filter((k) => pinned.includes(k.id)).map((k) => <Row key={k.id} k={k} />)}
      </ResourceAuxiliarySection>
      )}
      {GROUPS.map((g) => {
        const list = KINDS.filter((k) => k.group === g && (showEmpty || cnt(k) > 0) && match(k));
        if (!list.length) return null;
        return (
          <ResourceAuxiliarySection key={g} label={g} value={GROUP_TOTAL(g, counts)}>
            {list.map((k) => <Row key={k.id} k={k} />)}
          </ResourceAuxiliarySection>
        );
      })}
      <button className="product-focusable product-control" aria-pressed={showEmpty} onClick={() => setShowEmpty(!showEmpty)} style={resourceAuxiliaryFooterButtonStyle}>
        <Eye size={12} />{showEmpty ? "비어 있는 종류 숨기기" : `비어 있는 종류 ${emptyCount}개 표시`}
      </button>
    </nav>
  );
}

// ── 전역 내비게이션 레일 — 병합 IA 8항목(D19). 트래픽은 리소스의 '흐름' 관점으로,
//    애플리케이션·GitOps·Helm은 '배포'로 흡수. 8항목 전부 실서피스다.
const NAV_ITEMS: { id: string; label: string; icon: typeof Home }[] = [
  { id: "home", label: "홈", icon: Home },
  { id: "resources", label: "리소스", icon: ListTree },
  { id: "deploy", label: "배포", icon: Rocket },
  { id: "issues", label: "이슈", icon: AlertTriangle },
  { id: "timeline", label: "타임라인", icon: Clock },
  { id: "cost", label: "비용", icon: Coins },
  // 알림·AI 대화 = 내역 모아보기 서피스(벨·AI 패널의 "전체 보기" 목적지) — 주 내비 소속
  { id: "alerts", label: "알림", icon: Bell },
  { id: "ai", label: "AI 대화", icon: Sparkles },
];
// 연결은 내비 항목이 아니다(D7·D20) — 홈 액션과 각 도메인 화면에서 문맥 모달로 연다.
// 설정은 전역 앱 설정만(D20).
const NAV_BOTTOM: { id: string; label: string; icon: typeof Home }[] = [
  { id: "settings", label: "설정", icon: Settings },
];

type Surface = ProductSurfaceId;
const SURFACE_OF: Record<string, Surface> = { home: "home", resources: "resources", deploy: "deploy", issues: "issues", timeline: "timeline", cost: "cost", alerts: "alerts", ai: "ai", settings: "settings" };
// 리소스 서피스의 관점(D18) — 한 서피스, 세 관점. 스코프는 관점을 넘어 보존된다.
type ResView = "map" | "list" | "flow";

function GlobalNav({ collapsed, setCollapsed, surface, onSurface }: {
  collapsed: boolean; setCollapsed: (v: boolean) => void;
  surface: Surface; onSurface: (s: Surface) => void;
}) {
  const Item = ({ it }: { it: (typeof NAV_ITEMS)[number] }) => {
    const sid = SURFACE_OF[it.id];
    const active = !!sid && surface === sid;
    const enabled = active || !!sid;
    return (
      <button type="button" className={`product-focusable product-control${enabled ? " gnav" : ""}`} title={collapsed ? it.label : undefined}
        aria-label={`${it.label} 화면으로 이동`} aria-current={active ? "page" : undefined}
        disabled={!sid} onClick={sid ? () => onSurface(sid) : undefined}
        style={{ display: "flex", alignItems: "center", gap: 11, borderRadius: 9, padding: collapsed ? "9px 0" : "8px 11px", justifyContent: collapsed ? "center" : "flex-start",
          width: "100%", border: "none", textAlign: "left",
          background: active ? blueA(0.09) : "transparent", color: active ? BLUE : enabled ? UI.ink2 : UI.ink3,
          opacity: enabled ? 1 : 0.45, transition: "background .14s" }}>
        <it.icon size={16} style={{ flexShrink: 0 }} />
        {!collapsed && <span style={{ fontSize: TYPE.body, fontWeight: active ? 600 : 500, whiteSpace: "nowrap" }}>{it.label}</span>}
      </button>
    );
  };
  return (
    <motion.nav initial={false} animate={{ width: collapsed ? 60 : 208 }} transition={SOFT}
      data-slot="global-navigation"
      style={{ flexShrink: 0, background: UI.card, borderRight: `1px solid ${UI.line}`, display: "flex", flexDirection: "column",
        padding: "14px 10px 12px", position: "sticky", top: 0, height: `calc(100vh / ${PRESENT_SCALE})`, overflow: "hidden" }}>
      {/* 브랜드 — Kyro 워드마크 */}
      <div style={{ display: "flex", alignItems: "center", gap: 9, padding: collapsed ? "0 0 16px" : "0 4px 16px", justifyContent: collapsed ? "center" : "flex-start" }}>
        <span style={{ width: 26, height: 26, borderRadius: 8, background: `linear-gradient(135deg, ${BLUE}, ${BLUE2})`, display: "grid", placeItems: "center", flexShrink: 0 }}>
          <span style={{ width: 9, height: 9, borderRadius: 999, border: `2px solid ${UI.card}` }} />
        </span>
        {!collapsed && <span style={{ fontSize: TYPE.section, fontWeight: 700, letterSpacing: "-0.02em", color: UI.ink }}>Kyro</span>}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>{NAV_ITEMS.map((it) => <Item key={it.id} it={it} />)}</div>
      <div style={{ marginTop: "auto", display: "flex", flexDirection: "column", gap: 1, borderTop: `1px solid ${UI.line2}`, paddingTop: 8 }}>
        {NAV_BOTTOM.map((it) => <Item key={it.id} it={it} />)}
        <button type="button" aria-label={collapsed ? "주 메뉴 펼치기" : "주 메뉴 접기"} aria-expanded={!collapsed} onClick={() => setCollapsed(!collapsed)} className="gnav product-focusable product-control"
          style={{ display: "flex", alignItems: "center", gap: 11, border: "none", background: "transparent", borderRadius: 9, padding: collapsed ? "9px 0" : "8px 11px", justifyContent: collapsed ? "center" : "flex-start", color: UI.ink3, cursor: "pointer" }}>
          {collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
          {!collapsed && <span style={{ fontSize: TYPE.body, fontWeight: 600 }}>접기</span>}
        </button>
      </div>
    </motion.nav>
  );
}

// ── 홈 서피스 (D21: 고정 헤더 + 클러스터 섹션 + 위젯 보드) ──────────────────
// 모든 숫자는 단일 인벤토리 파생. 위젯 배치는 localStorage 보존, 편집=숨김·추가·이동(제품은 dnd-kit 드래그).
const W_DEFS: { id: string; title: string; info: string; defaultSpan: DashboardWidgetSpan }[] = [
  { id: "W1", title: "Clusters", info: "연결된 클러스터의 라이브 상태와 사용률을 한 행에서 비교", defaultSpan: 4 },
  { id: "W2", title: "이슈", info: "장애 상태 파드에서 파생된 활성 이슈 상위 3건", defaultSpan: 2 },
  { id: "W3", title: "저장소 동기화", info: "Git 저장소 단위 동기화 상태 — 앱 단위 현황은 배포 화면", defaultSpan: 2 },
  { id: "W4", title: "활동 추이", info: "기간 내 배포·알림·장애 리소스 수의 흐름", defaultSpan: 3 },
  { id: "W5", title: "네임스페이스 파드 분포", info: "파드 수 상위 네임스페이스 — 항목 클릭 시 리소스 목록으로 필터 이동", defaultSpan: 2 },
  { id: "W6", title: "장애·주의 리소스", info: "지금 주의가 필요한 리소스 상위 5 — 행 클릭 시 상세", defaultSpan: 2 },
  { id: "W7", title: "비용", info: "이번 달 클러스터 비용 요약 (증가는 주의 톤)", defaultSpan: 1 },
  { id: "W8", title: "최근 변경", info: "타임라인 최신 변경 5건의 미니 뷰", defaultSpan: 4 },
  { id: "W9", title: "현재 경보", info: "전역 경보 피드에서 지금 발생 중인 최신 경보", defaultSpan: 2 },
  { id: "W10", title: "운영 대기열", info: "승인 대기·실행 중 워크플로·처리 실패의 현재 합계", defaultSpan: 2 },
  { id: "W11", title: "애플리케이션 상태", info: "배포 애플리케이션의 런타임 상태 분포", defaultSpan: 2 },
  { id: "W12", title: "애플리케이션 상세", info: "주의가 필요한 애플리케이션 우선 목록 — 항목 클릭 시 실제 상세", defaultSpan: 2 },
];
const BOARD_KEY = "opsia-demo-board-v2"; // v2: W5~W8 기본 노출(D21 위젯 보드 전체가 기본값)
type BoardState = { order: string[]; hidden: string[]; spans: Record<string, DashboardWidgetSpan>; types: Record<string, string> };
const defaultBoard = (): BoardState => ({
  order: W_DEFS.map((w) => w.id),
  hidden: [],
  spans: Object.fromEntries(W_DEFS.map((w) => [w.id, w.defaultSpan])),
  types: Object.fromEntries(W_DEFS.map((w) => [w.id, w.id])),
});
const readBoard = (): BoardState => {
  try {
    const s = JSON.parse(localStorage.getItem(BOARD_KEY) || "");
    if (Array.isArray(s.order)) {
      const defaults = defaultBoard();
      const knownIds = new Set(W_DEFS.map((w) => w.id));
      const legacyOrder = s.order.filter((id: unknown): id is string => typeof id === "string" && knownIds.has(id));
      const missing = defaults.order.filter((id) => !legacyOrder.includes(id));
      // W1 Clusters is a new primary dashboard slot. Existing users keep their
      // customized relative order, while the newly introduced cluster summary
      // is inserted first instead of being buried below legacy widgets.
      const order = [...missing.filter((id) => id === "W1"), ...legacyOrder, ...missing.filter((id) => id !== "W1")];
      const spans = { ...defaults.spans };
      const types = { ...defaults.types };
      for (const id of order) {
        const span = Number(s.spans?.[id]);
        if ([1, 2, 3, 4].includes(span)) spans[id] = span as DashboardWidgetSpan;
        const type = s.types?.[id];
        if (typeof type === "string" && knownIds.has(type)) types[id] = type;
      }
      return {
        order,
        hidden: Array.isArray(s.hidden) ? s.hidden.filter((id: unknown): id is string => typeof id === "string" && knownIds.has(id)) : [],
        spans,
        types,
      };
    }
  } catch { /* 기본값 */ }
  return defaultBoard();
};

function HomeSurface({ workspaceId, applicationsFeed, alertEvents, namespaceFeed, clusterMeta, incidentClusterIds, onDrillCluster, onClusterSettings, onClusterDisconnect, onConnect, onAddRepo, onOpenPod: _onOpenPod, onPickNs, onWidgetDeepLink, onOpenAlert, onOpenApplication, onOpenIssues, pendingCl = [], pendingRepo = [] }: {
  workspaceId: string | null;
  applicationsFeed: ApplicationsFeed;
  alertEvents: AlertEventsFeed;
  namespaceFeed: InventoryNamespacesView;
  clusterMeta: Record<string, Record<string, number>>;
  incidentClusterIds: readonly string[];
  onDrillCluster: (clId: string) => void; onConnect: () => void; onAddRepo: () => void;
  onClusterSettings?: (clId: string) => void; onClusterDisconnect?: (clId: string) => void;
  onOpenPod: (name: string) => void; onPickNs: (ns: string) => void;
  pendingCl?: string[]; pendingRepo?: string[]; onWidgetDeepLink?: (id: string) => void;
  onOpenAlert?: (eventId: string) => void;
  onOpenApplication?: (applicationId: string) => void;
  onOpenIssues?: () => void;
}) {
  // 상단 요약 칩은 렌더 지점(아래 IIFE)에서 실 관측 이슈로 계산 — fixture 인벤토리 제거.
  const clusters = Object.keys(clusterMeta);
  const fleet = useFleetSummaryFeed(workspaceId, clusters);
  const fleetHeader = useMemo(
    () => fleet.totalsObservation === "observed" && fleet.totals
      ? fleetHeaderGroups(fleet.totals)
      : null,
    [fleet.totals, fleet.totalsObservation],
  );
  // W2 이슈 위젯 — 실 RCA 이슈 큐(GET /api/dashboard/rca/issues). 빈 배열=관측된 이슈 없음.
  const issues = useRcaIssues(incidentClusterIds);
  // W7 비용 위젯 — 실 GET /api/cost/overview. 현 계약은 관측 unavailable(가격 backfill 금지).
  const cost = useCostOverview(clusters);

  // priority 14: 좁은 화면(≤768px)에서 클러스터 카드·위젯 보드를 1열로, 상단 컨트롤을
  // 줄바꿈해 한글이 글자 단위로 세로 붕괴하지 않도록 한다.
  const narrow = useNarrowViewport();
  const [board, setBoard] = useState<BoardState>(readBoard);
  const [editing, setEditing] = useState(false);
  const save = (b: BoardState) => { setBoard(b); try { localStorage.setItem(BOARD_KEY, JSON.stringify(b)); } catch { /* 데모 */ } };
  // 드래그 리오더 — 끌고 있는 카드가 다른 카드 위를 지나면 즉시 자리를 바꾼다(라이브 미리보기, motion layout이 스프링으로 따라온다)
  const [dragId, setDragId] = useState<string | null>(null);
  const dragOverWidget = (overId: string) => {
    if (!dragId || dragId === overId) return;
    const order = [...board.order];
    const from = order.indexOf(dragId), to = order.indexOf(overId);
    if (from < 0 || to < 0) return;
    order.splice(from, 1);
    order.splice(to, 0, dragId);
    save({ ...board, order });
  };

  // 활동 추이 — 기간 컨텍스트에 따라 포인트 수만 달라지는 결정적 시계열 (단일 시드)

  // W5 네임스페이스 분포 — 실 클러스터 인벤토리 요약의 네임스페이스별 파드 수.
  const nsDist = useMemo(() => {
    const arr = namespaceFeed.items;
    const top = arr.slice(0, 5).map((n) => ({ label: n.namespace, value: n.podCount }));
    const rest = arr.slice(5).reduce((s, n) => s + n.podCount, 0);
    return rest > 0 ? [...top, { label: "기타", value: rest, pick: false }] : top; // '기타'는 필터 목적지가 없다 — 클릭 불가
  }, [namespaceFeed.items]);
  // W6 장애·주의 리소스 — 실 RCA 이슈 큐의 미해결 이슈 상위 5(홈 W2와 동일 소스).
  const watch = useMemo(() => {
    if (issues.status !== "ready") return [];
    return issues.items
      .filter((iss) => !/resolved/i.test(iss.status))
      .slice(0, 5)
      .map((iss) => ({
        id: iss.correlationId,
        tone: (iss.severity === "warning" ? "warn" : "crit") as "warn" | "crit",
        title: iss.resourceName ?? iss.correlationId.slice(0, 12),
        sub: [iss.namespace, iss.clusterId].filter(Boolean).join(" · ") || operationalMessageLabel(iss.symptom || iss.status),
        right: statusLabel(iss.status),
      }));
  }, [issues]);
  // W8 최근 변경 · W4 활동 — 실 GET /api/changes(버킷 시계열 + 순서 이벤트).
  const changeTimeline = useChangeTimeline(workspaceId, clusters);
  // W3 저장소 동기화 — 실 GET /api/applications(배포/GitOps 상태).
  const apps = applicationsFeed;
  const activeAlerts = useMemo(() => activeAlertRows(alertEvents.items), [alertEvents.items]);
  const applicationHealth = useMemo(
    () => applicationHealthItems(apps.items, { ok: HP.ok, warn: HP.warn, unknown: UI.ink3 }),
    [apps.items],
  );
  const applicationAttention = useMemo(
    () => applicationAttentionRows(apps.items),
    [apps.items],
  );
  // 렌더 순수성 — Date.now() 상대시각 금지. 이벤트의 절대 시각(월/일 HH:MM)만 표기.
  const clockTime = (ms: number) => {
    const d = new Date(ms);
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const dd = String(d.getDate()).padStart(2, "0");
    const hh = String(d.getHours()).padStart(2, "0");
    const mi = String(d.getMinutes()).padStart(2, "0");
    return `${mm}/${dd} ${hh}:${mi}`;
  };

  const body = (id: string) => {
    switch (id) {
      case "W1":
        return <HomeClustersWidget summaries={fleet.clusters} onOpen={onDrillCluster} onSettings={onClusterSettings} onDisconnect={onClusterDisconnect} pending={pendingCl} />;
      case "W2":
        if (issues.status === "loading") return <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>불러오는 중…</span>;
        if (issues.status === "unavailable") return <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>이슈를 불러오지 못했습니다</span>;
        {
          const content = issues.items.length
          ? <RankList onPick={onOpenIssues ? () => onOpenIssues() : undefined} rows={issues.items.slice(0, 3).map((iss) => ({
              id: iss.correlationId,
              tone: iss.severity === "warning" ? "warn" as const : "crit" as const,
              title: `${iss.resourceName ?? iss.correlationId} · ${operationalMessageLabel(iss.symptom ?? iss.status)}`,
              sub: [iss.namespace, iss.clusterId].filter(Boolean).join(" · ") || statusLabel(iss.status),
              right: statusLabel(iss.status),
            }))} />
          : <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>활성 이슈가 없습니다</span>;
          return issues.status === "stale"
            ? <div style={{ display: "flex", flexDirection: "column", gap: 6 }}><span style={{ fontSize: TYPE.caption, color: TINT.warn.fg }}>최근 관측값 · 재조회 대기</span>{content}</div>
            : content;
        }
      case "W3":
        if (apps.status === "loading") return <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>불러오는 중…</span>;
        if (apps.status === "unavailable") return <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>애플리케이션을 불러오지 못했습니다</span>;
        if (apps.items.length === 0) return <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>관측된 애플리케이션이 없습니다</span>;
        {
          const outSyncApps = apps.items.filter((a) => a.deliveryStatus && /pending|outofsync|drift|degraded|error/i.test(a.deliveryStatus)).length;
          const syncedApps = apps.items.length - outSyncApps;
          return (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <RatioBar a={syncedApps} b={outSyncApps} aLabel="동기화" bLabel="대기/드리프트" />
              {apps.stale && <span style={{ fontSize: TYPE.caption, color: TINT.warn.fg }}>최근 관측값 · 재조회 대기</span>}
              {pendingRepo.length > 0 && <span style={{ fontSize: TYPE.caption, color: TINT.blue.fg }}>연결 중 {pendingRepo.length} · 초기 동기화 대기</span>}
            </div>
          );
        }
      case "W4":
        if (changeTimeline.status === "loading") return <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>불러오는 중…</span>;
        if (changeTimeline.status === "unavailable") return <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>활동 관측 안 됨</span>;
        if (changeTimeline.buckets.length === 0) return <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>관측된 활동이 없습니다</span>;
        return <MultiLine series={[
          { label: "전체", color: BLUE, values: changeTimeline.buckets.map((b) => b.total) },
          { label: "경고", color: HP.warn, values: changeTimeline.buckets.map((b) => b.warnings) },
        ]} />;
      case "W5":
        if (namespaceFeed.status === "loading") return <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>불러오는 중…</span>;
        if (namespaceFeed.status === "unavailable") return <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>네임스페이스 관측 안 됨</span>;
        return nsDist.length
          ? <div style={{ flex: 1, display: "flex", alignItems: "center" }}><Donut items={nsDist} onPick={(l) => l !== "기타" && onPickNs(l)} /></div>
          : <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>관측된 파드가 없습니다</span>;
      case "W6":
        if (issues.status === "loading") return <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>불러오는 중…</span>;
        if (issues.status === "unavailable") return <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>불러오지 못했습니다</span>;
        {
          const content = watch.length
          ? <RankList onPick={onOpenIssues ? () => onOpenIssues() : undefined} rows={watch} />
          : <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>주의가 필요한 리소스가 없습니다</span>;
          return issues.status === "stale"
            ? <div style={{ display: "flex", flexDirection: "column", gap: 6 }}><span style={{ fontSize: TYPE.caption, color: TINT.warn.fg }}>최근 관측값 · 재조회 대기</span>{content}</div>
            : content;
        }
      case "W7": {
        if (cost.status === "loading") return <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>불러오는 중…</span>;
        if (cost.status === "error") return <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>비용을 불러오지 못했습니다</span>;
        // 현 dev 계약: 비용 관측 unavailable. 가짜 총액을 backfill하지 않고 정직 상태 표시.
        return (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <span style={{ fontSize: TYPE.body, fontWeight: 600, color: UI.ink2 }}>비용 관측 안 됨</span>
            <span style={{ fontSize: TYPE.caption, color: UI.ink3 }}>{reasonLabel(cost.reasonCodes[0] ?? "cost_observation_unavailable")}</span>
          </div>
        );
      }
      case "W8":
        if (changeTimeline.status === "loading") return <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>불러오는 중…</span>;
        if (changeTimeline.status === "unavailable") return <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>최근 변경 관측 안 됨</span>;
        {
          const recent = [...changeTimeline.events].reverse().slice(0, 6);
          if (recent.length === 0) return <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>최근 변경이 없습니다</span>;
          return <MiniTimeline columns={2} items={recent.map((e) => ({
            id: e.id,
            time: clockTime(e.occurredMs),
            tone: (e.kind === "incident" ? "crit" : e.kind === "deployment" ? "ok" : "warn") as "ok" | "warn" | "crit",
            title: operationalMessageLabel(e.title),
          }))} />;
        }
      case "W9":
        if (alertEvents.status === "loading") return <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>불러오는 중…</span>;
        if (alertEvents.status === "unavailable") return <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>경보를 불러오지 못했습니다</span>;
        {
          const content = activeAlerts.length
            ? <RankList onPick={onOpenAlert} rows={activeAlerts} />
            : <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>현재 발생 중인 경보가 없습니다</span>;
          return alertEvents.transport === "stale"
            ? <div style={{ display: "flex", flexDirection: "column", gap: 6 }}><span style={{ fontSize: TYPE.caption, color: TINT.warn.fg }}>최근 관측값 · 실시간 연결 재시도 중</span>{content}</div>
            : content;
        }
      case "W10":
        if (fleet.totalsObservation === "loading") return <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>불러오는 중…</span>;
        if (!fleet.totals) return <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>운영 합계를 관측하지 못했습니다</span>;
        {
          const operations = fleetHeaderGroups(fleet.totals).operations;
          return (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {fleet.totalsObservation === "stale" && <span style={{ fontSize: TYPE.caption, color: TINT.warn.fg }}>최근 관측값 · 재조회 대기</span>}
              <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 7 }}>
                {operations.map((metric) => {
                  const warning = metric.key === "dead_letters" && metric.value > 0;
                  return (
                    <span key={metric.key} style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0, border: `1px solid ${warning ? TINT.warn.bd : UI.line2}`, borderRadius: 9, background: warning ? TINT.warn.bg : UI.bg2, padding: "8px 10px" }}>
                      <b style={{ fontSize: TYPE.section, color: warning ? TINT.warn.fg : UI.ink, fontVariantNumeric: "tabular-nums" }}>{metric.value}</b>
                      <span style={{ fontSize: TYPE.caption, color: UI.ink3, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{metric.label}</span>
                    </span>
                  );
                })}
              </div>
            </div>
          );
        }
      case "W11":
        if (apps.status === "loading") return <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>불러오는 중…</span>;
        if (apps.status === "unavailable") return <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>애플리케이션을 불러오지 못했습니다</span>;
        if (apps.items.length === 0) return <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>관측된 애플리케이션이 없습니다</span>;
        return (
          <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 7, justifyContent: "center" }}>
            {apps.stale && <span style={{ fontSize: TYPE.caption, color: TINT.warn.fg }}>최근 관측값 · 재조회 대기</span>}
            <Donut items={applicationHealth} />
          </div>
        );
      case "W12":
        if (apps.status === "loading") return <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>불러오는 중…</span>;
        if (apps.status === "unavailable") return <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>애플리케이션을 불러오지 못했습니다</span>;
        if (applicationAttention.length === 0) return <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>관측된 애플리케이션이 없습니다</span>;
        return (
          <div style={{ display: "flex", flexDirection: "column", gap: 6, minHeight: 0, flex: 1 }}>
            {apps.stale && <span style={{ fontSize: TYPE.caption, color: TINT.warn.fg }}>최근 관측값 · 재조회 대기</span>}
            <RankList rows={applicationAttention} onPick={onOpenApplication} />
          </div>
        );
      default: return null;
    }
  };

  const visible = board.order.filter((id) => !board.hidden.includes(id));
  const hiddenSlots = board.order.filter((id) => board.hidden.includes(id));
  return (
    <main className="home-surface" style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: SPACE.card, padding: "var(--home-surface-padding, 14px 18px 40px)" }}>
      {/* ── 고정 헤더: 상태 요약 줄(지도 요약 줄과 같은 칩 문법·같은 표기 — 두 화면이 다른 형식으로 말하지 않는다) ── */}
      <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
        {(() => {
          const seg: React.CSSProperties = { display: "flex", alignItems: "center", gap: 5, fontSize: TYPE.label, fontWeight: 600, color: UI.ink2, background: UI.card, border: `1px solid ${UI.line}`, borderRadius: 999, padding: "5px 11px", whiteSpace: "nowrap" };
          const num: React.CSSProperties = { fontWeight: 700, color: UI.ink, fontVariantNumeric: "tabular-nums" };
          const healthMetrics = fleetHeader?.health ?? [{
            key: "clusters",
            label: "클러스터",
            value: clusters.length,
          }];
          return (
            <span style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <span style={seg}>
                <Server size={11} style={{ color: UI.ink3 }} />
                {healthMetrics.map((metric, index) => (
                  <span key={metric.key}>
                    {index > 0 && <span aria-hidden="true" style={{ marginRight: 5, color: UI.line }}>·</span>}
                    {metric.label} <b style={num}>{metric.value}</b>
                  </span>
                ))}
                {pendingCl.length > 0 && <span style={{ color: TINT.blue.fg }}>· 연결 중 {pendingCl.length}</span>}
              </span>
            </span>
          );
        })()}
        {/* 좁은 화면: 우측 컨트롤을 왼쪽 정렬로 되돌리고 줄바꿈해 상단 잘림/가로 넘침을 막는다. */}
        <span style={{ marginLeft: narrow ? 0 : "auto", display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <button className="product-focusable product-control" aria-label={editing ? "레이아웃 편집 완료" : "레이아웃 편집"} title={editing ? "편집 완료" : "레이아웃 편집"} aria-pressed={editing} onClick={() => setEditing(!editing)}
            style={{ width: 34, height: 34, display: "grid", placeItems: "center", border: `1px solid ${editing ? blueA(0.45) : UI.line}`, background: editing ? blueA(0.07) : UI.card, color: editing ? BLUE : UI.ink2, borderRadius: 9, padding: 0 }}>
            {editing ? <Check size={14} /> : <Pencil size={13} />}
          </button>
          <button className="product-focusable product-control" onClick={onAddRepo}
            style={{ height: 34, display: "flex", alignItems: "center", gap: 6, border: `1px solid ${UI.line}`, background: UI.card, color: UI.ink2, borderRadius: 9, padding: "0 13px", fontSize: TYPE.label, fontWeight: 600 }}>
            <span aria-hidden="true" style={{ width: 16, height: 16, flexShrink: 0, display: "grid", placeItems: "center", lineHeight: 0 }}>
              <GithubIcon size={15} />
            </span>
            <span>저장소 연결</span>
          </button>
          <button className="product-focusable product-action" onClick={onConnect}
            style={{ height: 34, display: "flex", alignItems: "center", gap: 6, border: "none", background: BLUE, color: UI.card, borderRadius: 9, padding: "0 13px", fontSize: TYPE.label, fontWeight: 600 }}>+ 클러스터 연결</button>
        </span>
      </div>

      {/* 위젯은 4열 unit을 공유하고 각 슬롯의 선택 span을 보존한다. 준비/빈/오류/로딩은
          같은 고정 래퍼 안에서만 바뀌므로 데이터 전환 중 레이아웃 이동이 없다. */}
      <div className={DASHBOARD_WIDGET_GRID_CLASS} data-dashboard-widget-grid="four-column" style={dashboardWidgetGridStyle()}>
        {visible.map((id) => {
          const widgetType = board.types[id] ?? id;
          const def = W_DEFS.find((w) => w.id === widgetType) ?? W_DEFS.find((w) => w.id === id)!;
          const span = board.spans[id] ?? def.defaultSpan;
          return (
            <motion.div key={id} layout transition={SPRING} className={DASHBOARD_WIDGET_GRID_ITEM_CLASS}
              data-dashboard-widget-slot={id} data-dashboard-widget-span={span}
              style={{ ...dashboardWidgetItemStyle(span), opacity: dragId === id ? 0.55 : 1 }}>
              {/* 네이티브 드래그는 플레인 래퍼가 담당 — motion의 팬 제스처 onDragStart와 충돌 방지 */}
              <div draggable={editing}
                onDragStart={editing ? (e: React.DragEvent) => { setDragId(id); e.dataTransfer.effectAllowed = "move"; } : undefined}
                onDragOver={editing ? (e: React.DragEvent) => { e.preventDefault(); dragOverWidget(id); } : undefined}
                onDragEnd={editing ? () => setDragId(null) : undefined}
                style={{ height: "100%", cursor: editing ? "grab" : undefined }}>
              <WidgetFrame title={def.title} info={def.info}
                onDeepLink={onWidgetDeepLink ? () => onWidgetDeepLink(widgetType) : undefined}
                editing={editing}
                span={span}
                widgetType={widgetType}
                widgetTypes={W_DEFS.map(({ id: typeId, title }) => ({ id: typeId, title }))}
                onSpanChange={(nextSpan) => save({ ...board, spans: { ...board.spans, [id]: nextSpan } })}
                onTypeChange={(nextType) => save({ ...board, types: { ...board.types, [id]: nextType } })}
                onEdit={() => setEditing(true)}
                onRemove={() => save({ ...board, hidden: [...board.hidden, id] })}>
                {body(widgetType)}
              </WidgetFrame>
              </div>
            </motion.div>
          );
        })}
        {editing && hiddenSlots.length > 0 && (
          <div style={{ gridColumn: "1 / -1", display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", border: `1.5px dashed ${UI.line}`, borderRadius: RADIUS.card, padding: "11px 14px" }}>
            <span style={{ fontSize: TYPE.label, fontWeight: 600, color: UI.ink3, whiteSpace: "nowrap" }}>위젯 추가</span>
            {hiddenSlots.map((id) => {
              const def = W_DEFS.find((w) => w.id === (board.types[id] ?? id)) ?? W_DEFS.find((w) => w.id === id)!;
              return (
                <button key={id} className="product-focusable product-control" onClick={() => save({ ...board, hidden: board.hidden.filter((x) => x !== id) })}
                  style={{ border: `1px solid ${UI.line}`, background: UI.card, color: UI.ink, borderRadius: 999, padding: "4px 12px", fontSize: TYPE.label, fontWeight: 600, cursor: "pointer", whiteSpace: "nowrap" }}>+ {def.title}</button>
              );
            })}
          </div>
        )}
      </div>
    </main>
  );
}

// ── 앱 ─────────────────────────────
// 종류 선택 → 맵 '연결 보기' 탭 매핑 (같은 축은 한 몸으로 움직인다)
// P1: 워크스페이스 표시명 — 백엔드 workspace_id는 "default"지만 제품 표기는 "Krafton Jungle".
// 다른 워크스페이스 id는 그대로 노출한다(지어내지 않음).
function workspaceLabel(id: string | null | undefined): string {
  if (id == null || id === "") return "워크스페이스 확인 중";
  return id === "default" ? "Krafton Jungle" : id;
}

const lensTabFor = (id: string): "svc" | "cfg" | "git" | null =>
  id === "Service" ? "svc"
  : id === "ConfigMap" || id === "Secret" ? "cfg"
  : ["Application", "ApplicationSet", "AppProject"].includes(id) ? "git"
  : null;

type SessionNote = {
  id: number;
  icon: "rule" | "connect";
  title: string;
  body: string;
  lifecycleClusterId?: string;
};

type ToastTone = "ok" | "crit";
type ToastPayload = {
  title: string;
  sub: string;
} & (
  | { tone: ToastTone; color?: never; Icon?: never }
  | { tone?: never; color: string; Icon: AlertEventIcon }
);
type ToastMessage = ToastPayload & { id: number };

function isClusterLifecycleNote(note: SessionNote, clusterId: string): boolean {
  return note.lifecycleClusterId === clusterId
    || (note.icon === "connect" && note.title.startsWith(`${clusterId} · `));
}

function shouldPinRcaRecovery(update: RecoveryProgressOverride): boolean {
  return update.selectionPending
    || update.selectionAccepted
    || update.selectionFailed
    || Boolean(update.actionRoute?.trim())
    || Boolean(update.reasonCode?.trim());
}

function App() {
  const contract = useDevpreviewContracts();
  // 헤더 계정/워크스페이스/로그아웃 — 실 GET /api/auth/session(하드코딩 세션 제거).
  const session = useSession();
  // 인증 세션이 워크스페이스 정체성의 기준이다. 클러스터 목록은 비어 있거나 늦게
  // 도착할 수 있으므로 보조 근거로만 사용하고, 값이 확정되기 전에는 운영 스모크가
  // 완성된 identity로 오인하지 않도록 별도의 loading slot을 노출한다.
  const workspaceIdentityId = session.workspaceId ?? contract.workspaceId;
  const rcaIssuePinsStorageKey = useMemo(
    () => rcaIssuePinStorageKey(workspaceIdentityId),
    [workspaceIdentityId],
  );
  const [rcaIssuePinsSnapshot, setRcaIssuePinsSnapshot] = useState<{ storageKey: string; pins: StoredRcaIssuePin[] }>(() => {
    const storageKey = rcaIssuePinStorageKey(workspaceIdentityId);
    return { storageKey, pins: readStoredRcaIssuePins(storageKey) };
  });
  const rcaIssuePins = rcaIssuePinsSnapshot.storageKey === rcaIssuePinsStorageKey
    ? rcaIssuePinsSnapshot.pins
    : readStoredRcaIssuePins(rcaIssuePinsStorageKey);
  const pinnedIssueCorrelationIds = useMemo(
    () => rcaIssuePins.map((pin) => pin.correlationId),
    [rcaIssuePins],
  );
  const clusterIds = useMemo(() => contract.clusters.map((cluster) => cluster.id), [contract.clusters]);
  const incidentClusterIds = useMemo(
    () => activeIncidentClusterIds(contract.clusters),
    [contract.clusters],
  );
  const [rcaIncident, setRcaIncident] = useState<RcaIncident | null>(null); // 이슈 RCA 사이드바 — 셸 레벨 렌더(transform 조상 밖)
  const promoteAlertRcaIssue = useCallback((items: RcaIssueDetailView[]) => {
    setRcaIncident((current) => promoteAlertIncident(current, items));
  }, []);
  const alertRcaIssues = useRcaIssueDetails(
    alertIncidentClusterIds(rcaIncident, incidentClusterIds),
    alertIncidentPollMs(rcaIncident),
    promoteAlertRcaIssue,
  );
  const initialShellRoute = useMemo(
    () => parseShellRoute(window.location.search),
    [],
  );
  const [kindId, setKindId] = useState("Deployment");
  const [resView, setResView] = useState<ResView>(initialShellRoute.resourceView); // D18 관점 — 지도가 기본, 스코프는 관점 공유
  const [trafficFocus, setTrafficFocus] = useState<string | null>(null); // 트래픽 보조 패널 → 그래프 포커스
  const [showEmpty, setShowEmpty] = useState(false);
  const [pinned, setPinned] = useState<string[]>([]);
  const [q, setQ] = useState(""); // 단일 검색 — 종류 인덱스와 표 행을 동시에 필터
  const [surface, setSurface] = useState<Surface>(initialShellRoute.surface); // 셸 내 서피스 전환 — 홈이 랜딩(D19)
  const [deployRepositoryFilter, setDeployRepositoryFilter] = useState<string | null>(null);
  const [deployApplicationDetailId, setDeployApplicationDetailId] = useState<string | null>(null);
  const [selectedAlertEventId, setSelectedAlertEventId] = useState<string | null>(null);
  const [scope, setScope] = useState<{ level: string; cluster?: string; node?: string }>(
    initialShellRoute.clusterId
      ? { level: "nodes", cluster: initialShellRoute.clusterId }
      : { level: "clusters" },
  );
  const [ns, setNs] = useState("모든 네임스페이스");
  const [nsOpen, setNsOpen] = useState(false);
  const [clusterOpen, setClusterOpen] = useState(false);
  const [bellOpen, setBellOpen] = useState(false);
  // 네임스페이스 셀렉트 — 실 관측 네임스페이스(파드 관측 기반)로 구동. 하드코딩 목록 제거.
  // 계약이 unavailable/빈이면 "모든 네임스페이스"만 남는다.
  const resourcesListActive = surface === "resources" && resView === "list";
  const resourcesDrillActive = surface === "resources" && resView === "map" && scope.level !== "clusters";
  const namespaceClusterIds = surface === "home"
    ? clusterIds
    : resourcesListActive
    ? clusterIds
    : resourcesDrillActive && scope.cluster ? [scope.cluster] : [];
  const nsFeed = useInventoryNamespaces(namespaceClusterIds);
  const nsOptions = useMemo(() => ["모든 네임스페이스", ...nsFeed.items.map((item) => item.namespace)], [nsFeed.items]);
  const selectedNamespace = ns === "모든 네임스페이스" ? null : ns;
  const [meOpen, setMeOpen] = useState(false); // 계정 메뉴 (헤더 맨 오른쪽, D20)
  const [detail, setDetail] = useState<{ kind: Kind; row: Row } | null>(null);
  const [recoveryProgressOverrides, setRecoveryProgressOverrides] = useState<ReadonlyMap<string, RecoveryProgressOverride>>(() => new Map());
  const [navCollapsed, setNavCollapsed] = useState(false);
  const [aiOpen, setAiOpen] = useState(false);
  const [aiMounted, setAiMounted] = useState(false);
  const [aiRecoveryRequest, setAiRecoveryRequest] = useState<AiRecoveryHandoff | null>(null);
  const [aiRecoveryReviewState, setAiRecoveryReviewState] = useState<RecoveryReviewState>("idle");
  const [aiFull, setAiFull] = useState(false);         // AI 패널 전체 화면 (헤더 ⤢ 토글)
  const [aiW, setAiW] = useState(440);                 // 실제 제품처럼 리사이즈 가능한 도킹 폭
  const [aiDragging, setAiDragging] = useState(false);
  const aiPanelRef = useRef<HTMLDivElement>(null);
  const aiPreviousFocusRef = useRef<HTMLElement | null>(null);
  const aiRestoreFocusRef = useRef(false);
  const [drillCl, setDrillCl] = useState<string | null>(initialShellRoute.clusterId); // 홈 카드 → 지도 드릴 스코프 전달(D21)
  const [connectView, setConnectView] = useState<null | "repo" | "cluster">(null); // 연결 위저드 딥오픈 대상 (설정 서피스)
  const [connectModal, setConnectModal] = useState<null | "repo" | "cluster">(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get("github_app_installation_id") ? "repo" : null;
  }); // 문맥 진입 = 모달 팝업
  const openClusterDrill = useCallback((clusterId: string) => {
    const search = updateShellRouteSearch(window.location.search, {
      surface: "resources",
      resourceView: "map",
      clusterId,
    });
    window.history.pushState(
      window.history.state,
      "",
      `${window.location.pathname}${search}${window.location.hash}`,
    );
    setDrillCl(clusterId);
    setScope({ level: "nodes", cluster: clusterId });
    setSurface("resources");
    setResView("map");
  }, []);
  useEffect(() => {
    const onPopState = () => {
      const route = parseShellRoute(window.location.search);
      setSurface(route.surface);
      setResView(route.resourceView);
      setDrillCl(route.clusterId);
      setScope(
        route.clusterId
          ? { level: "nodes", cluster: route.clusterId }
          : { level: "clusters" },
      );
      setDetail(null);
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);
  useEffect(() => {
    const search = updateShellRouteSearch(window.location.search, {
      surface,
      resourceView: resView,
      clusterId:
        surface === "resources" && resView === "map" ? drillCl : null,
    });
    if (search === window.location.search) return;
    window.history.replaceState(
      window.history.state,
      "",
      `${window.location.pathname}${search}${window.location.hash}`,
    );
  }, [drillCl, resView, surface]);
  const [connectionManagerOpen, setConnectionManagerOpen] = useState(false);
  const [resumeClusterConnection, setResumeClusterConnection] = useState<ResumeClusterConnection | null>(null);
  const [repositoryConnectContext, setRepositoryConnectContext] = useState<RepositoryConnectionContext | null>(null);
  const showAi = useCallback(() => {
    if (!aiOpen && document.activeElement instanceof HTMLElement) {
      aiPreviousFocusRef.current = document.activeElement;
    }
    setAiMounted(true);
    setAiOpen(true);
  }, [aiOpen]);
  const openAi = useCallback((request?: AiRecoveryHandoff) => {
    if (!aiOpen && document.activeElement instanceof HTMLElement) {
      aiPreviousFocusRef.current = document.activeElement;
    }
    if (request !== undefined) setAiRecoveryRequest(request);
    setAiMounted(true);
    setAiOpen(true);
  }, [aiOpen]);
  const closeAi = useCallback(() => {
    aiRestoreFocusRef.current = true;
    const previousFocus = aiPreviousFocusRef.current;
    if (
      aiPanelRef.current?.contains(document.activeElement)
      && previousFocus?.isConnected
    ) {
      previousFocus.focus({ preventScroll: true });
    }
    setAiOpen(false);
    setAiFull(false);
  }, []);
  useEffect(() => {
    if (aiOpen || !aiRestoreFocusRef.current) return;
    const frame = window.requestAnimationFrame(() => {
      const previousFocus = aiPreviousFocusRef.current;
      const fallback = document.querySelector<HTMLElement>(
        '[aria-label="AI 어시스턴트 열기"]',
      );
      (previousFocus?.isConnected ? previousFocus : fallback)?.focus({
        preventScroll: true,
      });
      aiRestoreFocusRef.current = false;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [aiOpen]);
  const [manifestRefreshKey, setManifestRefreshKey] = useState(0);
  // 세션 중 등록한 연결 대기 항목 — 등록의 결과가 목록에 보여야 한다(로그아웃=세션 초기화로 함께 소멸)
  const [pendingCl, setPendingCl] = useState<string[]>(() => { try { return JSON.parse(sessionStorage.getItem(PENDING_CLUSTER_STORAGE_KEY) || "[]"); } catch { return []; } });
  const [pendingRepo, setPendingRepo] = useState<string[]>(() => { try { return JSON.parse(sessionStorage.getItem("opsia-demo-pending-repo") || "[]"); } catch { return []; } });
  const applicationsActive = surface === "home"
    || surface === "resources"
    || surface === "deploy"
    || pendingRepo.length > 0;
  const repositoryApplications = useApplications(manifestRefreshKey, applicationsActive);
  const connectedRepos = useMemo(
    () => Array.from(new Set(repositoryApplications.items.map((item) => item.repositoryRef).filter((ref): ref is string => Boolean(ref)))).sort(),
    [repositoryApplications.items],
  );
  const repositoryGroups = useMemo(
    () => groupApplicationsByRepository(repositoryApplications.items),
    [repositoryApplications.items],
  );
  const addPending = (scope: "cluster" | "repo", ref: string) => {
    const key = scope === "cluster" ? PENDING_CLUSTER_STORAGE_KEY : "opsia-demo-pending-repo";
    const set = scope === "cluster" ? setPendingCl : setPendingRepo;
    set((xs) => { const nx = xs.includes(ref) ? xs : [...xs, ref]; try { sessionStorage.setItem(key, JSON.stringify(nx)); } catch { /* 데모 */ } return nx; });
  };
  const removePendingClusters = (...references: unknown[]) => {
    setPendingCl((current) => {
      const next = removePendingClusterReferences(current, references);
      if (next === current) return current;
      try { sessionStorage.setItem(PENDING_CLUSTER_STORAGE_KEY, JSON.stringify(next)); } catch { /* 세션 저장 불가 */ }
      return next;
    });
  };
  // 서버 인벤토리에 실 클러스터가 나타나면 부트스트랩 임시 카드는 렌더 단계에서
  // 즉시 숨긴다. 서버 동기화 effect 안에서 다시 setState 하지 않아 중복 렌더를 막는다.
  const visiblePendingCl = useMemo(
    () => reconcilePendingClusters(pendingCl, contract.clusters),
    [contract.clusters, pendingCl],
  );
  useEffect(() => {
    if (visiblePendingCl === pendingCl) return;
    try { sessionStorage.setItem(PENDING_CLUSTER_STORAGE_KEY, JSON.stringify(visiblePendingCl)); } catch { /* 세션 저장 불가 */ }
  }, [pendingCl, visiblePendingCl]);
  // 연결 완료는 세션 타이머가 아니라 서버가 소유한 repository 상태로 확정한다.
  // 이전 구현은 pendingRepo를 추가만 하고 제거하지 않아 active 저장소도 영원히
  // "초기 동기화 대기"로 남았다. 서버가 ready를 반환하는 즉시 대기 목록과
  // sessionStorage를 원자적으로 정리하고 Application/GitOps 목록을 다시 읽는다.
  useEffect(() => {
    if (pendingRepo.length === 0) return;
    const controller = new AbortController();
    let timer: number | undefined;
    const reconcile = async () => {
      const statuses = await Promise.all(pendingRepo.map(async (repoRef) => {
        try {
          return await getRepositoryConnectionStatus(repoRef, controller.signal);
        } catch {
          return null;
        }
      }));
      if (controller.signal.aborted) return;
      const ready = new Set(
        statuses
          .filter((status) => status?.terminal && status.connection_stage === "ready" && status.repository_status === "active")
          .map((status) => status!.repo_ref),
      );
      if (ready.size > 0) {
        setPendingRepo((current) => {
          const next = current.filter((repoRef) => !ready.has(repoRef));
          try { sessionStorage.setItem("opsia-demo-pending-repo", JSON.stringify(next)); } catch { /* 세션 저장 불가 */ }
          return next;
        });
        setManifestRefreshKey((current) => current + 1);
        return;
      }
      const refreshSeconds = statuses.reduce((minimum, status) => {
        const seconds = status?.refresh_after_seconds;
        return typeof seconds === "number" ? Math.min(minimum, seconds) : minimum;
      }, 1);
      timer = window.setTimeout(() => { void reconcile(); }, refreshSeconds * 1000);
    };
    void reconcile();
    return () => {
      controller.abort();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [pendingRepo]);
  const onAiHandleDown = (e: React.PointerEvent) => {
    e.preventDefault(); setAiDragging(true);
    const move = (ev: PointerEvent) => setAiW(clampSidePanelWidth(
      (document.documentElement.clientWidth - ev.clientX) / PRESENT_SCALE,
      380,
      560,
    ));
    const up = () => { setAiDragging(false); window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
  };
  const onAiHandleKeyDown = (event: React.KeyboardEvent) => {
    const nextWidth = sidePanelWidthFromKeyboard({
      currentWidth: aiW,
      key: event.key,
      maximumWidth: 560,
      minimumWidth: 380,
      shiftKey: event.shiftKey,
    });
    if (nextWidth === null) return;
    event.preventDefault();
    setAiW(nextWidth);
  };
  const scopeLabel = scope.level === "clusters" ? "전체 클러스터" : scope.level === "nodes" ? `클러스터 ${scope.cluster}` : `노드 ${scope.node}`;
  const kind = KINDS.find((k) => k.id === kindId)!;
  const togglePin = (id: string) => setPinned((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  const searchRef = useRef<HTMLInputElement>(null);
  const contentScrollRef = useRef<HTMLDivElement>(null);
  // 상단 크롬 높이 — 폰트·확대에 따라 변하므로 실측해서 오버레이 기준으로 쓴다
  const headerRef = useRef<HTMLElement>(null);
  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node) || !headerRef.current?.contains(target)) {
        setBellOpen(false);
        setMeOpen(false);
        setNsOpen(false);
        setClusterOpen(false);
      }
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    return () => document.removeEventListener("pointerdown", onPointerDown, true);
  }, []);
  const [topH, setTopH] = useState(TOPBAR_H);
  useEffect(() => {
    const el = headerRef.current; if (!el) return;
    // offsetHeight = CSS 픽셀 — zoom 컨테이너 안의 fixed top과 같은 좌표계 (시각 픽셀로 재면 zoom만큼 밀린다)
    const ro = new ResizeObserver(() => setTopH(el.offsetHeight));
    ro.observe(el); return () => ro.disconnect();
  }, []);
  // 서피스·관점·물리 드릴·종류 전환 = 새 화면. 실제 소유 스크롤 컨테이너를 초기화해
  // 긴 이슈/타임라인/노드 목록의 위치를 다음 화면으로 승계하지 않는다.
  useEffect(() => {
    contentScrollRef.current?.scrollTo({ top: 0, left: 0, behavior: "auto" });
  }, [surface, resView, scope.level, scope.cluster, scope.node, kindId]);
  // zoom 좌표계: fixed 오버레이 계산은 전부 CSS 픽셀(뷰포트/스케일)로
  const [vwCss, setVwCss] = useState(() => document.documentElement.clientWidth / PRESENT_SCALE);
  useEffect(() => {
    // 스크롤바 등장/소멸로 clientWidth가 바뀌는 경우까지 관찰 (window resize 이벤트로는 못 잡는다)
    const on = () => setVwCss(document.documentElement.clientWidth / PRESENT_SCALE);
    const ro = new ResizeObserver(on); ro.observe(document.documentElement);
    window.addEventListener("resize", on);
    return () => { ro.disconnect(); window.removeEventListener("resize", on); };
  }, []);
  // 반응형 리소스 목록 — 좁은 화면(실뷰포트 ≤768px)에서는 종류 사이드바(248px)가
  // 표를 덮어 행 클릭이 불가하던 결함을 없앤다. 이 폭에서는 사이드바를 상단 종류
  // 선택 컨트롤로 접고 표를 전체 폭으로 스택해 행·상세 드로어 도달성을 보장한다.
  // vwCss는 PRESENT_SCALE(zoom)로 나눈 콘텐츠 좌표라 실뷰포트 기준으로 환산한다.
  const narrowList = vwCss <= 768 / PRESENT_SCALE;
  const narrowFlow = vwCss <= 1100 / PRESENT_SCALE;
  // 반응형 — 좁은 화면(200% 확대 등)에서 내비를 자동으로 아이콘만 남긴다
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 1100px)");
    const on = () => { if (mq.matches) setNavCollapsed(true); };
    on(); mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);

  // 표 데이터: gateway가 단일 클러스터 경로만 제공하므로 전체 범위는 실제 클러스터별
  // 요청을 병렬 수행한 합집합이다. 카운트와 표가 같은 clusterIds를 사용해 2029/0 같은
  // 불일치가 생기지 않는다.
  const resourceClusterIds = resourcesListActive
    ? (scope.cluster ? [scope.cluster] : clusterIds)
    : [];
  const resourcesView = useInventoryResourcesAcrossClusters(resourceClusterIds, kindToResourceType(kindId));
  const allRows = resourcesView.rows;
  const inScope = scope.level !== "clusters" && !!scope.cluster;
  // 물리 토폴로지는 리소스 맵 드릴에서만 구독한다. 사용자가 목록·홈·이슈 등으로
  // 이동한 뒤에도 이전 scope가 남아 무거운 60초 reconciliation을 계속하지 않는다.
  const activeTopologyCluster = surface === "resources"
    && resView === "map"
    && scope.level !== "clusters"
    ? scope.cluster ?? null
    : null;
  const scopedTopology = useClusterTopology(activeTopologyCluster);
  const scopedNode = scope.level === "pods"
    ? scopedTopology.nodes.find((node) => node.name === scope.node)
    : undefined;
  const scopedNodePodKeys = useMemo(() => new Set(
    scopedNode ? podsForNode(scopedTopology.pods, scopedNode.key).map((pod) => pod.key) : [],
  ), [scopedNode, scopedTopology.pods]);
  // 행에 관측된 클러스터 귀속(cluster 필드)이 있으면 스코프와 일치할 때만 남긴다.
  // cluster 필드가 없으면 이름 해싱으로 귀속을 지어내지 않고 그대로 둔다(필터하지 않음).
  const scopedRows = useMemo(() => (inScope ? allRows.filter((row) => {
    const cl = typeof row.cluster === "string" ? row.cluster : "";
    if (cl !== "" && cl !== scope.cluster) return false;
    if (scope.level !== "pods" || kindId !== "Pod") return true;
    return scopedNodePodKeys.has(String(row._key ?? ""));
  }) : allRows), [allRows, inScope, kindId, scope.cluster, scope.level, scopedNodePodKeys]);
  const nsRows = useMemo(() => (ns === "모든 네임스페이스" ? scopedRows : scopedRows.filter((r) => r.ns === undefined || String(r.ns) === ns)), [scopedRows, ns]);
  const shownRows = useMemo(() => (q ? nsRows.filter((r) => String(r.name ?? "").toLowerCase().includes(q.toLowerCase())) : nsRows), [nsRows, q]);
  // 클러스터 카드 메타 — 라이브 인벤토리 요약(GET .../inventory/summary)의 종류별 카운트.
  // useInventoryKindCounts는 resource_type 키(소문자) 맵을 주므로, 소비처가 기대하는
  // kind 표기(Deployment 등)로 kindToResourceType 매핑을 통해 조회한다.
  const kindCountsView = useInventoryKindCounts(
    resourcesListActive || resourcesDrillActive ? clusterIds : [],
  );
  const clusterMeta = useMemo(() => {
    const kinds = ["Deployment", "StatefulSet", "DaemonSet", "Service", "Ingress", "Job", "CronJob", "Namespace"] as const;
    const meta: Record<string, Record<string, number>> = {};
    for (const cl of clusterIds) {
      meta[cl] = {};
      for (const k of kinds) meta[cl][k] = kindCountsView.meta[cl]?.[kindToResourceType(k)] ?? 0;
    }
    return meta;
  }, [clusterIds, kindCountsView.meta]);
  // 사이드바(KindIndex) 카운트 — 전 클러스터 합산 (kindId → resource_type로 조회).
  const kindCounts = useMemo(() => {
    const out: Record<string, number> = {};
    const countedClusters = scope.cluster ? [scope.cluster] : clusterIds;
    for (const k of KINDS) {
      const rt = kindToResourceType(k.id);
      let sum = 0;
      for (const cl of countedClusters) sum += kindCountsView.meta[cl]?.[rt] ?? 0;
      out[k.id] = sum;
    }
    if (scope.level === "pods" && scopedNode) {
      out.Pod = podsForNode(scopedTopology.pods, scopedNode.key).length;
    }
    return out;
  }, [clusterIds, kindCountsView.meta, scope.cluster, scope.level, scopedNode, scopedTopology.pods]);
  const openFromMap = (kid: string, data: Record<string, unknown>) => {
    const k = KINDS.find((x) => x.id === kid); if (k) setDetail({ kind: k, row: data });
  };

  // 알림 — 인벤토리 파생: 임계 파드(위험) + 예약 중 노드(정보) + OutOfSync 저장소(경고)
  // 실 알림 이벤트(GET /api/... alert-events) — fixture 인벤토리 파생 알림 제거. 관측 안 되면 세션 알림(notes)만.
  const alertEvents = useAlertEvents();
  // 세션 알림 — 위저드 연결·AI 규칙 생성 등 실제 사용자 행동의 결과
  const [notes, setNotes] = useState<SessionNote[]>([]);
  const noteSeq = useRef(0);
  const [readAlertIds, setReadAlertIds] = useState<Set<string>>(() => new Set());
  const liveAlerts = useMemo(
    () => (alertEvents.status === "ready" ? alertEvents.items : []),
    [alertEvents],
  );
  const unreadAlerts = useMemo(
    () => liveAlerts.filter(
      (event) => (
        event.status === "firing"
        && !readAlertIds.has(alertEventOccurrenceKey(event))
      ),
    ),
    [liveAlerts, readAlertIds],
  );
  const alertTotal = unreadAlerts.length + notes.length;
  const alertBadge = alertTotal > 5 ? "5+" : String(alertTotal);
  const alertBadgePresentation = useMemo(
    () => strongestAlertEventPresentation(unreadAlerts),
    [unreadAlerts],
  );
  const [bellRingVersion, setBellRingVersion] = useState(0);
  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  const toastSeq = useRef(0);
  const pushToast = useCallback((t: ToastPayload) => {
    const id = ++toastSeq.current;
    setToasts((cur) => [...cur, { id, ...t }].slice(-5));
    window.setTimeout(() => setToasts((cur) => cur.filter((x) => x.id !== id)), 3800);
  }, []);
  const ringBell = useCallback(() => {
    setBellRingVersion((version) => version + 1);
  }, []);
  const markAlertRead = useCallback((event: AlertEventView) => {
    const occurrenceKey = alertEventOccurrenceKey(event);
    setReadAlertIds((current) => {
      const next = new Set(current);
      next.add(occurrenceKey);
      return next;
    });
    void acknowledgeAlertEvent(event.eventId).catch(() => {
      setReadAlertIds((current) => {
        const next = new Set(current);
        next.delete(occurrenceKey);
        return next;
      });
      pushToast({
        title: "알림 읽음 처리 실패",
        sub: "잠시 후 다시 시도해 주세요.",
        tone: "crit",
      });
    });
  }, [pushToast]);
  const markAllAlertsRead = useCallback(() => {
    const allIds = unreadAlerts.map(alertEventOccurrenceKey);
    setReadAlertIds((current) => new Set([...current, ...allIds]));
    setNotes([]);
    if (unreadAlerts.length === 0) return;
    void Promise.allSettled(
      unreadAlerts.map((event) => acknowledgeAlertEvent(event.eventId)),
    ).then((results) => {
      const failedIds = results.flatMap((result, index) =>
        result.status === "rejected"
          ? [alertEventOccurrenceKey(unreadAlerts[index])]
          : []
      );
      if (failedIds.length === 0) return;
      setReadAlertIds((current) => {
        const next = new Set(current);
        for (const eventId of failedIds) next.delete(eventId);
        return next;
      });
      pushToast({
        title: "일부 알림을 읽음 처리하지 못했습니다",
        sub: "잠시 후 다시 시도해 주세요.",
        tone: "crit",
      });
    });
  }, [pushToast, unreadAlerts]);
  const notifiedIncidentAlerts = useRef<Set<string> | null>(null);
  useEffect(() => {
    if (alertEvents.status !== "ready") return;
    const currentIds = new Set(liveAlerts.map(alertEventOccurrenceKey));
    if (notifiedIncidentAlerts.current === null) {
      notifiedIncidentAlerts.current = currentIds;
      return;
    }
    let receivedNewIncident = false;
    for (const event of liveAlerts) {
      const occurrenceKey = alertEventOccurrenceKey(event);
      if (
        notifiedIncidentAlerts.current.has(occurrenceKey)
        || !isIncidentNotification(event)
      ) continue;
      receivedNewIncident = true;
      const presentation = alertEventPresentation(event);
      pushToast({
        title: `장애 발생 · ${event.ruleName ?? event.name}`,
        sub: `${event.cluster} · ${event.kind} ${event.name}`,
        color: presentation.color,
        Icon: presentation.Icon,
      });
    }
    if (receivedNewIncident) ringBell();
    for (const eventId of currentIds) notifiedIncidentAlerts.current.add(eventId);
  }, [alertEvents.status, liveAlerts, pushToast, ringBell]);
  // 목록(⋮ 메뉴)에서 연 연결 해제 대상 — 상세 뷰와 같은 다이얼로그를 제어형으로 연다.
  const [listDisconnectClusterId, setListDisconnectClusterId] = useState<string | null>(null);
  const listDisconnectChoice = useMemo(() => {
    const target = contract.clusters.find((cluster) => cluster.id === listDisconnectClusterId);
    return target ? toHomeClusterChoice(target) : null;
  }, [contract.clusters, listDisconnectClusterId]);
  const reportClusterLifecyclePhase = (
    clusterId: string,
    phase: "confirm" | "submitting" | "uninstalling" | "cleanup-required" | "residual-cleanup" | "succeeded" | "failed",
  ) => {
    if (phase === "confirm") return;
    const message = {
      submitting: "연결 해제 요청을 서버에 전달했습니다",
      uninstalling: "에이전트가 Deployment·ServiceAccount·RBAC 정리를 수행 중입니다",
      "cleanup-required": "서버가 자동 정리 미완료를 반환했습니다",
      "residual-cleanup": "등록과 자격 증명이 폐기됐으며 서버가 보고한 잔여 리소스 확인이 필요합니다",
      succeeded: "서버가 관리 DB 등록 및 자격 증명 폐기를 확인했습니다",
      failed: "서버가 연결 해제 실패를 반환했습니다",
    }[phase];
    const title = `${clusterId} · ${phase === "failed" ? "연결 해제 실패" : "연결 해제"}`;
    const clearLifecycleNote = phase === "succeeded";
    setNotes((current) => {
      const withoutPreviousLifecycle = current.filter((note) => !isClusterLifecycleNote(note, clusterId));
      if (clearLifecycleNote) return withoutPreviousLifecycle;
      return [
        { id: ++noteSeq.current, icon: "connect", title, body: message, lifecycleClusterId: clusterId },
        ...withoutPreviousLifecycle,
      ];
    });
    pushToast({ title, sub: message, tone: phase === "failed" ? "crit" : "ok" });
  };
  // 관련 리소스 이동 — 현재 로드된 라이브 rows에서 이름으로 찾고,
  // 없으면 최소 객체({name, ns})로 상세를 연다(기존 fallback 유지).
  const openRef = (kid: string, name: string) => {
    const k = KINDS.find((x) => x.id === kid); if (!k) return;
    const found = kid === kindId
      ? allRows.find((r) => String(r.name) === name || String(r.name).startsWith(name))
      : undefined;
    setDetail({ kind: k, row: found ?? { name } });
  };
  const openTrafficService = (node: RelationNodeView) => {
    // 관계 토폴로지는 Service뿐 아니라 Deployment/StatefulSet/ReplicaSet 같은
    // workload 노드도 반환한다. 상세를 무조건 Service로 열면 그래프의 kind와
    // 사이드 패널의 API 계약이 어긋나므로 관측된 kind를 그대로 보존한다.
    const resourceKindId = resolveTrafficResourceKindId(node.kind);
    const resourceKind = resourceKindId ? KINDS.find((item) => item.id === resourceKindId) : undefined;
    if (!resourceKind) return;
    setDetail({
      kind: resourceKind,
      row: {
        cluster: node.clusterId,
        name: node.name,
        ns: node.namespace ?? undefined,
        resource_type: node.resourceType,
      },
    });
  };
  // 버스 구독은 마운트 1회만 — 최신 openRef를 ref로 참조해 재구독 없이 호출한다.
  const openRefRef = useRef(openRef);
  useEffect(() => { openRefRef.current = openRef; });
  const contractRefreshRef = useRef(contract.refresh);
  useEffect(() => { contractRefreshRef.current = contract.refresh; });
  // 버스 수신 → 토스트 + 세션 알림 (선언은 위쪽, 여기서는 구독만)
  useEffect(() => onAction((a: DemoAction) => {
    // 내비게이션 액션 — AI 근거/링크가 셸의 실제 표면을 연다
    if (a.kind === "open_ref") { openRefRef.current(a.title, a.body); return; }
    if (a.kind === "open_crit") { setSurface("resources"); setResView("list"); setKindId("Pod"); return; }
    setNotes((n) => [{ id: ++noteSeq.current, icon: a.kind === "alert_rule" ? "rule" : "connect", title: a.title, body: a.body }, ...n]);
    pushToast({ title: a.title, sub: a.body, tone: "ok" });
    if (a.kind === "connect") {
      if (a.scope === "cluster") {
        if (a.ref) removePendingClusters(a.ref);
        contractRefreshRef.current();
      } else if (a.scope === "repo" && a.ref) {
        addPending(a.scope, a.ref);
      }
      window.setTimeout(() => setConnectModal(null), 400); // 연결 완료 → 모달 닫힘
    }
  }), [pushToast]);
  // 실 알림 이벤트의 절대 발생시각(월/일 HH:MM). Date.now() 상대시각은 렌더 순수성 위반이라 금지.
  const alertTime = (iso: string) => {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    const p2 = (n: number) => String(n).padStart(2, "0");
    return `${p2(d.getMonth() + 1)}/${p2(d.getDate())} ${p2(d.getHours())}:${p2(d.getMinutes())}`;
  };

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        // 최상위 표면부터 z-서열 역순으로 '한 겹씩' 닫는다: 팝오버 → 연결 모달 → 상세 → AI
        if (bellOpen || nsOpen || meOpen) { setBellOpen(false); setNsOpen(false); setMeOpen(false); }
        else if (connectModal) setConnectModal(null);
        else if (detail) setDetail(null);
        else if (rcaIncident) setRcaIncident(null);
        else if (aiOpen) closeAi();
      }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); searchRef.current?.focus(); }
    };
    window.addEventListener("keydown", h); return () => window.removeEventListener("keydown", h);
  }, [bellOpen, nsOpen, meOpen, connectModal, detail, rcaIncident, aiOpen, closeAi]);

  return (
    // 앱 셸 스크롤 규약 — 문서 스크롤 금지. 상단 크롬·내비는 고정하고 스크롤은
    // 콘텐츠 영역 안에서만 생긴다: 스크롤바가 상단바를 관통하지 않고,
    // 콘텐츠 높이 변동(폴링 갱신)으로 창 폭이 열닫히는 점프도 사라진다.
    <div className="uni" style={{ height: `calc(100vh / ${PRESENT_SCALE})`, overflow: "hidden", background: UI.bg, display: "flex", alignItems: "stretch", zoom: PRESENT_SCALE }}>
      {/* 전역 내비게이션 — 제품 셸의 바깥 틀 */}
      <GlobalNav collapsed={navCollapsed} setCollapsed={setNavCollapsed}
        surface={surface} onSurface={(sf) => {
          setRcaIncident(null);
          setDetail(null);
          setDeployApplicationDetailId(null);
          setSelectedAlertEventId(null);
          setAiOpen(false);
          setSurface(sf);
          setBellOpen(false);
          setMeOpen(false);
          setNsOpen(false);
          setClusterOpen(false);
          if (sf === "deploy") setDeployRepositoryFilter(null);
          if (sf === "connect") setConnectView(null);
        }} />

      <div aria-label="현재 화면 콘텐츠" role="region"
        style={{ flex: 1, minWidth: 0, height: "100%", display: "flex", flexDirection: "column", overflowX: "clip" }}>
      {/* 상단 크롬 — 워크스페이스·스코프·네임스페이스·검색. 셸이 문서 스크롤을 막으므로
          sticky 없이도 항상 고정된다. */}
      <header ref={headerRef} className="product-shell-header" style={{ zIndex: 74, flexShrink: 0, display: "flex", alignItems: "center", flexWrap: "wrap", gap: "var(--product-shell-header-gap, 10px)", padding: "var(--product-shell-header-padding, 12px 18px)", borderBottom: `1px solid ${UI.line}`, background: UI.card }}>
        {/* 워크스페이스 — 정체성은 항상 맨 왼쪽(D20). 데모 세계는 워크스페이스 1개라 사실 표시만 */}
        <span
          className="product-workspace-label"
          data-slot={workspaceIdentityId ? "workspace-identity" : "workspace-identity-loading"}
          data-workspace-id={workspaceIdentityId ?? ""}
          style={{ display: "flex", alignItems: "center", gap: 7, minWidth: 0, maxWidth: "var(--product-workspace-max-width, 30%)", fontSize: TYPE.body, fontWeight: 600, color: UI.ink, paddingRight: "var(--product-workspace-padding-right, 12px)", borderRight: `1px solid ${UI.line2}`, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
        >
          <Building2 size={14} style={{ color: UI.ink3 }} />{workspaceLabel(workspaceIdentityId)}
        </span>
        {/* 새로고침 — 내부/기술 표기("실제 계약") 텍스트 제거, 상태점 + 아이콘만(P1-10) */}
        <button type="button" className="product-focusable product-control" aria-label="라이브 데이터 새로고침" onClick={contract.refresh}
          title={contract.error ?? "새로고침"}
          style={{ display: "flex", alignItems: "center", gap: 5, border: "none", background: "transparent", padding: 4, color: contract.status === "error" ? HP.crit : UI.ink3, cursor: "pointer" }}>
          <span className={contract.status === "loading" ? "livedot" : undefined}
            style={{ width: 6, height: 6, borderRadius: 999, background: contract.status === "error" ? HP.crit : contract.status === "ready" ? HP.ok : HP.pending }} />
          <RefreshCw size={13} style={{ color: contract.status === "error" ? HP.crit : UI.ink3 }} />
        </button>
        <button
          type="button"
          className="product-focusable product-control"
          aria-label="전역 연결 관리 열기"
          aria-haspopup="dialog"
          aria-expanded={connectionManagerOpen}
          onClick={() => {
            setBellOpen(false);
            setClusterOpen(false);
            setNsOpen(false);
            setConnectionManagerOpen(true);
          }}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            border: `1px solid ${connectionManagerOpen ? BLUE : UI.line}`,
            borderRadius: 9,
            background: connectionManagerOpen ? blueA(0.08) : UI.card,
            color: connectionManagerOpen ? BLUE : UI.ink2,
            padding: "6px 9px",
            fontSize: TYPE.label,
            fontWeight: 600,
            cursor: "pointer",
          }}
        >
          <Plug size={13} />
          <span className="hide-narrow">연결 관리</span>
        </button>
        {/* 현재 스코프 표시 — 물리 스코프가 실제 적용되는 관점(지도·목록)에서만. 흐름은 서비스 수준 */}
        {surface === "resources" && resView !== "flow" && (
        <span style={{ position: "relative" }}>
            <button type="button" className="product-focusable product-control" aria-label="클러스터 범위" aria-haspopup="listbox" aria-expanded={clusterOpen} onClick={() => { setClusterOpen((open) => !open); setNsOpen(false); }}
            style={{ display: "flex", alignItems: "center", gap: 7, maxWidth: "min(32vw, 260px)", border: `1px solid ${clusterOpen ? BLUE : UI.line}`, borderRadius: 9, padding: "6px 11px", fontSize: TYPE.body, fontWeight: 600, color: UI.ink, background: UI.card, cursor: "pointer" }}>
            <Server size={13} style={{ color: UI.ink3 }} />{scope.cluster ?? "전체 클러스터"}<ChevronDown size={12} style={{ color: UI.ink3, transform: clusterOpen ? "rotate(180deg)" : "none" }} />
          </button>
          <AnimatePresence>{clusterOpen && <motion.div initial={{ opacity: 0, y: -5 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} style={{ position: "absolute", top: 40, left: 0, minWidth: 220, zIndex: 65, background: UI.card, border: `1px solid ${blueA(0.35)}`, borderRadius: 12, boxShadow: `0 18px 50px -18px ${inkA(0.28)}`, padding: 5 }}>
            {[{ id: "", label: "전체 클러스터" }, ...contract.clusters.map((cluster) => ({ id: cluster.id, label: cluster.id }))].map((item) => <button key={item.id || "all"} className="product-focusable product-control" type="button" role="option" aria-selected={(scope.cluster ?? "") === item.id} onClick={() => { setDetail(null); if (item.id) openClusterDrill(item.id); else { setDrillCl(null); setScope({ level: "clusters" }); } setClusterOpen(false); }}
              style={{ display: "flex", alignItems: "center", width: "100%", gap: 8, border: "none", borderRadius: 8, padding: "8px 10px", background: (scope.cluster ?? "") === item.id ? blueA(0.1) : "transparent", color: (scope.cluster ?? "") === item.id ? BLUE : UI.ink, fontSize: TYPE.body, fontWeight: (scope.cluster ?? "") === item.id ? 600 : 500, textAlign: "left", cursor: "pointer" }}>{item.label}{(scope.cluster ?? "") === item.id && <Check size={14} style={{ marginLeft: "auto" }} />}</button>)}</motion.div>}</AnimatePresence>
        </span>
        )}
        {surface === "resources" && resView !== "flow" && (
          <ClusterLifecycleControl
            cluster={scope.cluster
              ? contract.clusters.find((cluster) => cluster.id === scope.cluster) ?? null
              : null}
            onDisconnected={(clusterId) => {
              const disconnected = contract.clusters.find((cluster) => cluster.id === clusterId);
              removePendingClusters(clusterId, disconnected?.name, disconnected?.displayName);
              if (scope.cluster === clusterId) {
                setScope({ level: "clusters" });
                setDrillCl(null);
              }
              contract.refresh();
            }}
            onPhaseChange={reportClusterLifecyclePhase}
            roles={session.roles}
          />
        )}
        {surface === "resources" && resView !== "flow" && (
        <span style={{ position: "relative" }}>
          <button type="button" className="product-focusable product-control" aria-label="네임스페이스 범위 선택" aria-haspopup="listbox" aria-expanded={nsOpen} onClick={() => { setNsOpen((open) => !open); setClusterOpen(false); }}
            style={{ display: "flex", alignItems: "center", gap: 7, maxWidth: "min(32vw, 260px)", overflow: "hidden", whiteSpace: "nowrap", textOverflow: "ellipsis", border: `1px solid ${nsOpen ? blueA(0.45) : UI.line}`, background: UI.card, borderRadius: 9, padding: "6px 11px", fontSize: TYPE.body, fontWeight: 600, color: ns === "모든 네임스페이스" ? UI.ink : BLUE, cursor: "pointer" }}>
            <Globe size={13} style={{ color: UI.ink3 }} />{ns}<ChevronDown size={12} style={{ color: UI.ink3, transform: nsOpen ? "rotate(180deg)" : "none", transition: "transform .15s" }} />
          </button>
          <AnimatePresence>
            {nsOpen && (
              <motion.div key="ns" initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={SOFT}
                style={{ position: "absolute", top: 40, left: 0, minWidth: 190, zIndex: 65, background: UI.card, border: `1px solid ${UI.line}`, borderRadius: 12, boxShadow: `0 18px 50px -18px ${inkA(0.28)}`, padding: 5, overflow: "hidden" }}>
                {nsOptions.map((o) => (
                  <button key={o} className="rrow product-focusable product-control" aria-selected={ns === o} onClick={() => { setNs(o); setNsOpen(false); }}
                    style={{ display: "flex", alignItems: "center", gap: 8, width: "100%", textAlign: "left", border: "none", background: ns === o ? blueA(0.08) : "transparent", borderRadius: 8, padding: "7px 10px", fontSize: TYPE.label, fontWeight: ns === o ? 600 : 500, color: ns === o ? BLUE : UI.ink, cursor: "pointer" }}>
                    {o}{ns === o && <Check size={12} style={{ marginLeft: "auto" }} />}
                  </button>
                ))}
              </motion.div>
            )}
          </AnimatePresence>
        </span>
        )}
        <div className="product-input-surface product-global-search" style={{ flex: "var(--product-global-search-flex, 1)", maxWidth: "var(--product-global-search-max-width, 520px)", margin: "var(--product-global-search-margin, 0 auto)", display: "flex", alignItems: "center", gap: 8, border: `1px solid ${UI.line}`, background: UI.bg2, borderRadius: 9, padding: "6px 12px", transition: "border-color 150ms ease, box-shadow 150ms ease" }}>
          <Search size={13} style={{ color: UI.ink3 }} />
          {/* 전역 검색(D6) — 홈에서 입력하면 결과가 있는 리소스 목록으로 이동한다(무반응 인풋 금지) */}
          <input ref={searchRef} aria-label="리소스와 화면 전체 검색" value={q}
            onChange={(e) => { const v = e.currentTarget.value; setQ(v); if (v && surface !== "resources") { setSurface("resources"); setResView("list"); } }}
            placeholder="전체 검색 — 리소스·화면 이동" style={{ border: "none", outline: "none", background: "transparent", fontSize: TYPE.body, color: UI.ink, width: "100%" }} />
          <span style={{ fontSize: TYPE.caption, fontFamily: MONO, color: UI.ink3, border: `1px solid ${UI.line}`, borderRadius: 4, padding: "1px 5px" }}>⌘K</span>
        </div>
        {narrowList && !aiOpen && (
          <button type="button" className="product-focusable product-control" aria-label="AI 어시스턴트 열기" title="AI 어시스턴트" onClick={showAi}
            style={{ width: 30, height: 30, flexShrink: 0, borderRadius: 9, border: "none", cursor: "pointer", background: blueA(0.1), color: BLUE, display: "grid", placeItems: "center" }}>
            <Sparkles size={15} />
          </button>
        )}
        {/* 알림 벨 — 배지 수는 맵의 장애 수와 같은 인벤토리에서 나온다 */}
        <span style={{ position: "relative" }}>
          <button type="button" className="gnav product-focusable product-control" aria-label="알림 센터 열기" aria-expanded={bellOpen} onClick={() => { setBellOpen((open) => !open); setMeOpen(false); }}
            style={{ width: 30, height: 30, borderRadius: 999, border: "none", background: bellOpen ? blueA(0.1) : inkA(0.045), color: bellOpen ? BLUE : UI.ink2, cursor: "pointer", display: "grid", placeItems: "center" }}>
            <motion.span key={bellRingVersion}
              initial={{ rotate: 0, scale: 1 }}
              animate={bellRingVersion > 0 ? { rotate: [0, -20, 18, -14, 10, -6, 0], scale: [1, 1.12, 1.08, 1.1, 1.04, 1] } : { rotate: 0, scale: 1 }}
              transition={{ duration: 0.72, ease: "easeInOut" }}
              style={{ display: "grid", placeItems: "center", transformOrigin: "50% 12%" }}>
              <Bell size={14} />
            </motion.span>
          </button>
          {alertTotal > 0 && (
            <span aria-hidden="true" style={{ position: "absolute", top: -3, right: -3, minWidth: 15, height: 15, borderRadius: 999, background: alertBadgePresentation?.color ?? HP.warn, color: UI.card, fontSize: TYPE.caption, fontWeight: 600, display: "grid", placeItems: "center", padding: "0 4px", border: `2px solid ${UI.card}`, boxSizing: "content-box", pointerEvents: "none" }}>{alertBadge}</span>
          )}
          <AnimatePresence>
            {/* 애플 알림 센터 스타일 — 반투명 블러 패널 위 카드 스택 */}
            {bellOpen && (
              <motion.div key="bell" initial={{ opacity: 0, y: -8, scale: 0.97 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: -5, scale: 0.98 }} transition={SOFT}
                style={{ position: "absolute", top: 38, right: 0, width: 344, zIndex: 65, background: GLASS, backdropFilter: "blur(26px)", WebkitBackdropFilter: "blur(26px)",
                  border: `1px solid ${inkA(0.08)}`, borderRadius: RADIUS.sheet, boxShadow: `0 28px 70px -24px ${inkA(0.38)}`, padding: 10, maxHeight: `min(calc(70vh / ${PRESENT_SCALE}), 560px)`, overflowY: "auto", overscrollBehavior: "contain", scrollbarGutter: "stable", scrollbarWidth: "thin" }}>
                <div style={{ position: "sticky", top: -10, zIndex: 2, display: "flex", alignItems: "center", gap: 7, margin: "-2px -2px 4px", padding: "4px 10px 10px", background: GLASS, backdropFilter: "blur(26px)", WebkitBackdropFilter: "blur(26px)" }}>
                  <span style={{ fontSize: TYPE.section, fontWeight: 700, letterSpacing: "-0.02em", color: UI.heading }}>알림</span>
                  <span style={{ fontSize: TYPE.caption, fontWeight: 600, color: UI.ink3 }}>{alertTotal}</span>
                  {alertTotal > 0 && (
                    <button type="button" onClick={markAllAlertsRead}
                      style={{ marginLeft: "auto", border: "none", background: "transparent", color: BLUE, padding: "3px 4px", fontSize: TYPE.caption, fontWeight: 600, cursor: "pointer" }}>
                      모두 읽음
                    </button>
                  )}
                </div>
                {(pendingCl.length + pendingRepo.length > 0) && (
                  <div style={{ padding: "0 8px 8px" }}>
                    <div style={{ fontSize: TYPE.caption, fontWeight: 600, letterSpacing: "0.05em", color: UI.ink3, padding: "0 2px 6px" }}>진행 중</div>
                    {[...pendingCl.map((n) => ({ id: `pc-${n}`, t: n, b: "에이전트 부트스트랩 · 첫 인벤토리 수집 대기" })), ...pendingRepo.map((n) => ({ id: `pr-${n}`, t: n, b: "초기 동기화 대기" }))].map((x) => (
                      <div key={x.id} style={{ display: "flex", alignItems: "center", gap: 10, background: cardA(0.85), border: `1px solid ${inkA(0.05)}`, borderRadius: RADIUS.card, padding: "10px 12px", marginBottom: 6 }}>
                        <span className="pulsedot" style={{ width: 8, height: 8, borderRadius: 999, background: BLUE, flexShrink: 0 }} />
                        <span style={{ minWidth: 0, flex: 1 }}>
                          <span style={{ display: "block", fontSize: TYPE.label, fontWeight: 600, fontFamily: MONO, color: UI.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{x.t}</span>
                          <span style={{ display: "block", fontSize: TYPE.caption, color: UI.ink2, marginTop: 1 }}>{x.b}</span>
                        </span>
                        <span style={{ width: 34, height: 4, borderRadius: 999, background: blueA(0.15), overflow: "hidden", flexShrink: 0 }}>
                          <motion.span initial={{ x: -20 }} animate={{ x: 34 }} transition={{ repeat: Infinity, duration: DUR.meter, ease: "easeInOut" }} style={{ display: "block", width: 20, height: "100%", borderRadius: 999, background: BLUE }} />
                        </span>
                      </div>
                    ))}
                    <div style={{ fontSize: TYPE.caption, fontWeight: 600, letterSpacing: "0.05em", color: UI.ink3, padding: "6px 2px 0" }}>최근</div>
                  </div>
                )}
                {(() => {
                  const Card = ({ icon: I, tint, title, body, time, right, onClick }: { icon: typeof Bell; tint: string; title: string; body: string; time: string; right?: string; onClick?: () => void }) => {
                    const content = (
                      <>
                      <span style={{ width: 28, height: 28, borderRadius: 8, background: tint, display: "grid", placeItems: "center", flexShrink: 0, marginTop: 1 }}>
                        <I size={14} color={UI.card} strokeWidth={2.2} />
                      </span>
                      <span style={{ minWidth: 0, flex: 1 }}>
                        <span style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
                          <span style={{ flex: 1, minWidth: 0, fontSize: TYPE.body, fontWeight: 600, fontFamily: MONO, color: UI.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{title}</span>
                          <span style={{ fontSize: TYPE.caption, color: UI.ink3, flexShrink: 0 }}>{time}</span>
                        </span>
                        <span style={{ display: "block", fontSize: TYPE.caption, color: UI.ink2, marginTop: 2, lineHeight: 1.45 }}>{body}</span>
                        {right && <span style={{ display: "block", fontSize: TYPE.caption, fontFamily: MONO, color: UI.ink3, marginTop: 3 }}>{right}</span>}
                      </span>
                      </>
                    );
                    const style: React.CSSProperties = {
                      display: "flex", alignItems: "flex-start", gap: 10, width: "100%",
                      textAlign: "left", background: cardA(0.85),
                      border: `1px solid ${inkA(0.05)}`, borderRadius: RADIUS.card,
                      padding: "10px 12px", marginBottom: 6,
                      boxShadow: `0 1px 2px ${inkA(0.05)}`,
                    };
                    return onClick ? (
                      <button type="button" className="acard" aria-label={`${title} 알림 상세 열기`} onClick={onClick} style={{ ...style, cursor: "pointer" }}>
                        {content}
                      </button>
                    ) : (
                      <div className="acard" role="status" aria-label={`${title} 알림`} style={style}>
                        {content}
                      </div>
                    );
                  };
                  return (
                    <>
                      {notes.map((nn) => (
                        <Card key={`note-${nn.id}`} icon={nn.icon === "rule" ? Bell : Plug} tint={nn.icon === "rule" ? BLUE : HP.ok} title={nn.title} time="방금" body={nn.body} />
                      ))}
                      {unreadAlerts.map((ev) => {
                        const presentation = alertEventPresentation(ev);
                        return (
                          <Card key={ev.eventId} icon={presentation.Icon}
                            tint={presentation.color} title={ev.name} time={alertTime(ev.firedAt)}
                            body={[statusLabel(ev.severity), statusLabel(ev.status), ev.kind, ev.namespace, ev.ruleName].filter(Boolean).join(" · ")}
                            right={ev.cluster} onClick={() => {
                              setBellOpen(false);
                              if (ev.incidentId) {
                                setSurface("issues");
                                setDetail(null);
                                const matchingIssue = alertRcaIssues.items.find(
                                  (issue) => issue.incidentId === ev.incidentId,
                                );
                                if (matchingIssue) {
                                  setRcaIncident(incidentFromRcaIssue(matchingIssue));
                                } else {
                                  setRcaIncident(incidentFromAlertEvent(ev));
                                }
                                markAlertRead(ev);
                                return;
                              }
                              markAlertRead(ev);
                              openRef(ev.kind, ev.name);
                            }} />
                        );
                      })}
                      {alertEvents.status === "unavailable" && unreadAlerts.length === 0 && notes.length === 0 && (
                        <div style={{ padding: "10px 12px", fontSize: TYPE.caption, color: UI.ink3 }}>알림 이벤트 관측 안 됨</div>
                      )}
                      {alertEvents.status !== "unavailable" && unreadAlerts.length === 0 && notes.length === 0 && (
                        <div style={{ padding: "18px 12px", textAlign: "center", fontSize: TYPE.caption, color: UI.ink3 }}>새 알림이 없습니다</div>
                      )}
                    </>
                  );
                })()}
              </motion.div>
            )}
          </AnimatePresence>
        </span>
        {/* 계정 — 맨 오른쪽(D20). 로그아웃 = 데모 세션 초기화(실동작) */}
        <span style={{ position: "relative" }}>
          <button type="button" className="gnav product-focusable product-control" aria-label="계정 메뉴 열기" aria-expanded={meOpen} onClick={() => { setMeOpen((open) => !open); setBellOpen(false); }}
            style={{ width: 30, height: 30, borderRadius: 999, border: meOpen ? `1.5px solid ${BLUE}` : "1.5px solid transparent", background: blueA(0.12), color: BLUE, cursor: "pointer", display: "grid", placeItems: "center", fontSize: TYPE.label, fontWeight: 600 }}>{sessionInitial(session)}</button>
          <AnimatePresence>
            {meOpen && (
              <motion.div key="me" initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={SOFT}
                style={{ position: "absolute", top: 38, right: 0, width: 244, zIndex: 65, background: UI.card, border: `1px solid ${UI.line}`, borderRadius: 12, boxShadow: `0 18px 50px -18px ${inkA(0.28)}`, padding: 6, overflow: "hidden" }}>
                <div style={{ padding: "8px 10px 9px", borderBottom: `1px solid ${UI.line2}` }}>
                  <div style={{ fontSize: TYPE.body, fontWeight: 600, color: session.status === "ready" && (session.displayName || session.userId) ? UI.ink : UI.ink3, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{session.status === "loading" ? "확인 중…" : session.displayName ?? session.userId ?? "알 수 없음"}</div>
                  <div style={{ fontSize: TYPE.caption, fontFamily: MONO, color: UI.ink3, marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{session.email ?? (session.roles.length ? session.roles.map(statusLabel).join(" · ") : session.authMode ? `인증 모드: ${statusLabel(session.authMode)}` : "이메일 없음")}</div>
                  <div style={{ display: "flex", alignItems: "center", gap: 5, fontSize: TYPE.caption, color: UI.ink2, marginTop: 6 }}><Building2 size={11} style={{ color: UI.ink3 }} />{workspaceLabel(session.workspaceId ?? contract.workspaceId)} 워크스페이스</div>
                </div>
                {session.logoutSupported ? (
                  <button className="rrow product-focusable product-control" onClick={() => { void logoutApi().then(() => window.location.reload()).catch(() => undefined); }}
                    style={{ display: "flex", alignItems: "center", gap: 8, width: "100%", textAlign: "left", border: "none", background: "transparent", borderRadius: 8, padding: "8px 10px", marginTop: 3, fontSize: TYPE.label, fontWeight: 600, color: UI.ink2, cursor: "pointer" }}>
                    <LogOut size={13} style={{ color: UI.ink3 }} />로그아웃
                  </button>
                ) : (
                  <div style={{ display: "flex", alignItems: "center", gap: 8, width: "100%", padding: "8px 10px", marginTop: 3, fontSize: TYPE.caption, color: UI.ink3 }}>
                    <LogOut size={13} style={{ color: UI.ink3 }} />{session.authMode === "trusted_proxy" ? "상위 프록시 인증 — 로그아웃은 상위에서" : "이 세션은 로그아웃을 지원하지 않습니다"}
                  </div>
                )}
              </motion.div>
            )}
          </AnimatePresence>
        </span>
      </header>

      {/* 콘텐츠 스크롤 영역 — 스크롤은 여기서만. gutter 고정으로 스크롤바 유무에 따른
          가로 점프(창 열닫힘 체감)를 없앤다. */}
      <div ref={contentScrollRef} data-shell-scroll-container="true" style={{ flex: 1, minHeight: 0, overflowY: "auto", overflowX: "clip", scrollbarGutter: "stable" }}>
      {surface === "connect" ? (
        /* 연결 설정 — 셸 안에서 위저드 서피스로 전환 (별도 페이지 아님) */
        <div style={{ position: "relative", minHeight: `calc(100vh / ${PRESENT_SCALE} - 57px)`, background: UI.bg }}>
          <ConnectWizard
            key={connectView ?? "launcher"}
            embedded
            initialView={connectView}
            repositoryClusters={contract.clusters}
          />
        </div>
      ) : surface === "deploy" ? (
        <DeploySurface applicationsFeed={repositoryApplications} onRefreshApplications={() => setManifestRefreshKey((key) => key + 1)}
          pendingRepos={pendingRepo} repositoryFilter={deployRepositoryFilter}
          applicationDetailId={deployApplicationDetailId}
          onApplicationDetailClose={() => setDeployApplicationDetailId(null)}
          onOpenRef={openRef}
          onOpenIssues={() => setSurface("issues")} onAskAi={showAi} onAddRepo={() => setConnectModal("repo")}
          topInset={topH} leftInset={navCollapsed ? 60 : 208} rightInset={aiOpen ? aiW : 0} />
      ) : surface === "issues" ? (
        <IssuesSurface incidentClusterIds={incidentClusterIds} recoveryProgressOverrides={recoveryProgressOverrides} pinnedIssueCorrelationIds={pinnedIssueCorrelationIds} sessionRules={notes.filter((n) => n.icon === "rule").map((n) => n.body.split(" · ")[0])} onOpenRef={openRef} onOpenRca={setRcaIncident} />
      ) : surface === "timeline" ? (
        <TimelineSurface
          workspaceId={workspaceIdentityId}
          clusterIds={clusterIds}
          onOpenRef={openRef}
        />
      ) : surface === "checks" ? (
        <ChecksSurface onOpenRef={openRef} />
      ) : surface === "cost" ? (
        <CostSurface onOpenRef={openRef} />
      ) : surface === "alerts" ? (
        <AlertsSurface
          events={alertEvents}
          selectedEventId={selectedAlertEventId}
          onSelectedEventIdChange={setSelectedAlertEventId}
          onOpenRef={(kind, name) => {
            setSelectedAlertEventId(null);
            openRef(kind, name);
          }}
          topInset={topH}
          leftInset={navCollapsed ? 60 : 208}
          rightInset={aiOpen ? aiW : 0}
        />
      ) : surface === "ai" ? (
        <AiHistorySurface onOpenPanel={showAi} />
      ) : surface === "settings" ? (
        <SettingsSurface session={session} clusterId={clusterIds[0] ?? null} />
      ) : surface === "home" ? (
        /* 홈 — 위젯 보드 (D21). 카드 클릭=지도 드릴, 위젯 액션=전부 실 목적지 */
        <HomeSurface workspaceId={workspaceIdentityId} applicationsFeed={repositoryApplications} alertEvents={alertEvents} namespaceFeed={nsFeed}
          clusterMeta={clusterMeta} incidentClusterIds={incidentClusterIds} pendingCl={visiblePendingCl} pendingRepo={pendingRepo}
          onWidgetDeepLink={(id) => {
            if (id === "W1") { setSurface("resources"); setResView("map"); }
            else if (id === "W2") setSurface("issues");
            else if (id === "W3") { setDeployRepositoryFilter(null); setSurface("deploy"); }
            else if (id === "W4" || id === "W8") setSurface("timeline");
            else if (id === "W7") setSurface("cost");
            else if (id === "W9") setSurface("alerts");
            else if (id === "W10" || id === "W11" || id === "W12") { setDeployRepositoryFilter(null); setSurface("deploy"); }
            else { setSurface("resources"); setResView("list"); setKindId("Pod"); }
          }}
          onDrillCluster={openClusterDrill}
          onClusterSettings={() => setSurface("settings")}
          onClusterDisconnect={(cl) => {
            // 상세 뷰와 동일한 캐논 다이얼로그(이름 입력 확인 + 단계식 진행)를
            // 제어형으로 연다 — 목록 메뉴가 confirm 한 번으로 DELETE 를 쏘는
            // 두 번째 해제 구현이 되지 않게 한다.
            const target = contract.clusters.find((cluster) => cluster.id === cl);
            if (!target) return;
            if (!(target.role === "target" && !target.readOnly && session.roles.includes("service_admin"))) {
              pushToast({ title: "연결 해제 권한이 없습니다", sub: "service_admin 역할이 필요합니다", tone: "crit" });
              return;
            }
            setListDisconnectClusterId(cl);
          }}
          onOpenAlert={(eventId) => {
            setSelectedAlertEventId(eventId);
            setSurface("alerts");
          }}
          onOpenApplication={(applicationId) => {
            setDeployRepositoryFilter(null);
            setDeployApplicationDetailId(applicationId);
            setSurface("deploy");
          }}
          onOpenIssues={() => setSurface("issues")}
          onConnect={() => setConnectModal("cluster")}
          onAddRepo={() => setConnectModal("repo")}
          onOpenPod={(name) => openRef("Pod", name)}
          onPickNs={(n) => { if (nsOptions.includes(n)) setNs(n); setKindId("Pod"); setSurface("resources"); setResView("list"); }} />
      ) : (
        <main style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: SPACE.card, padding: "12px 18px 16px" }}>
          {/* ── D18 관점 세그먼트 — 한 서피스, 세 관점(지도·목록·흐름). "지도 밑 표" 구조 폐지.
                스코프(클러스터·노드·ns·검색어)는 관점을 넘어 보존된다 ── */}
          <div
            data-resource-view-switcher="true"
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              position: "sticky",
              top: 0,
              zIndex: 8,
              height: RESOURCE_VIEW_STICKY_TOP,
              boxSizing: "border-box",
              margin: "-12px -18px 0",
              padding: "12px 18px 8px",
              background: UI.bg,
            }}
          >
            <SegmentedControl
              active={resView}
              ariaLabel="리소스 관점"
              indicatorId="resview"
              items={[
                { value: "map", label: "인프라" },
                { value: "list", label: "쿠버네티스" },
                { value: "flow", label: "트래픽" },
              ]}
              onChange={setResView}
            />
            {/* P1: UI를 설명하는 데모성 카피("서비스 호출 관점 — 전체 클러스터")는 제거한다. */}
          </div>

          {resView === "map" && (
            /* 지도 — 드릴 전체 높이. 종류 선택은 목록 관점의 것: 패널·스트립에서 종류를 고르면 목록으로 전환 */
            <>
              <OpsiaMap key={drillCl ?? "root"} initialCluster={drillCl ?? undefined} pendingClusters={pendingCl} pendingRepos={pendingRepo} connectedRepos={connectedRepos}
                repositoryGroups={repositoryGroups}
                onRepositoryDisconnected={() => setManifestRefreshKey((key) => key + 1)}
                embedded onScopeChange={(nextScope) => {
                  setScope(nextScope);
                  if (nextScope.level === "clusters") setDrillCl(null);
                  else if (nextScope.cluster) setDrillCl(nextScope.cluster);
                }} onOpenResource={openFromMap} lensTab={lensTabFor(kindId)}
                selectedNamespace={selectedNamespace}
                onAddCluster={() => setConnectModal("cluster")}
                onAddRepo={() => setConnectModal("repo")}
                onOpenRepository={(repositoryRef) => { setDeployRepositoryFilter(repositoryRef); setSurface("deploy"); }}
                stickyTop={RESOURCE_VIEW_STICKY_TOP + 12}
                viewportTopInset={topH}
              />
              {/* 종류(kind) 탐색은 쿠버네티스 관점의 본문이 오너 — 인프라 뷰 패널에 같은 목록을 두 번 두지 않는다 */}
            </>
          )}

          {resView === "list" && (
            /* 목록 — 종류 패널 + 표 전체 높이. 맵 없음. 좁은 화면은 세로 스택 + 종류 select */
            <div style={{ display: "flex", flexDirection: narrowList ? "column" : "row", gap: narrowList ? 12 : RESOURCE_LAYOUT.columnGap, alignItems: narrowList ? "stretch" : "flex-start" }}>
              {narrowList && (
                <label style={{ display: "flex", alignItems: "center", gap: 8, background: UI.card, border: `1px solid ${UI.line}`, borderRadius: 12, padding: "8px 12px" }}>
                  <span style={{ fontSize: TYPE.label, fontWeight: 600, color: UI.ink3, flexShrink: 0 }}>종류</span>
                  <select aria-label="리소스 종류 선택" value={kindId} onChange={(e) => setKindId(e.currentTarget.value)}
                    style={{ flex: 1, minWidth: 0, fontSize: TYPE.body, color: UI.ink, background: UI.card, border: `1px solid ${UI.line}`, borderRadius: 8, padding: "7px 10px", cursor: "pointer" }}>
                    {KINDS.map((k) => (
                      <option key={k.id} value={k.id}>{k.label}{kindCounts[k.id] != null ? ` (${kindCounts[k.id]})` : ""}</option>
                    ))}
                  </select>
                </label>
              )}
              <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 12 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <kind.icon size={15} style={{ color: BLUE }} />
                  <span style={{ fontSize: TYPE.section, fontWeight: 700, letterSpacing: "-0.02em", color: UI.heading }}>{kind.label}</span>
                  <span style={{ fontSize: TYPE.label, fontVariantNumeric: "tabular-nums", color: UI.ink3 }}>{shownRows.length}{shownRows.length !== allRows.length ? ` / ${allRows.length}` : ""}</span>
                  <span style={{ fontSize: TYPE.caption, fontWeight: 600, color: inScope ? BLUE : UI.ink2, background: inScope ? blueA(0.08) : inkA(0.045), borderRadius: 999, padding: "3px 11px" }}>범위 · {scopeLabel}</span>
                </div>
                {/* 표 교체는 대기 없이 즉시 — exit를 기다리면 전환이 느리고, 탭 스로틀 시 멈춘다 */}
                <motion.div key={kindId} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={SOFT}>
                  {/* 라이브 인벤토리 상태를 정직하게 표시 — 데이터 없으면 관측 안 됨 */}
                  {resourcesView.status === "loading" ? (
                    <div style={{ background: UI.card, border: `1px solid ${UI.line}`, borderRadius: RADIUS.card, padding: "40px 18px", textAlign: "center", fontSize: TYPE.body, color: UI.ink3 }}>불러오는 중…</div>
                  ) : resourcesView.status === "unavailable" ? (
                    <div style={{ background: UI.card, border: `1px solid ${UI.line}`, borderRadius: RADIUS.card, padding: "40px 18px", textAlign: "center", fontSize: TYPE.body, color: UI.ink3 }}>인벤토리 관측 안 됨</div>
                  ) : (
                    <ResourceTable kind={kind} rows={shownRows} q={q}
                      filterDesc={[inScope ? `${scopeLabel}` : "", ns !== "모든 네임스페이스" ? `${ns} 네임스페이스` : ""].filter(Boolean).join(" · ")}
                      onClearFilter={() => { setQ(""); setNs("모든 네임스페이스"); }}
                      onOpen={(r) => setDetail({ kind, row: r })} />
                  )}
                </motion.div>
              </div>
              {/* 종류 선택 패널 — 지도 관점의 탐색 패널과 같은 KindIndex 하나를 공유(두 번째 구현 금지).
                  좁은 화면에서는 위 종류 select로 대체하고 사이드바를 렌더하지 않아 표를 가리지 않는다. */}
              {!narrowList && (
              <ResourceAuxiliaryPanel
                data-resource-kind-index="true"
                aria-label="쿠버네티스 리소스 종류"
                style={{
                  position: "sticky",
                  top: RESOURCE_AUX_STICKY_TOP,
                  height: resourceAuxiliaryViewportHeight(topH, RESOURCE_AUX_STICKY_TOP),
                  maxHeight: resourceAuxiliaryViewportHeight(topH, RESOURCE_AUX_STICKY_TOP),
                }}
              >
                <KindIndex sel={kindId} onPick={(k) => setKindId(k.id)} showEmpty={showEmpty} setShowEmpty={setShowEmpty} pinned={pinned} togglePin={togglePin} filter={q} counts={kindCounts} />
              </ResourceAuxiliaryPanel>
              )}
            </div>
          )}

          {resView === "flow" && (
            /* 트래픽 — 호출 그래프 + 보조 패널(서비스 상태·포커스, 세 관점 동일 문법). 서비스 클릭 = 상세 시트 */
            <div style={{ display: "flex", flexDirection: narrowFlow ? "column" : "row", gap: narrowFlow ? 12 : RESOURCE_LAYOUT.columnGap, alignItems: "flex-start" }}>
              <div data-resource-traffic-main="true" style={{ flex: 1, minWidth: 0 }}>
                <TopologyView embedded clusterIds={scope.cluster ? [scope.cluster] : clusterIds} focusId={trafficFocus} onFocusService={setTrafficFocus} onOpenService={openTrafficService} />
              </div>
              <TrafficPanel clusterIds={scope.cluster ? [scope.cluster] : clusterIds} focus={trafficFocus} onFocus={setTrafficFocus} onOpen={openTrafficService} stickyTop={RESOURCE_AUX_STICKY_TOP} viewportTopInset={topH} stacked={narrowFlow} />
            </div>
          )}
        </main>
      )}
      </div>
      </div>

      {/* AI 어시스턴트 — 상세 페이지 위까지 덮는 우측 오버레이 + 폭 조절 핸들 */}
      <AnimatePresence>
        {aiMounted && (
          <motion.div ref={aiPanelRef} data-ai-panel="true" key="ai" initial={{ x: aiW + 30 }} animate={{ x: aiOpen ? 0 : (aiFull ? window.innerWidth / PRESENT_SCALE : aiW) + 30 }} transition={SIDE_PANEL_ENTER_TRANSITION}
            aria-hidden={!aiOpen}
            inert={!aiOpen}
            style={{
              ...SIDE_PANEL_SURFACE_STYLE,
              position: "fixed",
              top: topH,
              right: 0,
              bottom: 0,
              width: aiFull
                ? `calc(100vw / ${PRESENT_SCALE} - ${navCollapsed ? 60 : 208}px)`
                : aiW,
              zIndex: 72,
              pointerEvents: aiOpen ? "auto" : "none",
              boxShadow: aiOpen ? SIDE_PANEL_SURFACE_STYLE.boxShadow : "none",
              transition: aiDragging ? "none" : SIDE_PANEL_WIDTH_TRANSITION,
            }}>
            {/* 전체 화면 중에는 폭 조절 핸들 비활성 — 핸들 규약은 상세·RCA와 동일한
                투명 6px 엣지(경계선 1px은 시각 유지, 히트 영역만 넓힘) */}
            {!aiFull && (
              <SidePanelResizeHandle
                ariaLabel="AI 패널 폭 조절"
                dragging={aiDragging}
                maximumWidth={560}
                minimumWidth={380}
                onKeyDown={onAiHandleKeyDown}
                onPointerDown={onAiHandleDown}
                placement="inline"
                value={aiW}
              />
            )}
            <div data-ai-panel-host="true" style={SIDE_PANEL_CONTENT_HOST_STYLE}>
              <AiPanel embedded full={aiFull} recoveryRequest={aiRecoveryRequest}
                onToggleFull={() => setAiFull((v) => !v)}
                onClose={closeAi}
                onCancelRecovery={() => {
                  setAiRecoveryRequest(null);
                  setAiRecoveryReviewState("idle");
                }}
                onRecoveryReviewStateChange={setAiRecoveryReviewState}
                contextView={aiRecoveryRequest?.contextView ?? (surface === "connect" ? "연결 설정" : surface === "home" ? "홈" : surface === "deploy" ? "배포" : surface === "issues" ? "이슈" : surface === "timeline" ? "타임라인" : surface === "checks" ? "점검" : surface === "cost" ? "비용" : surface === "alerts" ? "알림" : surface === "ai" ? "AI 대화" : surface === "settings" ? "설정" : resView === "flow" ? "트래픽" : resView === "list" ? "쿠버네티스 리소스" : "인프라 지도")}
                contextScope={aiRecoveryRequest?.contextScope ?? scope.cluster ?? ""}
                contextScopeLabel={aiRecoveryRequest?.contextScope ?? scope.cluster ?? "전체 클러스터"} />
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* AI 플로팅 버튼 — 항상 최상위(상세 위 포함) · AI 창이 열리면 사라진다 */}
      <AnimatePresence>
        {!aiOpen && !narrowList && (
          <motion.div
            key="fab"
            initial={{ scale: 0.5, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0.5, opacity: 0 }}
            transition={SOFT}
            className="group"
            style={{
              position: "fixed",
              right: 22,
              bottom: 22,
              zIndex: 75,
              width: 48,
              height: 48,
            }}
          >
            <motion.button type="button" aria-label="AI 어시스턴트 열기" onClick={showAi} whileHover={{ scale: 1.06 }} whileTap={{ scale: 0.93 }}
              title={aiRecoveryRequest !== null ? recoveryReviewStatusLabel(aiRecoveryReviewState, aiRecoveryRequest.actionRoute) : "AI 어시스턴트"}
              style={{ position: "absolute", right: 0, bottom: 0, width: 48, height: 48, borderRadius: 999, border: "none", cursor: "pointer",
                background: `linear-gradient(135deg, ${BLUE}, ${BLUE2})`, color: UI.card, display: "grid", placeItems: "center",
                boxShadow: `0 10px 26px -8px ${blueA(0.55)}, 0 2px 8px ${inkA(0.12)}` }}>
              {aiRecoveryRequest !== null && (aiRecoveryReviewState === "reviewing" || aiRecoveryReviewState === "executing") ? (
                <motion.span
                  aria-label="복구 AI 진행 중"
                  animate={{ rotate: 360 }}
                  transition={{ duration: 1.6, ease: "linear", repeat: Number.POSITIVE_INFINITY }}
                  style={{ display: "grid", placeItems: "center" }}
                >
                  <Sparkles size={20} />
                </motion.span>
              ) : <Sparkles size={20} />}
              {aiRecoveryRequest !== null && aiRecoveryReviewState !== "reviewing" && aiRecoveryReviewState !== "executing" ? (
                <span
                  aria-label="확인할 복구 AI 대화 1건"
                  style={{
                    position: "absolute", top: -4, right: -4, minWidth: 18, height: 18,
                    borderRadius: 999, padding: "0 4px", display: "grid", placeItems: "center",
                    border: `2px solid ${UI.card}`, background: HP.crit, color: UI.card,
                    fontSize: 10, lineHeight: 1, fontWeight: 700,
                  }}
                >1</span>
              ) : null}
            </motion.button>
          </motion.div>
        )}
      </AnimatePresence>

      {/* 목록 ⋮ 메뉴의 연결 해제 — 상세 뷰와 동일한 캐논 다이얼로그(이름 입력 확인) */}
      {listDisconnectChoice && (
        <ClusterDisconnectDialog
          cluster={listDisconnectChoice}
          key={listDisconnectChoice.id}
          open
          onOpenChange={(nextOpen) => {
            if (nextOpen) return;
            setListDisconnectClusterId(null);
            // 취소로 닫아도 목록을 재조회 — 다이얼로그 진행 중 서버 상태가 바뀌었을
            // 수 있고(부분 해제 등), 닫힘 직후 화면이 최신이어야 한다.
            contract.refresh();
          }}
          onDisconnected={(clusterId) => {
            const disconnected = contract.clusters.find((cluster) => cluster.id === clusterId);
            removePendingClusters(clusterId, disconnected?.name, disconnected?.displayName);
            if (scope.cluster === clusterId) { setScope({ level: "clusters" }); setDrillCl(null); }
            contract.refresh();
            setListDisconnectClusterId(null);
          }}
          onPhaseChange={reportClusterLifecyclePhase}
          port={LIST_CLUSTER_DISCONNECT_PORT}
        />
      )}

      {/* 환경 연결 — 문맥 모달. 위저드가 자체 백드롭·중앙정렬·스크롤을 소유(이중 모달 금지) */}
      <AnimatePresence>
        {connectionManagerOpen ? (
          <ConnectionControlCenter
            clusters={contract.clusters}
            key="connection-control-center"
            onClose={() => setConnectionManagerOpen(false)}
            onConnectCluster={() => {
              setConnectionManagerOpen(false);
              setResumeClusterConnection(null);
              setConnectModal("cluster");
            }}
            onConnectRepository={() => {
              setConnectionManagerOpen(false);
              setRepositoryConnectContext(null);
              setConnectModal("repo");
            }}
            onDisconnected={(clusterId) => {
              const disconnected = contract.clusters.find((cluster) => cluster.id === clusterId);
              removePendingClusters(clusterId, disconnected?.name, disconnected?.displayName);
              if (scope.cluster === clusterId) {
                setScope({ level: "clusters" });
                setDrillCl(null);
              }
              contract.refresh();
            }}
            onLifecyclePhase={reportClusterLifecyclePhase}
            onRefresh={() => {
              contract.refresh();
              setManifestRefreshKey((current) => current + 1);
            }}
            onResumeCluster={(cluster) => {
              setConnectionManagerOpen(false);
              setResumeClusterConnection({
                clusterId: cluster.id,
                name: cluster.displayName,
                provider: cluster.provider,
              });
              setConnectModal("cluster");
            }}
            roles={session.roles}
          />
        ) : null}
      </AnimatePresence>

      <AnimatePresence>
        {connectModal && (
          <motion.div key="cmw" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: DUR.fade }}
            style={{ position: "fixed", top: topH, left: navCollapsed ? 60 : 208, right: 0, bottom: 0, zIndex: 76 }}>
            <ConnectWizard
              key={connectModal}
              embedded
              initialView={connectModal}
              repositoryClusters={contract.clusters}
              repositoryContext={repositoryConnectContext ?? undefined}
              resumeCluster={resumeClusterConnection ?? undefined}
              onRepositoryComplete={() => {
                setManifestRefreshKey((current) => current + 1);
                setRepositoryConnectContext(null);
              }}
              onDismiss={() => {
                setRepositoryConnectContext(null);
                setResumeClusterConnection(null);
                setConnectModal(null);
                // 위저드를 어느 단계에서 닫든 목록을 즉시 재조회한다 — 명령 발급
                // 단계까지 갔다면 가등록 클러스터가 이미 생겨 있으므로, 갱신 없이는
                // 방금 만든 클러스터가 홈에 보이지 않는 사용성 구멍이 있었다.
                contract.refresh();
              }}
            />
          </motion.div>
        )}
      </AnimatePresence>

      {/* 리소스·RCA 상세은 하나의 레이어 슬롯을 공유한다. mode=wait로 기존
          드로어 퇴장 후 다음 드로어가 들어와 동일 z-index 중첩을 막는다. */}
      <AnimatePresence mode="wait">
        {detail ? (
          <DetailOverlay key={`resource-${detail.kind.id}-${String(detail.row.cluster ?? "")}-${String(detail.row._key ?? detail.row.name)}`} kind={detail.kind} row={detail.row} onClose={() => setDetail(null)} onToast={pushToast} onOpenRef={openRef} onShowPods={(b) => { setDetail(null); setSurface("resources"); setResView("list"); setKindId("Pod"); setQ(b); }} onConnectRepository={(context) => { setRepositoryConnectContext(context); setConnectModal("repo"); }} onRequestManifestAccess={() => { setDetail(null); setSurface("settings"); }} onOpenDeploySurface={() => { setDetail(null); setSurface("deploy"); }} manifestRefreshKey={manifestRefreshKey} forceFull={aiOpen} rightInset={aiOpen ? aiW : 0} leftInset={navCollapsed ? 60 : 208} topInset={topH} viewportW={vwCss} />
        ) : rcaIncident ? (
          <IssueDetail key={`rca-${rcaIncident.correlationId ?? rcaIncident.incidentId ?? rcaIncident.name}`} {...rcaIncident} topInset={topH} leftInset={navCollapsed ? 60 : 208}
          onClose={() => setRcaIncident(null)}
          onOpenRef={(k, n) => {
            setRcaIncident(null);
            openRef(k, n);
          }}
          onAskAi={openAi}
          onRecoverySelected={(correlationId, update, source) => {
            setRecoveryProgressOverrides((current) => {
              const next = new Map(current);
              next.set(correlationId, update);
              return next;
            });
            if (shouldPinRcaRecovery(update)) {
              const pin = {
                groupKey: rcaIssuePinGroupKey({
                  ...(rcaIncident ?? {}),
                  correlationId,
                }),
                correlationId,
                touchedAt: new Date().toISOString(),
              };
              setRcaIssuePinsSnapshot((current) => {
                const currentPins = current.storageKey === rcaIssuePinsStorageKey
                  ? current.pins
                  : readStoredRcaIssuePins(rcaIssuePinsStorageKey);
                const next = upsertStoredRcaIssuePin(currentPins, pin);
                writeStoredRcaIssuePins(rcaIssuePinsStorageKey, next);
                return { storageKey: rcaIssuePinsStorageKey, pins: next };
              });
            }
            if (source === "direct" && update.selectionAccepted) {
              setAiOpen(false);
              setAiMounted(false);
              setAiFull(false);
              setAiRecoveryRequest(null);
              setAiRecoveryReviewState("idle");
            }
          }}
          rightInset={aiOpen ? aiW : 0} />
        ) : null}
      </AnimatePresence>

      {/* 작업 토스트 — 우측 상단 스택 */}
      <div style={{ position: "fixed", top: topH + 10, right: 16, zIndex: 80, display: "flex", flexDirection: "column", gap: 8, pointerEvents: "none" }}>
        <AnimatePresence initial={false} mode="popLayout">
          {toasts.map((t) => {
            const ToastIcon = t.Icon ?? (t.tone === "crit" ? AlertTriangle : Check);
            const toastColor = t.color ?? (t.tone === "crit" ? HP.crit : HP.ok);
            return (
              <motion.div key={t.id} layout="position" initial={{ opacity: 0, y: -14, scale: 0.97 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: -8, scale: 0.98 }}
                transition={{ ...SOFT, layout: { duration: 0.24, ease: [0.32, 0.72, 0, 1] } }}
                style={{ display: "flex", alignItems: "center", gap: 10, width: 340, background: UI.card, border: `1px solid ${UI.line}`, borderRadius: 13, padding: "11px 13px", boxShadow: `0 16px 44px -16px ${inkA(0.3)}`, pointerEvents: "auto" }}>
                <span style={{ width: 26, height: 26, borderRadius: 9, background: toastColor, display: "grid", placeItems: "center", flexShrink: 0 }}>
                  <ToastIcon size={t.tone === "ok" ? 14 : 13} color={UI.card} strokeWidth={t.tone === "ok" ? 3 : 2.2} />
                </span>
                <span style={{ minWidth: 0 }}>
                  <span style={{ display: "block", fontSize: TYPE.body, fontWeight: 600, color: UI.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.title}</span>
                  <span style={{ display: "block", fontSize: TYPE.caption, color: UI.ink2, marginTop: 1 }}>{t.sub}</span>
                </span>
              </motion.div>
            );
          })}
        </AnimatePresence>
      </div>

      <style>{`
        .uni { font-family: var(--font-sans); font-weight: var(--font-weight-body); -webkit-font-smoothing: antialiased; }
        /* 이 셸은 라이트 전용 디자인이다. OS 다크 모드가 <html>에 .dark 를 켜면 product-*
           유틸(.product-control 등)의 상호작용 토큰이 다크 값으로 바뀌어, 라이트 화면 위에
           검은 hover 알약이 뜨는 오류가 났다. 셸 스코프 안에서 라이트 값을 고정한다. */
        .uni { color-scheme: light;
          --control-hover: ${INTERACTION.controlHover};
          --control-selected: ${INTERACTION.controlSelected};
          --disabled-background: ${INTERACTION.disabledBg};
          --disabled-foreground: ${INTERACTION.disabledText};
          --focus-ring: ${INTERACTION.focusRing};
          --action: ${INTERACTION.action};
          --action-hover: ${INTERACTION.actionHover};
          --action-pressed: ${INTERACTION.actionPressed};
        }
        .uni .krow { transition: background .14s ease; }
        .uni .krow:hover { background: ${inkA(0.045)}; }
        .uni .krow:hover .kpin { opacity: .5 !important; }
        .uni .rrow { transition: background .12s ease; }
        .uni .rrow:hover { background: ${inkA(0.028)}; }
        .uni .gnav:hover { background: ${inkA(0.05)} !important; }
        .uni .acard { transition: transform .12s ease, background .12s ease; }
        .uni .acard:not(:disabled):hover { background: ${UI.card} !important; transform: translateY(-1px); }
        /* YAML 구문 색상 — 라이트 코드 에디터 팔레트 (Badge 텍스트 톤과 동일 계열) */
        .uni .y-k { color: ${TINT.blue.fg}; }
        .uni .y-s { color: ${TINT.ok.fg}; }
        .uni .y-n { color: ${TINT.warn.fg}; }
        .uni .y-b { color: ${TINT.purple.fg}; }
        .uni .y-p { color: ${UI.ink3}; }
        .uni .y-c { color: ${UI.ink3}; font-style: italic; }
        .uni .y-del { color: ${TINT.crit.fg}; background: ${TINT.crit.bg}; }
        .uni .y-add { color: ${TINT.ok.fg}; background: ${TINT.ok.bg}; }
        .uni .livedot { animation: lv 1.6s ease-in-out infinite; }
        @keyframes lv { 0%,100% { opacity: 1; } 50% { opacity: .35; } }
        .uni ::-webkit-scrollbar { width: 8px; } .uni ::-webkit-scrollbar-thumb { background: ${inkA(0.12)}; border-radius: 99px; }
        .uni .manifest-yaml-editor {
          scrollbar-width: auto;
          scrollbar-color: #64748b #161b22;
        }
        .uni .manifest-yaml-editor::-webkit-scrollbar {
          width: 0;
          height: 12px;
        }
        .uni .manifest-yaml-editor::-webkit-scrollbar-track {
          background: #161b22;
          border-radius: 0 0 11px 11px;
        }
        .uni .manifest-yaml-editor::-webkit-scrollbar-thumb {
          min-width: 48px;
          background: #64748b;
          border: 3px solid #161b22;
          border-radius: 999px;
          background-clip: padding-box;
        }
        .uni .manifest-yaml-editor::-webkit-scrollbar-thumb:hover {
          background: #94a3b8;
          background-clip: padding-box;
        }
        @media (prefers-reduced-motion: reduce) { .uni .livedot { animation: none !important; } }
        /* 좁은 화면(200% 확대 등): 부가 요소를 접어 핵심만 남긴다 */
        @media (max-width: 980px) { .uni .hide-narrow { display: none !important; } }
      `}</style>
    </div>
  );
}

// 단일 루트 엔트리(main.tsx)에서 마운트한다 —
// 모듈 로드 시 자체 마운트하지 않고 UnifiedApp 컴포넌트만 내보낸다.
export function UnifiedApp() {
  return (
    <DevpreviewContractProvider>
      <I18nProvider navigatorLanguage="ko-KR" storage={null}>
        <AuthSessionGateProvider reportUnauthorized={() => undefined}>
          <App />
        </AuthSessionGateProvider>
      </I18nProvider>
    </DevpreviewContractProvider>
  );
}
