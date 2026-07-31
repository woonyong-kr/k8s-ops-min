export type ConnectionFailurePresentation = {
  kind: "repository_credential" | "generic";
  title: string;
  message: string;
  actionLabel: string | null;
};

const REPOSITORY_CREDENTIAL_UNAVAILABLE = "repository credential is unavailable";

export function connectionFailurePresentation(
  detail: string,
): ConnectionFailurePresentation {
  if (detail.trim().toLowerCase() === REPOSITORY_CREDENTIAL_UNAVAILABLE) {
    return {
      kind: "repository_credential",
      title: "저장소 인증을 다시 확인해야 합니다",
      message:
        "저장된 GitHub App 권한을 확인할 수 없습니다. 이전 단계에서 GitHub App을 다시 연결해 주세요.",
      actionLabel: "GitHub App 다시 연결",
    };
  }
  return {
    kind: "generic",
    title: "요청을 완료하지 못했습니다",
    message: detail,
    actionLabel: null,
  };
}
