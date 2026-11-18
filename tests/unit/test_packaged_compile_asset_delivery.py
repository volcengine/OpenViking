"""A changed packaged Compile rule must reach existing accounts through its asset owner."""

import json

from openviking.core.namespace_constants import SYSTEM_MEMORY_CUSTOM_ROOT
from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
from openviking.system_assets import SYSTEM_ASSETS_DIR
from openviking.system_assets.reconcile import SystemAssetReconciler
from openviking.system_assets.sync import SYSTEM_ASSET_MANIFEST_PATH, SystemAssetSynchronizer
from openviking.utils.agfs_utils import RagfsBindingConfig, create_agfs_client
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.agfs_config import AGFSConfig
from scripts.build_support.system_assets_manifest import build_system_assets_manifest

# The preceding package marker must trigger delivery of the new rule. This is a
# historical fixture, not an expected digest for all future versions.
PREVIOUS_DIGEST = "9ea66fbe2a48c529db366ee7ba0241a2684b5f8acc846c511ea4db4c3f98abde"
RULE = "compile/example-skills/llm-wiki/references/conflicts.md"
URI = "viking://system/compile/example-skills/llm-wiki/references/conflicts.md"


def test_packaged_manifest_matches_all_managed_asset_bytes():
    assert (SYSTEM_ASSETS_DIR / "manifest.json").read_bytes() == build_system_assets_manifest(
        SYSTEM_ASSETS_DIR
    )


async def test_real_reconcile_updates_old_marker_accounts_and_then_skips_unchanged(tmp_path):
    client = create_agfs_client(
        RagfsBindingConfig(agfs=AGFSConfig(path=str(tmp_path / "storage"), backend="local"))
    )
    fs = VikingFS(agfs=client)
    agfs = fs._async_agfs
    custom_uri = SYSTEM_MEMORY_CUSTOM_ROOT + "/owned.yaml"
    prior = b"# Previous packaged guidance\n"
    custom = b"custom: preserved\n"
    contexts = {
        account: RequestContext(user=UserIdentifier(account, "system-user"), role=Role.ROOT)
        for account in ("alpha", "beta", "deleting")
    }
    pages = []

    class Accounts:
        async def list_accounts(self, *, limit, page):
            pages.append(page)
            rows = [
                {"account_id": "alpha", "status": "active"},
                {"account_id": "beta", "status": "active"},
                {"account_id": "deleting", "status": "deleting"},
            ]
            return rows[(page - 1) * limit : page * limit]

    try:
        # Seed the real global skip marker and account content, without replacing
        # either reconciler or synchronizer with a mock that always runs.
        marker = json.dumps({"schema_version": 1, "sha256": PREVIOUS_DIGEST}).encode()
        await agfs.ensure_parent_dirs(SYSTEM_ASSET_MANIFEST_PATH)
        await agfs.write(SYSTEM_ASSET_MANIFEST_PATH, marker)
        for ctx in contexts.values():
            for uri, content in ((URI, prior), (custom_uri, custom)):
                path = fs._uri_to_path(uri, ctx=ctx)
                await agfs.ensure_parent_dirs(path)
                await agfs.write(path, content)
        synchronizer = SystemAssetSynchronizer(fs)
        reconciler = SystemAssetReconciler(
            synchronizer, Accounts(), agfs, user_id="system-user", page_size=2
        )
        assert await reconciler.sync_if_changed() == 2
        assert pages == [1, 2]
        expected = (SYSTEM_ASSETS_DIR / RULE).read_bytes()
        assert await fs.read_file_bytes(URI, ctx=contexts["alpha"]) == expected
        assert await fs.read_file_bytes(URI, ctx=contexts["beta"]) == expected
        assert await fs.read_file_bytes(URI, ctx=contexts["deleting"]) == prior
        for ctx in contexts.values():
            assert await fs.read_file_bytes(custom_uri, ctx=ctx) == custom
        persisted = json.loads(await agfs.read(SYSTEM_ASSET_MANIFEST_PATH))
        assert persisted["sha256"] == synchronizer.manifest_digest != PREVIOUS_DIGEST
        pages.clear()
        assert await reconciler.sync_if_changed() == 0
        assert pages == []
    finally:
        client.close()
