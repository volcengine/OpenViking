from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.resource_service import ResourceService
from openviking.storage.queuefs.add_resource_msg import AddResourceMsg
from openviking_cli.session.user_id import UserIdentifier


def _ctx() -> RequestContext:
    return RequestContext(
        user=UserIdentifier("account-1", "user-1"),
        role=Role.USER,
    )


def _service(viking_fs, resource_processor=None) -> ResourceService:
    return ResourceService(
        vikingdb=object(),
        viking_fs=viking_fs,
        resource_processor=resource_processor or SimpleNamespace(),
        skill_processor=object(),
    )


@pytest.mark.asyncio
async def test_cleanup_reserved_target_removes_only_empty_directory():
    lock = {"lease_ref": "lock-1"}
    ctx = _ctx()
    viking_fs = SimpleNamespace(
        exists=AsyncMock(return_value=True),
        stat=AsyncMock(return_value={"isDir": True}),
        ls=AsyncMock(return_value=[]),
        rm=AsyncMock(),
    )
    service = _service(viking_fs)

    removed = await service._cleanup_reserved_target_if_empty(
        root_uri="viking://resources/empty",
        ctx=ctx,
        resource_lock=lock,
    )

    assert removed is True
    viking_fs.rm.assert_awaited_once_with(
        "viking://resources/empty",
        recursive=True,
        ctx=ctx,
        lease_ref=lock,
    )


@pytest.mark.asyncio
async def test_cleanup_reserved_target_preserves_nonempty_directory():
    viking_fs = SimpleNamespace(
        exists=AsyncMock(return_value=True),
        stat=AsyncMock(return_value={"isDir": True}),
        ls=AsyncMock(return_value=[{"name": "document.md", "isDir": False}]),
        rm=AsyncMock(),
    )
    service = _service(viking_fs)

    removed = await service._cleanup_reserved_target_if_empty(
        root_uri="viking://resources/nonempty",
        ctx=_ctx(),
        resource_lock={"lease_ref": "lock-1"},
    )

    assert removed is False
    viking_fs.rm.assert_not_awaited()


@pytest.mark.asyncio
async def test_cleanup_reserved_target_preserves_content_appearing_mid_rollback():
    """A concurrent writer between emptiness listings must preserve the target."""
    viking_fs = SimpleNamespace(
        exists=AsyncMock(return_value=True),
        stat=AsyncMock(return_value={"isDir": True}),
        ls=AsyncMock(
            side_effect=[
                [{"name": ".path.ovlock", "isDir": False}],
                [
                    {"name": ".path.ovlock", "isDir": False},
                    {"name": "document.md", "isDir": False},
                ],
            ]
        ),
        rm=AsyncMock(),
    )
    service = _service(viking_fs)

    removed = await service._cleanup_reserved_target_if_empty(
        root_uri="viking://resources/racy",
        ctx=_ctx(),
        resource_lock={"lease_ref": "lock-1"},
    )

    assert removed is False
    assert viking_fs.ls.await_count == 2
    viking_fs.rm.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target_preexisting", "cleanup_empty_target_on_failure"),
    [(False, True), (True, False)],
)
async def test_explicit_target_plan_tracks_reservation_ownership(
    target_preexisting: bool,
    cleanup_empty_target_on_failure: bool,
):
    lock = {"lease_ref": "lock-1"}
    calls = []

    async def acquire_lock(*args, **kwargs):
        calls.append("lock")
        return lock

    async def target_exists(*args, **kwargs):
        calls.append("exists")
        return target_preexisting

    async def ensure_access(*args, **kwargs):
        calls.append("acl")

    viking_fs = SimpleNamespace(
        exists=AsyncMock(side_effect=target_exists),
        _ensure_access=AsyncMock(side_effect=ensure_access),
        _uri_to_path=lambda uri, ctx: f"/agfs/{uri}",
        _async_agfs=SimpleNamespace(pathlock_acquire_tree=AsyncMock(side_effect=acquire_lock)),
    )
    processor = SimpleNamespace(
        tree_builder=SimpleNamespace(
            resolve_target_uri=AsyncMock(return_value=("viking://resources/report", None))
        )
    )
    service = _service(viking_fs, processor)

    planned = await service._plan_source_job_target(
        path="report.zip",
        ctx=_ctx(),
        to="viking://resources/report",
        parent="",
        create_parent=False,
        source_info=SimpleNamespace(
            source_path="report.zip",
            source_name="report.zip",
            source_format="zip",
        ),
        defer_candidate_resolution=False,
    )

    assert planned == (
        "viking://resources/report",
        lock,
        False,
        cleanup_empty_target_on_failure,
    )
    assert calls == ["acl", "exists", "lock", "acl"]


@pytest.mark.asyncio
async def test_source_job_error_cleans_new_empty_reservation():
    viking_fs = SimpleNamespace()
    service = _service(viking_fs)
    service._execute_resource_ingestion = AsyncMock(
        return_value={"status": "error", "errors": ["directory produced no content"]}
    )
    service._cleanup_reserved_target_if_empty = AsyncMock(return_value=True)
    lock = {"lease_ref": "lock-1"}
    ctx = _ctx()
    msg = AddResourceMsg(
        task_id="task-1",
        path="report.zip",
        root_uri="viking://resources/report",
        account_id="account-1",
        user_id="user-1",
        role="user",
        cleanup_empty_target_on_failure=True,
    )

    result = await service.execute_add_resource_job(
        msg,
        ctx=ctx,
        resource_lock=lock,
        stage_callback=AsyncMock(),
    )

    assert result["status"] == "error"
    service._cleanup_reserved_target_if_empty.assert_awaited_once_with(
        root_uri="viking://resources/report",
        ctx=ctx,
        resource_lock=lock,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_on_failure", [False, True])
@pytest.mark.parametrize("raises_error", [False, True])
async def test_prepared_file_failure_preserves_cleanup_policy(cleanup_on_failure, raises_error):
    service = _service(SimpleNamespace())
    error_message = "remote parser rejected file"
    error_result = {"status": "error", "errors": [error_message]}
    service._execute_resource_ingestion = AsyncMock(
        side_effect=RuntimeError(error_message) if raises_error else None,
        return_value=error_result,
    )
    service._cleanup_reserved_target_if_empty = AsyncMock(return_value=True)
    lock = {"lease_ref": "lock-1"}
    ctx = _ctx()
    msg = AddResourceMsg.from_dict(
        AddResourceMsg(
            task_id="task-1",
            path="report.pdf",
            root_uri="viking://resources/report",
            account_id="account-1",
            user_id="user-1",
            role="user",
            understanding_file_id="file-1",
            cleanup_empty_target_on_failure=cleanup_on_failure,
            internal_task=True,
        ).to_dict()
    )

    job = service.execute_add_resource_job(
        msg,
        ctx=ctx,
        resource_lock=lock,
        stage_callback=AsyncMock(),
    )
    if raises_error:
        with pytest.raises(RuntimeError, match=error_message):
            await job
    else:
        assert await job == error_result

    service._execute_resource_ingestion.assert_awaited_once()
    kwargs = service._execute_resource_ingestion.await_args.kwargs
    assert kwargs["understanding_file_id"] == "file-1"
    assert kwargs["parser_backend"] == "understanding"
    assert kwargs["internal_task"] is True
    if cleanup_on_failure:
        service._cleanup_reserved_target_if_empty.assert_awaited_once_with(
            root_uri=msg.root_uri,
            ctx=ctx,
            resource_lock=lock,
        )
    else:
        service._cleanup_reserved_target_if_empty.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "names",
    [
        [".path.ovlock"],
        [".exact.ovlock.7f3a1c"],
        [".path.ovlock", ".exact.ovlock.7f3a1c", ".redirect.json", ".sync_log.json"],
        ["_system", "tasks"],
        [".abstract.md", ".overview.md"],
        [".path.ovlock", ".abstract.md", ".overview.md", ".relations.json"],
    ],
)
async def test_cleanup_reserved_target_removes_directory_with_internal_markers_only(names):
    """#4501: empty / not-ready sidecars plus lock markers still roll back."""
    viking_fs = SimpleNamespace(
        exists=AsyncMock(return_value=True),
        stat=AsyncMock(return_value={"isDir": True}),
        ls=AsyncMock(return_value=[{"name": name, "isDir": False} for name in names]),
        read_file=AsyncMock(return_value=""),
        rm=AsyncMock(),
    )
    service = _service(viking_fs)

    removed = await service._cleanup_reserved_target_if_empty(
        root_uri="viking://resources/repro/broken-pdf",
        ctx=_ctx(),
        resource_lock={"lease_ref": "lock-1"},
    )

    assert removed is True
    viking_fs.rm.assert_awaited_once_with(
        "viking://resources/repro/broken-pdf",
        recursive=True,
        ctx=ANY,
        lease_ref={"lease_ref": "lock-1"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,content",
    [
        (".abstract.md", "[.abstract.md is not ready]"),
        (".abstract.md", "# viking://resources/demo [Directory abstract is not ready]"),
        (".overview.md", "# demo\n\n[Directory overview is not ready]"),
    ],
)
async def test_cleanup_reserved_target_removes_not_ready_sidecars(name, content):
    viking_fs = SimpleNamespace(
        exists=AsyncMock(return_value=True),
        stat=AsyncMock(return_value={"isDir": True}),
        ls=AsyncMock(
            return_value=[
                {"name": ".path.ovlock", "isDir": False},
                {"name": name, "isDir": False, "size": len(content)},
            ]
        ),
        read_file=AsyncMock(return_value=content),
        rm=AsyncMock(),
    )
    service = _service(viking_fs)

    removed = await service._cleanup_reserved_target_if_empty(
        root_uri="viking://resources/repro/stub-only",
        ctx=_ctx(),
        resource_lock={"lease_ref": "lock-1"},
    )

    assert removed is True
    viking_fs.rm.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_reserved_target_preserves_filled_abstract_sidecar():
    filled = "# Summary\n\nReal ingest abstract body."
    viking_fs = SimpleNamespace(
        exists=AsyncMock(return_value=True),
        stat=AsyncMock(return_value={"isDir": True}),
        ls=AsyncMock(
            return_value=[
                {"name": ".path.ovlock", "isDir": False},
                {"name": ".abstract.md", "isDir": False, "size": len(filled)},
            ]
        ),
        read_file=AsyncMock(return_value=filled),
        rm=AsyncMock(),
    )
    service = _service(viking_fs)

    removed = await service._cleanup_reserved_target_if_empty(
        root_uri="viking://resources/repro/filled-sidecar",
        ctx=_ctx(),
        resource_lock={"lease_ref": "lock-1"},
    )

    assert removed is False
    viking_fs.rm.assert_not_awaited()
    viking_fs.read_file.assert_awaited()


@pytest.mark.asyncio
async def test_cleanup_reserved_target_preserves_filled_relations_sidecar():
    filled = '{"links":[{"uri":"viking://resources/other"}]}'
    viking_fs = SimpleNamespace(
        exists=AsyncMock(return_value=True),
        stat=AsyncMock(return_value={"isDir": True}),
        ls=AsyncMock(
            return_value=[
                {"name": ".path.ovlock", "isDir": False},
                {"name": ".relations.json", "isDir": False, "size": len(filled)},
            ]
        ),
        read_file=AsyncMock(return_value=filled),
        rm=AsyncMock(),
    )
    service = _service(viking_fs)

    removed = await service._cleanup_reserved_target_if_empty(
        root_uri="viking://resources/repro/filled-relations",
        ctx=_ctx(),
        resource_lock={"lease_ref": "lock-1"},
    )

    assert removed is False
    viking_fs.rm.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["", "{}", "[]"])
async def test_cleanup_reserved_target_removes_empty_relations_stub(content):
    viking_fs = SimpleNamespace(
        exists=AsyncMock(return_value=True),
        stat=AsyncMock(return_value={"isDir": True}),
        ls=AsyncMock(
            return_value=[
                {"name": ".path.ovlock", "isDir": False},
                {"name": ".relations.json", "isDir": False, "size": len(content)},
            ]
        ),
        read_file=AsyncMock(return_value=content),
        rm=AsyncMock(),
    )
    service = _service(viking_fs)

    removed = await service._cleanup_reserved_target_if_empty(
        root_uri="viking://resources/repro/empty-relations",
        ctx=_ctx(),
        resource_lock={"lease_ref": "lock-1"},
    )

    assert removed is True
    viking_fs.rm.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_reserved_target_preserves_when_sidecar_read_fails():
    viking_fs = SimpleNamespace(
        exists=AsyncMock(return_value=True),
        stat=AsyncMock(return_value={"isDir": True}),
        ls=AsyncMock(return_value=[{"name": ".overview.md", "isDir": False, "size": 12}]),
        read_file=AsyncMock(side_effect=OSError("io error")),
        rm=AsyncMock(),
    )
    service = _service(viking_fs)

    removed = await service._cleanup_reserved_target_if_empty(
        root_uri="viking://resources/repro/unreadable-sidecar",
        ctx=_ctx(),
        resource_lock={"lease_ref": "lock-1"},
    )

    assert removed is False
    viking_fs.rm.assert_not_awaited()


def _post_process_msg(cleanup_on_failure: bool) -> AddResourceMsg:
    return AddResourceMsg(
        task_id="task-2",
        path="report.pdf",
        root_uri="viking://resources/report",
        account_id="account-1",
        user_id="user-1",
        role="user",
        prepared={
            "root_uri": "viking://resources/report",
            "temp_uri": "viking://temp/account-1/task-2",
            "temp_dir_path": "viking://temp/account-1/task-2",
            "source_committed": False,
            "target_preexisting": not cleanup_on_failure,
            "root_is_file": False,
        },
        cleanup_empty_target_on_failure=cleanup_on_failure,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_on_failure", [False, True])
async def test_post_process_job_error_rolls_back_owned_reservation(cleanup_on_failure):
    service = _service(SimpleNamespace())
    service._resource_processor = SimpleNamespace(
        finish_prepared_resource=AsyncMock(
            return_value={"status": "error", "errors": ["post-processing failed"]}
        )
    )
    service._cleanup_reserved_target_if_empty = AsyncMock(return_value=True)
    lock = {"lease_ref": "lock-1"}
    ctx = _ctx()
    msg = _post_process_msg(cleanup_on_failure)

    result = await service.execute_add_resource_job(
        msg,
        ctx=ctx,
        resource_lock=lock,
        stage_callback=AsyncMock(),
    )

    assert result["status"] == "error"
    if cleanup_on_failure:
        service._cleanup_reserved_target_if_empty.assert_awaited_once_with(
            root_uri="viking://resources/report",
            ctx=ctx,
            resource_lock=lock,
        )
    else:
        service._cleanup_reserved_target_if_empty.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_process_job_exception_rolls_back_owned_reservation():
    service = _service(SimpleNamespace())
    service._resource_processor = SimpleNamespace(
        finish_prepared_resource=AsyncMock(side_effect=RuntimeError("summarize crashed"))
    )
    service._cleanup_reserved_target_if_empty = AsyncMock(return_value=True)
    lock = {"lease_ref": "lock-1"}
    ctx = _ctx()
    msg = _post_process_msg(True)

    with pytest.raises(RuntimeError, match="summarize crashed"):
        await service.execute_add_resource_job(
            msg,
            ctx=ctx,
            resource_lock=lock,
            stage_callback=AsyncMock(),
        )

    service._cleanup_reserved_target_if_empty.assert_awaited_once_with(
        root_uri="viking://resources/report",
        ctx=ctx,
        resource_lock=lock,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target_preexisting", "expected_flag"),
    [(False, True), (True, False)],
)
async def test_deferred_post_process_message_carries_target_ownership(
    target_preexisting,
    expected_flag,
):
    service = _service(SimpleNamespace())
    prepared = {
        "root_uri": "viking://resources/report",
        "temp_uri": "viking://temp/account-1/task-3",
        "temp_dir_path": "viking://temp/account-1/task-3",
        "source_committed": True,
        "target_preexisting": target_preexisting,
        "root_is_file": False,
    }
    service._resource_processor = SimpleNamespace(
        process_resource=AsyncMock(
            return_value={
                "status": "success",
                "root_uri": "viking://resources/report",
                "source_path": "report.pdf",
                "_post_process": prepared,
                "_resource_lock": None,
            }
        )
    )
    service._manage_watch_if_needed = AsyncMock()
    service._enqueue_add_resource_job = AsyncMock(
        return_value=SimpleNamespace(task_id="task-3")
    )

    result = await service._execute_resource_ingestion(
        "report.pdf",
        ctx=_ctx(),
        defer_post_processing=True,
        to="viking://resources/report",
    )

    assert result["task_id"] == "task-3"
    enqueued_msg = service._enqueue_add_resource_job.await_args.args[0]
    assert enqueued_msg.job_phase.value == "post_process"
    assert enqueued_msg.cleanup_empty_target_on_failure is expected_flag
