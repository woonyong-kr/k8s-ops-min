import re
from pathlib import Path

# 내부 문서 지도는 private/ 에 있다. docs/ 는 외부 공개용 색인이라
# 내부 키워드(command, RCA, Safe PR 등)를 담지 않는다.
DOCS_ROOT = Path(__file__).resolve().parents[1] / "private"
README = DOCS_ROOT / "README.md"

REQUIRED_KEYWORDS = (
    "command",
    "target",
    "evidence",
    "RCA",
    "Safe PR",
    "dashboard",
    "permission",
    "Bruno",
    "AWS",
    "event",
    "provider",
    "worker",
    "test",
    "GitOps",
    "realtime",
)

FORBIDDEN_DOC_TERMS = (
    "K8sGPT",
    "HolmesGPT",
    "Kubeheal",
    "Cloudflare",
)


LOCAL_MD_LINK = re.compile(r"\[[^\]]+\]\(([^)]+\.md(?:#[^)]+)?)\)")


def test_docs_readme_links_all_docs_within_three_levels() -> None:
    assert README.exists(), "private/README.md must be the internal documentation root"

    docs = sorted(path.resolve() for path in DOCS_ROOT.rglob("*.md") if path != README)
    assert docs, "private/README.md should link at least one private/*.md file"

    seen: dict[Path, int] = {README.resolve(): 0}
    queue = [README.resolve()]
    while queue:
        current = queue.pop(0)
        depth = seen[current]
        if depth >= 3:
            continue
        for target in _local_markdown_links(current):
            if target not in seen or seen[target] > depth + 1:
                seen[target] = depth + 1
                queue.append(target)

    missing = [str(path.relative_to(DOCS_ROOT)) for path in docs if path not in seen]
    assert not missing, "private/README.md is missing <=3-level links to: " + ", ".join(missing)


def test_docs_readme_keyword_entrypoints() -> None:
    text = README.read_text(encoding="utf-8")
    missing = [keyword for keyword in REQUIRED_KEYWORDS if keyword not in text]
    assert not missing, f"private/README.md missing keyword entrypoints: {', '.join(missing)}"


def test_docs_avoid_forbidden_external_product_terms() -> None:
    docs = sorted(DOCS_ROOT.rglob("*.md"))
    hits: list[str] = []
    for path in docs:
        text = path.read_text(encoding="utf-8")
        for term in FORBIDDEN_DOC_TERMS:
            if term in text:
                hits.append(f"{path.relative_to(DOCS_ROOT.parent)}: {term}")

    assert not hits, "Use generic wording such as 외부 기준 저장소 or 벤치마크 최소선: " + ", ".join(
        hits
    )


def _local_markdown_links(path: Path) -> list[Path]:
    links: list[Path] = []
    for match in LOCAL_MD_LINK.finditer(path.read_text(encoding="utf-8")):
        href = match.group(1).split("#", 1)[0]
        if href.startswith(("http://", "https://", "mailto:")):
            continue
        target = (path.parent / href).resolve()
        if target.is_file() and target.is_relative_to(DOCS_ROOT.resolve()):
            links.append(target)
    return links
