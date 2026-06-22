from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read_text(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_pr_review_workflow_avoids_untrusted_target_and_write_contents():
    workflow = _read_text(".github/workflows/pr-review.yml")

    assert "pull_request_target:" not in workflow
    assert "contents: write" not in workflow
    assert "qodo-ai/pr-agent@main" not in workflow
