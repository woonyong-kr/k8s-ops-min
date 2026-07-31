#!/usr/bin/env bash
# 커밋 메시지 컨벤션 게이트
#
#   <type>: <한국어 명사 키워드> / <한국어 명사 키워드>
#
# 규칙 (docs/CONTRIBUTING 또는 팀 commit-convention 스킬 기준):
#   - 허용 타입: feat fix refactor docs test chore style perf build ci revert
#   - 스코프 금지        : feat(scope): ... 은 실패
#   - 한국어 제목 필수    : 제목에 한글이 없으면 실패
#   - 키워드 구분         : 공백 포함 ` / `로 나뉜 비어 있지 않은 키워드가 둘 이상
#   - 명사형 키워드       : 각 키워드의 한국어 서술형·동사 종결을 금지
#   - 종결 문장부호 금지  : . ! ? 。 ！ ？ …
#   - 한 줄 유지 (제목 72자 이하)
#   - 모호한 단어 지양    : 수정 / 작업 / 변경 / 업데이트 만으로 끝나는 제목은 실패
#
# 사용:
#   .git/hooks/commit-msg  → exec scripts/commit-msg-gate.sh "$1"
#   CI                     → scripts/commit-msg-gate.sh --range origin/dev..HEAD

set -euo pipefail

TYPES='feat|fix|refactor|docs|test|chore|style|perf|build|ci|revert'
VAGUE='수정|작업|변경|업데이트'
DENYLIST="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/commit-denylist.txt"

fail() {
  echo "커밋 메시지 컨벤션 위반: $1" >&2
  echo "  제목: $2" >&2
  return 1
}

contains_hangul_syllable() {
  SUBJECT="$1" python3 - <<'PY'
import os
import sys

subject = os.environ["SUBJECT"]
sys.exit(0 if any("\uac00" <= char <= "\ud7a3" for char in subject) else 1)
PY
}

ends_with_sentence_punctuation() {
  SUBJECT="$1" python3 - <<'PY'
import os
import sys

sys.exit(0 if os.environ["SUBJECT"].endswith((".", "!", "?", "。", "！", "？", "…")) else 1)
PY
}

keyword_shape_status() {
  KEYWORDS="$1" python3 - <<'PY'
import os
import re
import sys

keywords = os.environ["KEYWORDS"]
parts = [part.strip() for part in keywords.split(" / ")]

if len(parts) < 2 or any(not part for part in parts):
    sys.exit(1)

# "명사"의 모든 형태소를 셸 훅에서 판별할 수는 없다. 대신 팀에서 금지한
# 서술형/동사형 종결과 자주 쓰이는 활용형을 각 키워드 끝에서 거절한다.
# 기술 고유명사(Tauri, GitOps, API, URL 등)는 어떤 목록에도 넣지 않는다.
narrative_ending = re.compile(
    r"(?:"
    r"한다|했다|합니다|해요|했어요|하십시오|"
    r"된다|됐다|됩니다|되었다|되다|"
    r"이다|있다|없다|"
    r"바로잡는다|모은다|막는다|연다|닫는다|"
    r"늘린다|줄인다|보인다|나눈다|받는다|"
    r"[가-힣]+(?:는다|은다|인다|힌다|린다|낸다|킨다|운다|든다|친다)"
    r")$"
)

if any(narrative_ending.search(part) for part in parts):
    sys.exit(2)
PY
}

validate_keyword_shape() {
  local keywords="$1"
  local status=0

  keyword_shape_status "$keywords" || status=$?
  case "$status" in
    0) return 0 ;;
    1)
      fail "제목은 공백 포함 ' / '로 나뉜 비어 있지 않은 명사 키워드가 둘 이상이어야 한다" "$2"
      ;;
    2)
      fail "키워드는 서술형·동사형으로 끝내지 않는다. 명사형 결과를 쓴다" "$2"
      ;;
    *)
      fail "키워드 형식을 판별하지 못했다" "$2"
      ;;
  esac
}

check_subject() {
  local subject="$1"
  local keywords=""
  local rc=0

  if [[ "$subject" =~ ^($TYPES)\([^\)]+\): ]]; then
    fail "스코프를 쓰지 않는다. '${BASH_REMATCH[1]}: ...' 로 쓴다" "$subject" || rc=1
  elif [[ ! "$subject" =~ ^($TYPES):\ .+ ]]; then
    fail "'<type>: <한국어 명사 키워드> / <한국어 명사 키워드>' 형식이어야 한다 (허용 타입: $TYPES)" "$subject" || rc=1
  else
    keywords="${subject#*: }"
    validate_keyword_shape "$keywords" "$subject" || rc=1
  fi

  # Bash의 다국어 범위식은 러너의 locale/regex 구현에 따라 결과가 달라진다.
  # Unicode 한글 음절 코드포인트를 직접 검사해 로컬 훅과 CI가 같은 판정을 내리게 한다.
  if ! contains_hangul_syllable "$subject"; then
    fail "제목은 한국어로 쓴다" "$subject" || rc=1
  fi

  if ends_with_sentence_punctuation "$subject"; then
    fail "제목을 문장부호로 끝내지 않는다" "$subject" || rc=1
  fi

  # 길이: 컨벤션은 "한 줄로 유지"라고만 한다. 숫자를 정하지 않았으므로 **경고만** 한다.
  # (여기서 임의로 실패시키면 팀의 기존 커밋 대부분이 위반으로 잡힌다 — 규칙이 아니라 취향이다)
  if (( ${#subject} > 72 )); then
    echo "경고: 제목이 ${#subject}자다. 한 줄로 짧게 유지하는 편이 좋다 — $subject" >&2
  fi

  # "…를 수정" / "…작업" 처럼 모호한 단어로 끝나는 제목
  if [[ "$subject" =~ ($VAGUE)$ ]]; then
    fail "'${BASH_REMATCH[1]}' 같은 모호한 단어로 끝내지 않는다. 명사형 결과를 쓴다" "$subject" || rc=1
  fi

  # 금지어 — denylist 파일이 있을 때만 검사한다(한 줄에 하나).
  if [[ -f "$DENYLIST" ]]; then
    while IFS= read -r word; do
      word="${word%%#*}"
      # 앞뒤 공백만 제거한다. xargs 는 백슬래시를 먹어서 정규식이 깨진다.
      word="$(printf '%s' "$word" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
      [[ -z "$word" ]] && continue
      if printf '%s' "$subject" | grep -qiE -- "$word"; then
        fail "커밋 제목에 denylist 금지어를 남기지 않는다" "$subject" || rc=1
      fi
    done < "$DENYLIST"
  fi

  return $rc
}

rc=0

if [[ "${1:-}" == "--range" ]]; then
  range="${2:?사용: $0 --range <git-range>}"
  # GitHub의 merge commit 제목은 플랫폼이 생성하므로 팀 제목 형식을 적용할 수 없다.
  # 병합된 실제 커밋은 그대로 검사하고, 합성 merge commit만 범위에서 제외한다.
  while IFS= read -r subject; do
    [[ -z "$subject" ]] && continue
    check_subject "$subject" || rc=1
  done < <(git log --no-merges --pretty=%s "$range")
else
  msg_file="${1:?사용: $0 <commit-msg-file> 또는 --range <range>}"
  subject="$(head -n1 "$msg_file")"
  check_subject "$subject" || rc=1
fi

if (( rc != 0 )); then
  cat >&2 <<'EOF'

형식:
  <type>: <한국어 명사 키워드> / <한국어 명사 키워드>

허용 타입:
  feat fix refactor docs test chore style perf build ci revert

예:
  feat: 상세 패널 / 3상태 복원
  fix: 자식 노드 비교 / 순서 정합성
  docs: 원본 이식 범위 / 토큰 어댑터
EOF
fi

exit $rc
