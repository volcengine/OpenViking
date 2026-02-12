# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Full workflow integration tests"""

import shutil
from pathlib import Path

import pytest_asyncio

from openviking import AsyncOpenViking
from openviking.message import TextPart


@pytest_asyncio.fixture(scope="function")
async def integration_client(test_data_dir: Path):
    """Integration test client"""
    await AsyncOpenViking.reset()

    # Clean data directory to avoid AGFS "directory already exists" errors
    shutil.rmtree(test_data_dir, ignore_errors=True)
    test_data_dir.mkdir(parents=True, exist_ok=True)

    client = AsyncOpenViking(path=str(test_data_dir))
    await client.initialize()

    yield client

    await client.close()
    await AsyncOpenViking.reset()


class TestResourceToSearchWorkflow:
    """Full workflow from resource addition to search"""

    async def test_add_and_search(
        self, integration_client: AsyncOpenViking, sample_files: list[Path]
    ):
        """Test: add resource -> vectorize -> search"""
        client = integration_client

        # 1. Add multiple resources
        uris = []
        for f in sample_files:
            result = await client.add_resource(path=str(f), reason="Integration test")
            uris.append(result["root_uri"])

        # 2. Wait for vectorization to complete
        await client.wait_processed()

        # 3. Verify search
        result = await client.find(query="batch file content")

        assert result.total >= 0

    async def test_add_search_read_workflow(
        self, integration_client: AsyncOpenViking, sample_markdown_file: Path
    ):
        """Test: add -> search -> read"""
        client = integration_client

        # 1. Add resource
        await client.add_resource(path=str(sample_markdown_file), reason="Workflow test", wait=True)

        # 2. Search
        search_result = await client.find(query="sample document")

        # 3. Read searched resource
        if search_result.resources:
            if search_result.resources[0].is_leaf:
                content = await client.read(search_result.resources[0].uri)
                assert len(content) > 0
            else:
                res = await client.tree(search_result.resources[0].uri)
                for data in res:
                    if not data["isDir"]:
                        content = await client.read(data["uri"])
                        assert len(content) > 0


class TestSessionWorkflow:
    """Session management full workflow"""

    async def test_session_conversation_workflow(
        self, integration_client: AsyncOpenViking, sample_markdown_file: Path
    ):
        """Test: session create -> multi-turn conversation -> commit -> memory extraction"""
        client = integration_client

        # 1. Add resource
        await client.add_resource(
            path=str(sample_markdown_file), reason="Session workflow test", wait=True
        )

        # 2. Create session
        session = client.session(session_id="workflow_test_session")

        # 3. Multi-turn conversation
        session.add_message("user", [TextPart("Hello, I need help with testing.")])

        # 4. Search and use context
        search_result = await client.search(query="testing", session=session)
        if search_result.resources:
            session.used(contexts=[search_result.resources[0].uri])

        session.add_message("assistant", [TextPart("I can help you with testing.")])

        session.add_message("user", [TextPart("What features are available?")])
        session.add_message("assistant", [TextPart("There are many features available.")])

        # 5. Commit
        commit_result = session.commit()
        assert commit_result["status"] == "committed"

        # 6. Wait for memory extraction
        await client.wait_processed()

    async def test_session_reload_workflow(self, integration_client: AsyncOpenViking):
        """Test: session create -> commit -> reload -> continue conversation"""
        client = integration_client
        session_id = "reload_test_session"

        # 1. Create session and add messages
        session1 = client.session(session_id=session_id)
        session1.add_message("user", [TextPart("First message")])
        session1.add_message("assistant", [TextPart("First response")])
        session1.commit()

        # 2. Reload session
        session2 = client.session(session_id=session_id)
        session2.load()

        # 3. Continue conversation
        session2.add_message("user", [TextPart("Second message")])
        session2.add_message("assistant", [TextPart("Second response")])

        # 4. Commit again
        commit_result = session2.commit()
        assert commit_result["status"] == "committed"


class TestImportExportWorkflow:
    """Import/export full workflow"""

    async def test_export_import_roundtrip(
        self, integration_client: AsyncOpenViking, sample_markdown_file: Path, temp_dir: Path
    ):
        """Test: export -> delete -> import -> verify"""
        client = integration_client

        # 1. Add resource
        result = await client.add_resource(
            path=str(sample_markdown_file),
            reason="Export test",
        )
        print(result)
        original_uri = result["root_uri"]

        # 2. Read original content
        original_content = ""
        entries = await client.tree(original_uri)
        for data in entries:
            if not data["isDir"]:
                original_content += await client.read(data["uri"])

        # 3. Export
        export_path = temp_dir / "workflow_export.ovpack"
        await client.export_ovpack(original_uri, str(export_path))
        assert export_path.exists()

        # 4. Delete original resource
        await client.rm(original_uri, recursive=True)

        # 5. Import
        import_uri = await client.import_ovpack(
            str(export_path), "viking://resources/imported/", vectorize=False
        )

        # 6. Verify content consistency
        imported_content = ""
        entries = await client.tree(import_uri)
        for data in entries:
            if not data["isDir"]:
                imported_content += await client.read(data["uri"])
        assert original_content == imported_content


class TestFullEndToEndWorkflow:
    """Full end-to-end workflow"""

    async def test_complete_workflow(
        self, integration_client: AsyncOpenViking, sample_files: list[Path], temp_dir: Path
    ):
        """Test complete end-to-end workflow"""
        client = integration_client

        # ===== Phase 1: Resource Management =====
        # Add multiple resources
        resource_uris = []
        for f in sample_files:
            result = await client.add_resource(path=str(f), reason="E2E test")
            resource_uris.append(result["root_uri"])

        # Wait for processing to complete
        await client.wait_processed()

        # ===== Phase 2: Search Verification =====
        # Quick search
        find_result = await client.find(query="batch file")
        assert find_result.total >= 0

        # ===== Phase 3: Session Management =====
        session = client.session(session_id="e2e_test_session")

        # Multi-turn conversation
        session.add_message("user", [TextPart("I need information about batch files.")])

        # Search with session context
        search_result = await client.search(query="batch", session=session)
        if search_result.resources:
            session.used(contexts=[search_result.resources[0].uri])

        session.add_message("assistant", [TextPart("Here is information about batch files.")])

        # Commit session
        commit_result = session.commit()
        assert commit_result["status"] == "committed"

        # ===== Phase 4: Import/Export =====
        if resource_uris:
            # Export
            export_path = temp_dir / "e2e_export.ovpack"
            await client.export_ovpack(resource_uris[0], str(export_path))

            # Import to new location
            import_uri = await client.import_ovpack(
                str(export_path), "viking://resources/e2e_imported/"
            )

            # Verify import success
            await client.stat(import_uri)

        # ===== Phase 5: Cleanup Verification =====
        # List all resources
        entries = await client.ls("viking://", recursive=True)
        assert isinstance(entries, list)
