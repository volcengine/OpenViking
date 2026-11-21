from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from demo import load_documents  # noqa: E402


def test_load_documents_reads_three_sample_docs():
    docs = load_documents()
    assert len(docs) == 3
    assert {Path(doc["path"]).name for doc in docs} == {
        "deployment.md",
        "incident-playbook.md",
        "retention-policy.md",
    }
