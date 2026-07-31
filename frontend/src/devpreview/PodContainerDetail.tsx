import type { PodResourceSummaryView } from "./podResourceDetailFeed";
import { DEFAULT_POD_CONTAINER_PORT_PROTOCOL, type PodContainerPortView } from "./podContainerSummary";
import { MONO, TYPE, UI } from "./theme";

interface PodContainerDetailProps {
  summary: PodResourceSummaryView;
}

const COPY = {
  containerUnavailable: "컨테이너 관측 안 됨",
  imageLabel: "이미지",
  imageUnavailable: "이미지 관측 안 됨",
  loading: "불러오는 중…",
  noDeclaredPorts: "선언 포트 없음",
  partialObservation: "일부 관측",
  portLabel: "포트",
  portUnavailable: "포트 관측 안 됨",
} as const;
const LABEL_SEPARATOR = " · ";
const PORT_LIST_SEPARATOR = ", ";
const PORT_NAME_SEPARATOR = ":";
const PORT_PROTOCOL_SEPARATOR = "/";

function portLabel(port: PodContainerPortView): string {
  const name = port.name ? `${port.name}${PORT_NAME_SEPARATOR}` : "";
  const protocol = port.protocol === DEFAULT_POD_CONTAINER_PORT_PROTOCOL
    ? ""
    : `${PORT_PROTOCOL_SEPARATOR}${port.protocol}`;
  return `${name}${port.port}${protocol}`;
}

function portsLabel(
  ports: PodContainerPortView[],
  complete: boolean | null,
): string {
  if (ports.length > 0) {
    const observed = ports.map(portLabel).join(PORT_LIST_SEPARATOR);
    return complete === false ? `${observed}${LABEL_SEPARATOR}${COPY.partialObservation}` : observed;
  }
  return complete === true ? COPY.noDeclaredPorts : COPY.portUnavailable;
}

export function PodContainerDetail({ summary }: PodContainerDetailProps) {
  if (summary.status === "loading") {
    return (
      <div style={{ fontSize: TYPE.caption, color: UI.ink3 }}>
        {COPY.loading}
      </div>
    );
  }

  if (summary.containers.length === 0) {
    return (
      <div style={{ fontSize: TYPE.caption, color: UI.ink3 }}>
        {COPY.containerUnavailable}
      </div>
    );
  }

  return (
    <div style={{ display: "grid", gap: 8 }}>
      {summary.containers.map((container) => (
        <div key={container.name} style={{ border: `1px solid ${UI.line2}`, background: UI.bg2, borderRadius: 10, padding: "11px 13px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
            <span style={{ fontSize: TYPE.body, fontWeight: 600, fontFamily: MONO, color: UI.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {container.name}
            </span>
          </div>
          <div style={{ fontSize: TYPE.caption, color: UI.ink3, marginTop: 4, fontFamily: MONO, overflowWrap: "anywhere" }}>
            {COPY.imageLabel}{LABEL_SEPARATOR}{container.image ?? COPY.imageUnavailable}
          </div>
          <div style={{ fontSize: TYPE.caption, color: UI.ink3, marginTop: 3, fontFamily: MONO, overflowWrap: "anywhere" }}>
            {COPY.portLabel}{LABEL_SEPARATOR}{portsLabel(container.ports, summary.containerPortsComplete)}
          </div>
        </div>
      ))}
    </div>
  );
}
