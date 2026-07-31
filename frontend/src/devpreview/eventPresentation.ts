export function presentEventMessage(
  raw: string | null | undefined,
): { label: string; original: string } {
  const original = raw ?? "";
  let match: RegExpMatchArray | null;
  if ((match = original.match(/^Started container (.+)$/i))) {
    return { label: `컨테이너 ${match[1]} 시작`, original };
  }
  if ((match = original.match(/^Created container (.+)$/i))) {
    return { label: `컨테이너 ${match[1]} 생성`, original };
  }
  if ((match = original.match(/^Pulled image ["']?(.+?)["']? in (.+)$/i))) {
    return { label: `이미지 ${match[1]} 가져오기 완료 · ${match[2]}`, original };
  }
  if ((match = original.match(/^Successfully assigned (.+) to (.+)$/i))) {
    return { label: `${match[1]}을 ${match[2]} 노드에 배정`, original };
  }
  if ((match = original.match(/^Killing container (.+)$/i))) {
    return { label: `컨테이너 ${match[1]} 종료`, original };
  }
  if ((match = original.match(/^ScalingReplicaSet (.+) from (\d+) to (\d+)$/i))) {
    return { label: `ReplicaSet ${match[1]} ${match[2]}→${match[3]} 조정`, original };
  }
  return { label: original ? "이벤트 세부 정보" : "", original };
}
