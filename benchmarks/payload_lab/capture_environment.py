from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def command(*args: str) -> str:
    result = subprocess.run(args, check=True, text=True, capture_output=True)
    return result.stdout.strip()


def integer_command(*args: str) -> int:
    return int(command(*args))


def main() -> None:
    destination = Path(os.environ["ENVIRONMENT_OUT"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    disk = shutil.disk_usage(destination.parent)
    docker_info = json.loads(command("docker", "info", "--format", "{{json .}}"))
    image_names = (
        "kyro-payload-benchmark:local",
        "postgres:16.10-alpine",
        "nats:2.11-alpine",
        "prom/prometheus:v2.55.1",
        "grafana/loki:3.2.1",
        "grafana/tempo:2.6.1",
        "grafana/grafana:11.3.1",
    )
    images: dict[str, Any] = {}
    for image in image_names:
        try:
            images[image] = json.loads(command("docker", "image", "inspect", image))[0]
        except subprocess.CalledProcessError:
            images[image] = {"missing": True}
    payload = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": command("git", "rev-parse", "HEAD"),
        "host": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "physical_cpu": integer_command("sysctl", "-n", "hw.physicalcpu"),
            "logical_cpu": integer_command("sysctl", "-n", "hw.logicalcpu"),
            "memory_bytes": integer_command("sysctl", "-n", "hw.memsize"),
            "disk_total_bytes": disk.total,
            "disk_free_bytes": disk.free,
        },
        "docker": {
            "version": json.loads(command("docker", "version", "--format", "{{json .}}")),
            "compose_version": command("docker", "compose", "version", "--short"),
            "n_cpu": docker_info.get("NCPU"),
            "memory_bytes": docker_info.get("MemTotal"),
            "architecture": docker_info.get("Architecture"),
            "driver": docker_info.get("Driver"),
            "kernel_version": docker_info.get("KernelVersion"),
            "operating_system": docker_info.get("OperatingSystem"),
        },
        "images": {
            name: {
                "id": value.get("Id"),
                "repo_digests": value.get("RepoDigests", []),
                "created": value.get("Created"),
                "architecture": value.get("Architecture"),
            }
            for name, value in images.items()
        },
    }
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(destination)


if __name__ == "__main__":
    main()
