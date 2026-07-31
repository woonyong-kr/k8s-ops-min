import { useId, useState, type CSSProperties } from "react";

import { presentEventMessage } from "./eventPresentation";

interface EventMessageTextProps {
  color: string;
  fontSize: number;
  message: string;
  reasonLabel: string;
}

export function EventMessageText({
  color,
  fontSize,
  message,
  reasonLabel,
}: EventMessageTextProps) {
  const [expanded, setExpanded] = useState(false);
  const originalId = useId();
  const presented = presentEventMessage(message);
  const compactStyle: CSSProperties = {
    color,
    display: "block",
    fontSize,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };

  if (presented.label !== "이벤트 세부 정보") {
    return (
      <span aria-label={presented.label} style={compactStyle} title={presented.original}>
        {presented.label}
      </span>
    );
  }

  return (
    <span style={{ color, display: "block", fontSize, minWidth: 0 }}>
      <button
        aria-controls={originalId}
        aria-expanded={expanded}
        aria-label={`${reasonLabel}: 이벤트 원문 보기`}
        onClick={() => setExpanded((value) => !value)}
        style={{
          ...compactStyle,
          background: "transparent",
          border: 0,
          cursor: "pointer",
          padding: 0,
          textAlign: "left",
          width: "100%",
        }}
        type="button"
      >
        {presented.label}
      </button>
      {expanded && <span
        aria-label={`${reasonLabel}: 이벤트 원문`}
        id={originalId}
        style={{ display: "block", marginTop: 4, overflowWrap: "anywhere" }}
      >
        {presented.original}
      </span>}
    </span>
  );
}
