import re
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[1] / "docs" / "api"


def _bru_files() -> list[Path]:
    if not API_ROOT.exists():
        return []
    return sorted(API_ROOT.rglob("*.bru"))


def test_bruno_collection_uses_importable_block_syntax() -> None:
    files = _bru_files()
    if not files:
        pytest.skip("docs/api Bruno collection is not present")

    for path in files:
        text = path.read_text(encoding="utf-8")
        if "tests" in text:
            assert re.search(r"(?m)^tests\s*\{", text), f"{path} must use tests {{}} syntax"
        if "body:json" in text:
            assert re.search(
                r"(?m)^body:json\s*\{", text
            ), f"{path} must use body:json {{}} syntax"

        assert "bru.setVar[" not in text, f"{path} must call bru.setVar()"
        assert _balanced_braces(text), f"{path} has unbalanced braces"


def _balanced_braces(text: str) -> bool:
    depth = 0
    for char in text:
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0
