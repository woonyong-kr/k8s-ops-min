#!/usr/bin/env bash
# 담당 범위 밖 파일을 저장소에서 제거합니다.
# 사용법:  bash trim-portfolio.sh
set -euo pipefail
cd "$(dirname "$0")"

[ -f _trim-keep-list.txt ] || { echo "_trim-keep-list.txt 가 없습니다."; exit 1; }
[ -d .git ] || { echo "git 저장소 루트에서 실행하세요."; exit 1; }

echo "▶ 남은 잠금 파일 정리"
rm -f .git/index.lock .git/HEAD.lock
find .git/objects -name 'tmp_obj_*' -delete 2>/dev/null || true

echo "▶ 현재 추적 파일 수: $(git ls-files | wc -l | tr -d ' ')"

git -c core.quotepath=false ls-files > /tmp/_kyro_all.txt
grep -vxFf _trim-keep-list.txt /tmp/_kyro_all.txt > /tmp/_kyro_rm.txt || true
echo "▶ 제거 대상: $(wc -l < /tmp/_kyro_rm.txt | tr -d ' ') 개"

if [ -s /tmp/_kyro_rm.txt ]; then
  tr '\n' '\0' < /tmp/_kyro_rm.txt | xargs -0 git rm -q --
fi

echo "▶ 미추적 잔해 정리"
rm -rf _to_delete .pytest_cache references
find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
find . -mindepth 1 -type d -empty -not -path './.git/*' -delete 2>/dev/null || true

rm -f /tmp/_kyro_all.txt /tmp/_kyro_rm.txt

echo
echo "▶ 완료. 추적 파일 수: $(git ls-files | wc -l | tr -d ' ')"
echo "▶ 루트:"
ls -A | sed 's/^/    /'
echo
echo "변경은 커밋하거나 스테이징하지 않았습니다. git diff로 먼저 검토하세요."
