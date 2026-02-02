# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Import/export tests"""

from pathlib import Path

from openviking import AsyncOpenViking


class TestExportOvpack:
    """Test export_ovpack"""

    async def test_export_success(self, client_with_resource, temp_dir: Path):
        """Test successful export"""
        client, uri = client_with_resource
        export_path = temp_dir / "export.ovpack"

        result = await client.export_ovpack(uri, str(export_path))

        assert isinstance(result, str)
        assert Path(result).exists()

    async def test_export_directory(
        self, client: AsyncOpenViking, sample_directory: Path, temp_dir: Path
    ):
        """Test exporting directory"""
        # Add files from directory
        for f in sample_directory.glob("**/*.txt"):
            await client.add_resource(path=str(f), reason="Test export dir")

        # Export entire resource directory
        export_path = temp_dir / "dir_export.ovpack"
        result = await client.export_ovpack("viking://resources/", str(export_path))

        assert isinstance(result, str)


class TestImportOvpack:
    """Test import_ovpack"""

    async def test_import_success(self, client_with_resource, temp_dir: Path):
        """Test successful import"""
        client, uri = client_with_resource

        # Export first
        export_path = temp_dir / "import_test.ovpack"
        await client.export_ovpack(uri, str(export_path))

        # Import to new location
        import_uri = await client.import_ovpack(
            str(export_path), "viking://resources/imported/", vectorize=False
        )

        assert isinstance(import_uri, str)
        assert "imported" in import_uri

    async def test_import_with_force(self, client_with_resource, temp_dir: Path):
        """Test force overwrite import"""
        client, uri = client_with_resource

        # Export first
        export_path = temp_dir / "force_test.ovpack"
        await client.export_ovpack(uri, str(export_path))

        # First import
        await client.import_ovpack(
            str(export_path), "viking://resources/force_test/", vectorize=False
        )

        # Second force import (overwrite)
        import_uri = await client.import_ovpack(
            str(export_path), "viking://resources/force_test/", force=True, vectorize=False
        )

        assert isinstance(import_uri, str)

    async def test_import_export_roundtrip(
        self, client: AsyncOpenViking, sample_markdown_file: Path, temp_dir: Path
    ):
        """Test export-import roundtrip"""
        # Add resource
        result = await client.add_resource(path=str(sample_markdown_file), reason="Roundtrip test")
        original_uri = result["root_uri"]

        # Read original content
        original_content = ""
        entries = await client.tree(original_uri)
        for e in entries:
            if not e["isDir"]:
                original_content = await client.read(e["uri"])

        # Export
        export_path = temp_dir / "roundtrip.ovpack"
        await client.export_ovpack(original_uri, str(export_path))

        # Delete original resource
        await client.rm(original_uri, recursive=True)

        # Import
        import_uri = await client.import_ovpack(
            str(export_path), "viking://resources/roundtrip/", vectorize=False
        )

        # Read imported content
        imported_content = ""
        entries = await client.tree(import_uri)
        for e in entries:
            if not e["isDir"]:
                imported_content = await client.read(e["uri"])

        # Verify content consistency
        assert original_content == imported_content
