import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = ROOT / "docs"
README = ROOT / "README.md"

FORBIDDEN_DOC_TERMS = (
    "K8sGPT",
    "HolmesGPT",
    "Kubeheal",
    "Cloudflare",
)

LOCAL_MD_LINK = re.compile(r"\[[^\]]+\]\(([^)]+\.md(?:#[^)]+)?)\)")


def test_root_readme_links_every_public_document() -> None:
    """대표 README에서 공개 설계 문서로 한 번에 이동할 수 있어야 한다."""
    linked = {
        (README.parent / match.group(1).split("#", 1)[0]).resolve()
        for match in LOCAL_MD_LINK.finditer(README.read_text(encoding="utf-8"))
    }
    public_docs = {
        path.resolve()
        for path in DOCS_ROOT.glob("*.md")
        if path.name != "README.md"
    }
    missing = sorted(str(path.relative_to(ROOT)) for path in public_docs - linked)
    assert not missing, "README.md에서 찾을 수 없는 공개 문서: " + ", ".join(missing)


def test_local_markdown_links_resolve() -> None:
    broken: list[str] = []
    for path in [README, *sorted(DOCS_ROOT.rglob("*.md"))]:
        for match in LOCAL_MD_LINK.finditer(path.read_text(encoding="utf-8")):
            href = match.group(1).split("#", 1)[0]
            if href.startswith(("http://", "https://", "mailto:")):
                continue
            target = (path.parent / href).resolve()
            if not target.is_file():
                broken.append(f"{path.relative_to(ROOT)} -> {href}")
    assert not broken, "깨진 로컬 Markdown 링크: " + ", ".join(broken)


def test_docs_avoid_unrelated_external_product_terms() -> None:
    hits: list[str] = []
    for path in sorted(DOCS_ROOT.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        for term in FORBIDDEN_DOC_TERMS:
            if term in text:
                hits.append(f"{path.relative_to(ROOT)}: {term}")
    assert not hits, "외부 제품명 대신 비교 기준을 설명한다: " + ", ".join(hits)
