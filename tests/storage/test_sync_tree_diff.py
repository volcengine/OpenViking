import pytest

from openviking.storage.viking_fs._sync import _SyncMixin
from openviking_cli.exceptions import NotFoundError


class _FakeSyncFS(_SyncMixin):
    """In-memory FS with just the surface sync_tree touches."""

    def __init__(self, files, dirs):
        self.files = dict(files)
        self.dirs = set(dirs)

    async def exists(self, uri, ctx=None):
        return uri in self.files or uri in self.dirs

    async def ls(self, uri, show_all_hidden=False, node_limit=None, ctx=None):
        prefix = uri.rstrip("/") + "/"
        entries: list[dict] = []
        seen: set[str] = set()
        for d in sorted(self.dirs):
            rest = d[len(prefix) :] if d.startswith(prefix) else ""
            if rest and "/" not in rest:
                entries.append({"name": rest, "isDir": True})
                seen.add(rest)
        for f in sorted(self.files):
            if not f.startswith(prefix):
                continue
            rest = f[len(prefix) :]
            name = rest.split("/", 1)[0]
            if name in seen:
                continue
            if "/" not in rest:
                entries.append({"name": rest, "isDir": False})
                seen.add(rest)
        return entries

    async def stat(self, uri, ctx=None, skip_count=False):
        if uri in self.files:
            return {"size": len(self.files[uri])}
        raise NotFoundError(uri, "file")

    async def read_file(self, uri, ctx=None):
        if uri in self.files:
            return self.files[uri]
        raise NotFoundError(uri, "file")

    async def rm(self, uri, ctx=None, recursive=False, lease_ref=None):
        self.files.pop(uri, None)
        self.dirs.discard(uri)
        if recursive:
            prefix = uri.rstrip("/") + "/"
            self.files = {k: v for k, v in self.files.items() if not k.startswith(prefix)}
            self.dirs = {d for d in self.dirs if not d.startswith(prefix)}

    async def mv(self, src, dst, ctx=None, lease_ref=None):
        if src not in self.files and src not in self.dirs:
            raise NotFoundError(src, "file")
        if src in self.files:
            self.files[dst] = self.files.pop(src)
            return
        prefix = src.rstrip("/") + "/"
        for f in [f for f in self.files if f.startswith(prefix)]:
            self.files[dst.rstrip("/") + "/" + f[len(prefix) :]] = self.files.pop(f)
        self.dirs = {
            dst + d[len(src) :] if d.startswith(prefix) else d for d in self.dirs
        }
        self.dirs.discard(src)
        self.dirs.add(dst)

    async def mkdir(self, uri, exist_ok=True, ctx=None, lease_ref=None):
        self.dirs.add(uri)

    async def delete_temp(self, uri, ctx=None):
        await self.rm(uri, recursive=True)


@pytest.mark.asyncio
async def test_sync_tree_diff_reports_only_applied_moves():
    staging = "viking://user/default/resources/staging"
    target = "viking://user/default/resources/target"
    fs = _FakeSyncFS(
        files={
            f"{staging}/control.md": "control",
            f"{staging}/added.md": "added",
            f"{staging}/updated.md": "new content",
            f"{target}/updated.md": "old",
        },
        dirs=[staging, target],
    )
    real_mv = fs.mv

    async def failing_mv(src, dst, **kwargs):
        if str(dst).endswith(("added.md", "updated.md")):
            raise OSError("injected transient mv failure")
        return await real_mv(src, dst, **kwargs)

    fs.mv = failing_mv

    diff = await fs.sync_tree(
        staging,
        target,
        file_change_status={f"{staging}/updated.md": True},
    )

    # Only the mv that succeeded is reported. On main, added.md and
    # updated.md are recorded before their mv runs, so a swallowed
    # failure still shows up in the diff that drives the incremental
    # semantic tree and the task's change report.
    assert [u.rsplit("/", 1)[-1] for u in diff.added_files] == ["control.md"]
    assert diff.updated_files == []
