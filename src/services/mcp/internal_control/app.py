from __future__ import annotations

from services.mcp.internal_control.server import main

RUNTIME_DISCOVERY_IGNORE = True

if __name__ == "__main__":
    raise SystemExit(main())
