"""target drift worker 앱 배선."""

from __future__ import annotations

from events import on_drift_detected

from domains.target.events import ClusterDriftDetectedBody
from packages.runtime.app import App

__all__ = [
    "app",
    "on_drift_detected",
]

app = App("target-drift-worker")
app.on(ClusterDriftDetectedBody)(on_drift_detected)


if __name__ == "__main__":
    app.run()
