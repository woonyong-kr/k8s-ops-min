import { RefreshCw, ShieldCheck } from "lucide-react";

import type { ResourceAccessView } from "./resourceAccessFeed";
import { BLUE, MONO, TYPE, UI, inkA } from "./theme";

const itemStyle: React.CSSProperties = {
  alignItems: "center",
  background: UI.bg2,
  border: `1px solid ${UI.line2}`,
  borderRadius: 8,
  display: "flex",
  gap: 8,
  justifyContent: "space-between",
  minWidth: 0,
  padding: "7px 10px",
};

function Meta({ label, value }: { label: string; value: React.ReactNode }) {
  return <div style={itemStyle}><span style={{ color: UI.ink3, fontSize: TYPE.caption }}>{label}</span><span style={{ color: UI.ink, fontFamily: MONO, fontSize: TYPE.caption, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{value}</span></div>;
}

export function ResourceAccessPanel({ view }: { view: ResourceAccessView }) {
  if (view.status === "loading") return <div style={{ color: UI.ink3, fontSize: TYPE.label }}>권한 근거를 불러오는 중…</div>;
  if (view.status === "idle") return <div style={{ color: UI.ink3, fontSize: TYPE.label }}>이 리소스의 권한 근거는 현재 범위에서 관측되지 않습니다.</div>;
  if (view.status === "unavailable") return <div style={{ color: UI.ink3, fontSize: TYPE.label }}>권한 인벤토리가 아직 수집되지 않았습니다.</div>;
  if (view.status === "error" || !view.data) {
    return <button type="button" onClick={view.retry} style={{ alignItems: "center", background: UI.card, border: `1px solid ${UI.line}`, borderRadius: 8, color: BLUE, cursor: "pointer", display: "inline-flex", fontSize: TYPE.label, fontWeight: 600, gap: 7, padding: "7px 11px" }}><RefreshCw size={12} />권한 다시 불러오기</button>;
  }
  const data = view.data;
  if (data.type === "unavailable") {
    return <div style={{ color: UI.ink3, fontSize: TYPE.label }}>권한 인벤토리를 확인할 수 없습니다 · {data.reason_codes.join(", ")}</div>;
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
      <div style={{ alignItems: "center", color: UI.ink2, display: "flex", fontSize: TYPE.label, gap: 7, marginBottom: 2 }}>
        <ShieldCheck size={13} style={{ color: BLUE }} />
        {data.type === "subject" ? "주체 권한" : data.type === "role" ? "역할 연결" : "네임스페이스 권한 요약"}
        {data.observed_at && <span style={{ color: UI.ink3, fontSize: TYPE.caption, marginLeft: "auto" }}>{data.observed_at.replace("T", " ").slice(0, 16)}</span>}
      </div>
      {data.type === "subject" && <>
        <Meta label="주체" value={`${data.subject.namespace}/${data.subject.name}`} />
        <Meta label="직접 바인딩" value={data.direct.length} />
        <Meta label="상속 그룹" value={data.inherited_from_groups.length} />
        <Meta label="권한 규칙" value={data.flat.length} />
        <Meta label="사용 파드" value={data.used_by_pods.length} />
        {data.truncated && <span style={{ background: inkA(0.05), borderRadius: 7, color: UI.ink3, fontSize: TYPE.caption, padding: "6px 9px" }}>수집 한도로 일부 권한만 표시합니다.</span>}
      </>}
      {data.type === "role" && <>
        <Meta label="역할" value={`${data.role.kind}/${data.role.name}`} />
        <Meta label="바인딩" value={data.bindings.length} />
        <Meta label="연결 주체" value={data.bindings.reduce((sum, binding) => sum + binding.subjects.length, 0)} />
      </>}
      {data.type === "namespace" && <>
        <Meta label="네임스페이스" value={data.namespace} />
        <Meta label="ServiceAccount" value={data.service_account_count} />
        <Meta label="RoleBinding" value={data.role_bindings.length} />
        <Meta label="ClusterRoleBinding" value={data.cluster_role_bindings_with_local_subject.length} />
      </>}
    </div>
  );
}
