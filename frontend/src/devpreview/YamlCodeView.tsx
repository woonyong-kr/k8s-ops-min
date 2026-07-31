import { MONO, TYPE } from "./theme";

// IDE 품질 읽기 전용 YAML 뷰어 — 다크 코드 표면 + 구문 색 + 줄번호.
// 토큰화는 줄 단위 순수 함수(정규식)로만 하고 DOM 주입 없이 React 스팬으로 렌더한다
// (XSS 표면 0). 편집기(textarea)와 동일한 다크 팔레트를 써서 탭 안 시각 일관성 유지.

const CODE_BG = "#0d1117";
const CODE_FG = "#e6edf3";
const GUTTER_FG = "#4c566a";
const GUTTER_BG = "#0a0e14";
const COLOR_KEY = "#7ee0ff";
const COLOR_STRING = "#a5d6a7";
const COLOR_NUMBER = "#ffcb6b";
const COLOR_KEYWORD = "#c792ea";
const COLOR_COMMENT = "#6b7280";
const COLOR_PUNCT = "#8b93a1";

const NUMBER_PATTERN = /^-?\d+(\.\d+)?$/;
const KEYWORD_PATTERN = /^(true|false|null|~|yes|no|on|off)$/i;
const KEY_PATTERN = /^(\s*(?:-\s+)?)([^:\s#][^:#]*?)(:)(\s.*|$)/;

function valueSpan(value: string, key: number): React.ReactNode {
  const trimmed = value.trim();
  if (trimmed === "") return value;
  const lead = value.slice(0, value.length - value.trimStart().length);
  let color = COLOR_STRING;
  if (NUMBER_PATTERN.test(trimmed)) color = COLOR_NUMBER;
  else if (KEYWORD_PATTERN.test(trimmed)) color = COLOR_KEYWORD;
  else if (trimmed.startsWith("&") || trimmed.startsWith("*") || trimmed.startsWith("|") || trimmed.startsWith(">")) color = COLOR_PUNCT;
  return (
    <span key={key}>
      {lead}
      <span style={{ color }}>{value.trimStart()}</span>
    </span>
  );
}

function renderLine(line: string, key: number): React.ReactNode {
  if (line.trim().startsWith("#")) {
    return <span key={key} style={{ color: COLOR_COMMENT, fontStyle: "italic" }}>{line}</span>;
  }
  const match = KEY_PATTERN.exec(line);
  if (!match) {
    // 키 없는 줄(리스트 항목 값·연속 스칼라)은 값 규칙만 적용.
    const dash = /^(\s*-\s+)(.*)$/.exec(line);
    if (dash) {
      return (
        <span key={key}>
          <span style={{ color: COLOR_PUNCT }}>{dash[1]}</span>
          {valueSpan(dash[2], 1)}
        </span>
      );
    }
    return <span key={key}>{line}</span>;
  }
  const [, indent, keyText, colon, rest] = match;
  return (
    <span key={key}>
      <span style={{ color: COLOR_PUNCT }}>{indent}</span>
      <span style={{ color: COLOR_KEY }}>{keyText}</span>
      <span style={{ color: COLOR_PUNCT }}>{colon}</span>
      {valueSpan(rest, 1)}
    </span>
  );
}

const DIFF_ADD_FG = "#7ee787";
const DIFF_ADD_BG = "rgba(46,160,67,0.15)";
const DIFF_DEL_FG = "#ffa198";
const DIFF_DEL_BG = "rgba(248,81,73,0.15)";
const DIFF_HUNK_FG = "#c792ea";

function diffLineStyle(line: string): React.CSSProperties {
  if (line.startsWith("@@")) return { color: DIFF_HUNK_FG, fontWeight: 600 };
  if (line.startsWith("+++") || line.startsWith("---")) return { color: COLOR_PUNCT, fontWeight: 600 };
  if (line.startsWith("+")) return { color: DIFF_ADD_FG, background: DIFF_ADD_BG };
  if (line.startsWith("-")) return { color: DIFF_DEL_FG, background: DIFF_DEL_BG };
  return { color: CODE_FG };
}

/** unified diff 를 IDE 스타일(+초록·−빨강·@@ 헌크)로 렌더한다. 편집 미리보기 전용. */
export function DiffCodeView({ value, ariaLabel, maxHeight = 280 }: {
  value: string;
  ariaLabel: string;
  maxHeight?: number | null;
}) {
  const lines = value.replace(/\n$/, "").split("\n");
  return (
    <div role="figure" aria-label={ariaLabel}
      style={{
        border: "1px solid #1c2330",
        borderRadius: 12,
        background: CODE_BG,
        maxHeight: maxHeight ?? undefined,
        overflowX: "auto",
        overflowY: maxHeight == null ? "visible" : "auto",
        fontFamily: MONO,
        fontSize: TYPE.code,
        lineHeight: 1.65,
      }}>
      <pre style={{ margin: 0, padding: "12px 16px", whiteSpace: "pre", minWidth: 0 }}>
        {lines.map((line, index) => (
          <div key={index} style={diffLineStyle(line)}>{line || " "}</div>
        ))}
      </pre>
    </div>
  );
}

/** 읽기 전용 YAML 을 IDE 스타일(다크·구문 색·줄번호)로 렌더한다. */
export function YamlCodeView({ value, ariaLabel, maxHeight = 320 }: {
  value: string;
  ariaLabel: string;
  maxHeight?: number | null;
}) {
  const lines = value.replace(/\n$/, "").split("\n");
  return (
    <div role="figure" aria-label={ariaLabel}
      style={{
        display: "flex",
        border: "1px solid #1c2330",
        borderRadius: 12,
        background: CODE_BG,
        maxHeight: maxHeight ?? undefined,
        overflowX: "auto",
        overflowY: maxHeight == null ? "visible" : "auto",
        fontFamily: MONO,
        fontSize: TYPE.code,
        lineHeight: 1.65,
      }}>
      <div aria-hidden style={{ flexShrink: 0, padding: "12px 0", background: GUTTER_BG, color: GUTTER_FG, textAlign: "right", userSelect: "none", position: "sticky", left: 0 }}>
        {lines.map((_, index) => (
          <div key={index} style={{ padding: "0 10px 0 14px", fontVariantNumeric: "tabular-nums" }}>{index + 1}</div>
        ))}
      </div>
      <pre style={{ margin: 0, padding: "12px 16px", color: CODE_FG, whiteSpace: "pre", minWidth: 0 }}>
        {lines.map((line, index) => (
          <div key={index}>{renderLine(line, index) }</div>
        ))}
      </pre>
    </div>
  );
}
