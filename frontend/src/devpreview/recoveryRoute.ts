export function isSafePrRoute(route?: string | null): boolean {
  const normalized = route?.trim().toLowerCase();
  return normalized === "draft_pr" || normalized === "safe_pr";
}

export function recoveryRouteLabel(route?: string | null): string {
  const normalized = route?.trim().toLowerCase();
  if (normalized === "auto") return "자동 복구";
  if (isSafePrRoute(normalized)) return "복구 PR";
  if (normalized === "approval_required") return "복구 요청";
  return normalized || "미확인";
}
