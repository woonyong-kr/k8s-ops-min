"""문서가 코드보다 뒤처지지 않게 고정한다.

이 저장소는 세 번 같은 실패를 했다. 질의를 고치고 문서를 안 고쳤고, 인덱스를
추가하고 "설계하지 않았습니다"를 남겼고, 검사를 늘리고 개수를 안 바꿨다.
사람이 기억해서 맞추는 방식은 이미 실패했으므로 테스트로 옮긴다.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

SQL_DIR = ROOT / "sql" / "quality"
DOCS = ROOT / "docs"
README = ROOT / "README.md"


def _datacatalog_tables(models: object) -> list[object]:
    """공유 Base에 등록된 서비스 카탈로그 테이블은 제외한다."""
    return [
        value.__table__
        for value in vars(models).values()
        if isinstance(value, type)
        and value.__module__ == models.__name__
        and hasattr(value, "__table__")
    ]


def _sql_blocks(path: Path) -> list[str]:
    return [b.rstrip() for b in re.findall(r"```sql\n(.*?)\n```", path.read_text("utf-8"), re.S)]


@pytest.mark.parametrize("sql_path", sorted(SQL_DIR.glob("*.sql")), ids=lambda p: p.stem)
def test_문서에_실린_질의는_실제_파일과_같다(sql_path: Path):
    """문서 블록이 옛 버전이면 붙여넣어도 돌지 않고, 설계 근거도 다른 질의의 것이 된다."""
    blocks = _sql_blocks(DOCS / "sql-quality-checks.md")
    assert sql_path.read_text("utf-8").rstrip() in blocks, (
        f"{sql_path.name} 이 sql-quality-checks.md 의 어느 블록과도 일치하지 않는다"
    )


def test_검사와_조회_질의_개수가_문서와_맞는다():
    from domains.datacatalog.checks import CHECK_FILES, LOOKUP_FILES

    text = README.read_text("utf-8")
    assert f"검사 {len(CHECK_FILES)}종" in text
    assert f"조회 질의 {len(LOOKUP_FILES)}종" in text
    assert len(list(SQL_DIR.glob("*.sql"))) == len(CHECK_FILES) + len(LOOKUP_FILES)


def test_테이블과_유일제약_개수가_문서와_맞는다():
    from domains.datacatalog import models

    tables = _datacatalog_tables(models)
    uniques = [
        c
        for t in tables
        for c in t.constraints
        if type(c).__name__ == "UniqueConstraint"
    ]
    text = README.read_text("utf-8")
    assert f"{len(tables)}개 테이블" in text
    assert f"유일 제약 {len(uniques)}종" in text


def test_er_다이어그램이_모든_테이블을_담는다():
    from domains.datacatalog import models

    doc = (DOCS / "metadata-catalog.md").read_text("utf-8")
    diagram = re.search(r"```mermaid\nerDiagram(.*?)```", doc, re.S)
    assert diagram, "metadata-catalog.md 에 erDiagram 이 없다"
    drawn = set(re.findall(r"\b([a-z][a-z_]+)\b", diagram.group(1)))
    actual = {t.name[len("catalog_") :] for t in _datacatalog_tables(models)}
    assert not actual - drawn, f"다이어그램에 빠진 테이블: {sorted(actual - drawn)}"


def test_mcp_도구_개수가_문서와_맞는다():
    from services.catalog_mcp.server import TOOLS

    assert f"도구 {len(TOOLS)}종" in README.read_text("utf-8")


def test_검증표가_참조하는_테스트가_실제로_있다():
    """없는 테스트를 인용하면 검증했다는 주장 자체가 근거를 잃는다."""
    doc = (DOCS / "catalog-api-mcp.md").read_text("utf-8")
    referenced = set(re.findall(r"`(test_[a-z_]+\.py)::(test_[^`\s]+)`", doc))
    assert referenced, "검증표에 테스트 참조가 없다"
    for filename, func in referenced:
        path = Path(__file__).parent / filename
        assert path.exists(), f"{filename} 이 없다"
        assert f"def {func}(" in path.read_text("utf-8"), f"{filename} 에 {func} 가 없다"


def test_인덱스_개수_서술이_실제와_맞는다():
    models_src = (SRC / "domains" / "datacatalog" / "models.py").read_text("utf-8")
    count = models_src.count("Index(")
    for name in ("metadata-catalog.md", "sql-quality-checks.md"):
        text = (DOCS / name).read_text("utf-8")
        if "Index" in text or "인덱스" in text:
            assert "인덱스를 설계하지 않았" not in text
            assert "인덱스는 설계하지 않았" not in text
    assert f"{count}개" in (DOCS / "metadata-catalog.md").read_text("utf-8")


def test_문체가_섞이지_않는다():
    """포트폴리오 19개 중 2개만 다른 문체이면 읽는 사람이 편집 흔적을 먼저 본다."""
    # 존댓말은 "…니다." 로 끝난다. 어미를 열거하면 반드시 빠뜨린다 —
    # 실제로 "쓰인다" "구조체다" 같은 것을 놓쳐 64곳이 남아 있었다.
    plain = re.compile(r"(?:(?<!니)다|아니다)\.")
    offenders: list[str] = []
    for path in sorted(DOCS.glob("*.md")):
        in_code = False
        for lineno, raw in enumerate(path.read_text("utf-8").splitlines(), 1):
            line = re.sub(r"[\u201c\"][^\u201d\"]*[\u201d\"]", "", re.sub(r"`[^`]*`", "", raw.strip()))
            if line.startswith("```"):
                in_code = not in_code
                continue
            if in_code or not line or line.startswith(("|", ">", "--")):
                continue
            if plain.search(line):
                offenders.append(f"{path.name}:{lineno}")
    assert not offenders, "본문 문체가 ~습니다체와 섞였다: " + ", ".join(offenders[:10])
