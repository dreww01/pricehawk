"""Regression tests for GitHub-renderable Mermaid documentation."""

from pathlib import Path


DOCS_WITH_MERMAID_API_ROUTES = (
    Path("docs/architecture.md"),
    Path("docs/logic_used.md"),
)


def test_mermaid_api_route_labels_are_quoted() -> None:
    """Route labels with braces must be quoted to avoid Mermaid shape parsing."""
    for doc_path in DOCS_WITH_MERMAID_API_ROUTES:
        content = doc_path.read_text(encoding="utf-8")
        assert "[GET /api/insights/{product_id}]" not in content, (
            f"{doc_path} contains an unquoted Mermaid label with route braces"
        )
        assert "[POST /api/insights/generate/{product_id}]" not in content, (
            f"{doc_path} contains an unquoted Mermaid label with route braces"
        )

    logic_doc = Path("docs/logic_used.md").read_text(encoding="utf-8")
    architecture_doc = Path("docs/architecture.md").read_text(encoding="utf-8")
    assert 'Start["POST /api/insights/generate/{product_id}"]' in logic_doc
    assert 'API["GET /api/insights/{product_id}"]' in architecture_doc
