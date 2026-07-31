#!/usr/bin/env python3
"""Redact sensitive assignments from bounded deployment diagnostic output."""

from __future__ import annotations

import sys

from release_flow_smoke import redact_sensitive_text


def main() -> int:
    for line in sys.stdin:
        sys.stdout.write(redact_sensitive_text(line))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
