// ── 데모 액션 버스 — 임베드된 서피스(위저드·AI)가 셸에 실제 결과를 알린다 ──
// 셸은 이 이벤트로 토스트·알림 센터를 갱신한다. 팝업 흉내가 아니라 상태가 실제로 이어진다.
export const ACTION_EVENT = "opsia:demo-action";
// alert_rule·connect = 알림(토스트+벨) / open_ref = 셸 상세 시트 열기(title=종류, body=이름) / open_crit = 장애 목록으로 이동
export type DemoAction = {
  kind: "alert_rule" | "connect" | "open_ref" | "open_crit"; title: string; body: string;
  /** connect 전용: 등록 대상 구분과 이름 — 셸이 목록(클러스터 카드·저장소 탭)에 실반영한다 */
  scope?: "cluster" | "repo"; ref?: string;
};
export const emitAction = (a: DemoAction) => window.dispatchEvent(new CustomEvent(ACTION_EVENT, { detail: a }));
export const onAction = (fn: (a: DemoAction) => void) => {
  const h = (e: Event) => fn((e as CustomEvent<DemoAction>).detail);
  window.addEventListener(ACTION_EVENT, h);
  return () => window.removeEventListener(ACTION_EVENT, h);
};
