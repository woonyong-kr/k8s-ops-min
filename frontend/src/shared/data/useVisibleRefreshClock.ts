import { useCallback, useEffect, useState } from "react";

export function useVisibleRefreshClock(enabled: boolean, intervalMs: number) {
  if (!Number.isFinite(intervalMs) || intervalMs <= 0) {
    throw new RangeError("visible refresh interval must be a positive number");
  }
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((current) => current + 1), []);

  useEffect(() => {
    if (!enabled) return;
    const refreshWhenVisible = () => {
      if (document.visibilityState === "visible") refresh();
    };
    const interval = window.setInterval(refreshWhenVisible, intervalMs);
    document.addEventListener("visibilitychange", refreshWhenVisible);
    return () => {
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
    };
  }, [enabled, intervalMs, refresh]);

  return { refresh, revision };
}
