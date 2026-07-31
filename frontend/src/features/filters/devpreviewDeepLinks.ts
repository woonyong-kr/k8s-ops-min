type OpsiaPin = { kind: "svc" | "crit"; id: string } | null;

function currentSearchParams(): URLSearchParams {
  return new URLSearchParams(window.location.search);
}

export function readDevpreviewOpsiaPin(validServiceIds: readonly string[]): OpsiaPin {
  const params = currentSearchParams();
  const serviceId = params.get("svc");
  if (serviceId && validServiceIds.includes(serviceId)) return { kind: "svc", id: serviceId };
  if (params.get("crit") === "1") return { kind: "crit", id: "all" };
  return null;
}

export function readDevpreviewTopologyFocus(validServiceIds: readonly string[]): string | null {
  const serviceId = currentSearchParams().get("focus");
  return serviceId && validServiceIds.includes(serviceId) ? serviceId : null;
}
