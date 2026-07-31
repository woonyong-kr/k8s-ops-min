"""Single-process OSS management controller composition root."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from packages.runtime.controller import ControllerRuntime  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="load every entrypoint, validate the composition, and exit",
    )
    args = parser.parse_args()
    runtime = ControllerRuntime(ROOT)
    if args.check:
        print(json.dumps(runtime.check_report(), sort_keys=True))
        return
    asyncio.run(runtime.serve())


if __name__ == "__main__":
    main()
