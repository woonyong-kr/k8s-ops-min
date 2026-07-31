export type PullRequestReference = {
  number: string | null;
  label: string;
};

export function pullRequestReference(prUrl: string): PullRequestReference {
  const number = pullRequestNumber(prUrl);
  return {
    number,
    label: number ? `Kyro 복구 PR #${number}` : "Kyro에서 생성한 복구 PR",
  };
}

export function pullRequestNumber(prUrl: string): string | null {
  try {
    const url = new URL(prUrl);
    const match = url.pathname.match(/\/pull\/(\d+)(?:\/|$)/);
    return match?.[1] ?? null;
  } catch {
    return null;
  }
}
