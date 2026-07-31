// ── Kyro 데모 디자인 토큰 — 단일 소스 ─────────────────────────────
// 철학: Vercel Geist(의미 토큰·차분한 표면·정보 우선) × Apple HIG(컬러는 데이터에만·헤어라인·스프링 모션)
// 규칙: 데모 화면은 색·타이포·모션 값을 여기서만 가져온다. 컴포넌트 안 하드코딩 금지.

// 표면·잉크 (Geist: background / surface / border, 3단 잉크)
export const UI = {
  bg: "#E9EBF0",     // 페이지 배경 — 밝은 환경에서도 흰 카드와 구분되는 중성 회색
  bg2: "#F7F8FA",    // 표면 안 보조 배경 (표 헤더·인셋)
  card: "#FFFFFF",   // 표면
  line: "#E0E3E8",   // 헤어라인 (구분 1순위 — 그림자보다 먼저)
  line2: "#EBEDF1",  // 보조 헤어라인
  ink: "#111318",    // 본문·제목
  heading: "#2B2F36",// 카드·섹션 제목
  ink2: "#5F6570",   // 보조 텍스트
  ink3: "#9AA0AA",   // 라벨·자리표시
} as const;

// 알파 잉크·액센트 — rgba 리터럴 금지, 반드시 이 헬퍼로 파생
export const inkA = (a: number) => `rgba(17,19,24,${a})`;
export const cardA = (a: number) => `rgba(255,255,255,${a})`; // 글래스 표면(블러 위)
export const GLASS = "rgba(246,247,250,0.86)";                // 프로스트 시트 배경
export const blueA = (a: number) => `rgba(10,132,255,${a})`;
export const critA = (a: number) => `rgba(255,95,85,${a})`;
export const okA = (a: number) => `rgba(48,209,88,${a})`;
export const warnA = (a: number) => `rgba(255,179,64,${a})`;

// 액센트 — 선택·포커스·링크 전용 (상태 표현에 쓰지 않는다)
export const BLUE = "#0A84FF";
export const BLUE2 = "#5AC8FA"; // 브랜드 그라디언트 종점 전용

// 상호작용 — 액션과 중립 컨트롤의 상태를 컴포넌트별로 재정의하지 않는다.
export const INTERACTION = {
  action: BLUE,
  actionHover: "#0973E6",
  actionPressed: "#0068D9",
  focusRing: blueA(0.32),
  controlHover: "#F4F5F7",
  controlSelected: "#EDF4FF",
  disabledBg: "#F3F4F6",
  disabledText: "#9AA0AA",
} as const;

// 보조 중립 — 호버 헤어라인·어포던스·인셋 (회색 리터럴 난립 금지)
export const LINE3 = "#DADDE3";  // 호버·점선 보더
export const INK4 = "#C6CAD1";   // 셰브런·유휴 스트로크 어포던스
export const INSET = "#EEF0F4";  // 창·레일 인셋 배경
export const MARK = "#FFF1B8";   // 검색 하이라이트

// 코드 표면 (다크 인셋)
export const CODE = { bg: "#0F1219", fg: "#D6DBE5" } as const;

// 범주·아이덴티티 컬러 — 차트 카테고리·서비스 아바타 전용 (상태 의미 금지)
export const IDENT = { teal: "#0FA3B1", indigo: "#4C6EF5", jade: "#12B5A5", ruby: "#DC382C" } as const;

// 상태 팔레트 (Apple 시스템 컬러 계열) — 상태 외 용도 금지
export const HP = {
  ok: "#30D158",
  warn: "#FFB340",
  crit: "#FF5F55",
  pending: "#D9DCE1",
  ghost: "#F3F4F6",
} as const;
// 토폴로지 등 상태 별칭 — 반드시 HP와 같은 값
export const ST = { ok: HP.ok, warn: HP.warn, crit: HP.crit } as const;

// 상태 틴트 (배경/보더 짝) — Badge·칩·경고 박스 공용
export const TINT = {
  ok:   { fg: "#1F9D4D", bg: "#EDFAF1", bd: "#C9EAD4" },
  warn: { fg: "#B25A00", bg: "#FFF8EF", bd: "#F3D8B7" },
  crit: { fg: "#C43028", bg: "#FFF3F2", bd: "#F5CFCC" },
  blue: { fg: "#0A6CFF", bg: "#EDF4FF", bd: "#CFE1FB" },
  purple: { fg: "#8250DF", bg: "#F6F1FE", bd: "#E3D5FA" },
  gray: { fg: "#5F6570", bg: "#F4F5F7", bd: "#E4E6EA" },
  lime: { fg: "#4D7C0F", bg: "#F3FAE7", bd: "#DDF0BB" },
} as const;

// 외부 브랜드 표식 (로고 옆 고정색)
export const BRAND = { github: "#24292F", awsA: "#FF9900", awsB: "#F76F00" } as const;

// 타이포 — 의미 기반 5단계 + 기능성 예외(KPI·code)
export const SANS = `"Pretendard Variable", "Pretendard", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`;
export const MONO = "ui-monospace, 'SF Mono', SFMono-Regular, Menlo, monospace";
export const TYPE = {
  caption: 12,
  label: 14,
  body: 15,
  section: 17,
  page: 24,
  kpi: 28,
  code: 12,
} as const;

// 모션 — 용도별 스프링 3종 (임의 duration 금지)
export const SOFT = { type: "spring", bounce: 0.12, visualDuration: 0.32 } as const;   // 요소 등장·탭 인디케이터
export const SPRING = { type: "spring", bounce: 0.16, visualDuration: 0.5 } as const;  // 카드·위젯 레이아웃
export const PAGE = { type: "spring", bounce: 0.08, visualDuration: 0.55 } as const;   // 뷰(페이지) 전환
export const EASE_DRAW = [0.22, 1, 0.36, 1] as const;                                   // 차트 선 드로잉
// 스프링이 부적합한 곳(차트 드로잉·게이지 충전·카운트업·마이크로 페이드)만 허용되는 duration
export const DUR = {
  micro: 0.12,  // 툴팁·팝오버 마이크로 페이드
  fade: 0.18,   // 오버레이·모드 전환 페이드
  draw: 0.85,   // 차트 선·도넛 드로잉
  meter: 1.2,   // 게이지·비율 바 충전
  count: 1.4,   // 숫자 카운트업
} as const;

// 엘리베이션 — 헤어라인 우선, 그림자는 떠 있는 것(팝오버·오버레이)에만
export const ELEV = {
  hover: "0 10px 26px -20px rgba(17,19,24,0.16)",
  pop: "0 18px 50px -18px rgba(17,19,24,0.28)",
  overlay: "0 28px 70px -24px rgba(17,19,24,0.38)",
} as const;

// 라운드 스케일 (4px 그리드)
export const RADIUS = { tile: 3, chip: 6, control: 9, card: 14, panel: 16, sheet: 18 } as const;

// 여백 스케일 — 조밀한 목록부터 페이지 구획까지 4px 그리드로 제한한다.
export const SPACE = { compact: 8, stack: 12, card: 16, section: 20, page: 24 } as const;

// 리소스 관점(인프라·쿠버네티스·트래픽)의 공통 2열 프레임.
// 관점 전환 시 우측 보조 패널의 위치와 크기가 흔들리지 않게 한 계약에서 관리한다.
export const RESOURCE_LAYOUT = {
  viewSwitcherHeight: 56,
  auxiliaryWidth: 270,
  columnGap: 16,
  stickyGap: 12,
  viewportBottomGap: 16,
  auxiliaryHeaderHeight: 52,
  auxiliaryHeaderPadding: "8px",
  auxiliaryBodyPadding: "8px",
  auxiliarySectionHeight: 28,
  auxiliaryRowHeight: 52,
  auxiliaryRowPadding: "7px 8px",
  auxiliaryRowGap: 2,
  auxiliaryIconColumn: 20,
  auxiliaryTrailingColumn: 30,
} as const;

// 시연 스케일 — 데모는 멀리서도 읽혀야 한다 (기본 1.25 = 별도 확대 없이 발표 가독)
export const PRESENT_SCALE = 1.25;
