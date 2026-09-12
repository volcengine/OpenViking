# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests that the file md5 computed at apply time reaches the vector record.

Local incremental import computes each changed file's md5 while uploading, then
threads it through SemanticMsg.file_md5s -> DAG -> vectorize_file so the vector
record carries the fresh fingerprint. Without this a just-changed file's stored
md5 stays stale and the next no-op falls back to byte comparison.
"""

from openviking.storage.queuefs.semantic_msg import SemanticMsg


class TestSemanticMsgFileMd5s:
    def test_roundtrip_preserves_file_md5s(self) -> None:
        msg = SemanticMsg(
            uri="viking://resources/x",
            context_type="resource",
            file_md5s={"viking://resources/x/a.py": "md5a"},
        )
        restored = SemanticMsg.from_dict(msg.to_dict())
        assert restored.file_md5s == {"viking://resources/x/a.py": "md5a"}

    def test_defaults_to_empty(self) -> None:
        msg = SemanticMsg(uri="viking://resources/x", context_type="resource")
        assert msg.file_md5s == {}
        assert SemanticMsg.from_dict(msg.to_dict()).file_md5s == {}

    def test_roundtrip_preserves_local_artifact_snapshot(self) -> None:
        msg = SemanticMsg(
            uri="viking://resources/x",
            context_type="resource",
            artifact_ref={
                "backend": "local",
                "root": "/tmp/artifact-1",
                "resource_rel": "repository",
                "root_type": "dir",
            },
            artifact_files=["a.py", "src/b.py"],
            file_abstracts={"viking://resources/x/a.py": "summary a"},
        )

        restored = SemanticMsg.from_json(msg.to_json())

        assert restored.artifact_ref == msg.artifact_ref
        assert restored.artifact_ref["resource_rel"] == "repository"
        assert restored.artifact_files == ["a.py", "src/b.py"]
        assert restored.file_abstracts == {"viking://resources/x/a.py": "summary a"}
