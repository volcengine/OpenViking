import asyncio

import pytest

from scripts.maintenance.vikingdb_content_backfill import (
    backfill_vikingdb_content as backfill,
)


class FailingAgfs:
    def ls(self, _path):
        raise OSError("storage unavailable")


class FailingSource:
    async def tree(self, *_args, **_kwargs):
        raise OSError("tree unavailable")


def test_account_enumeration_surfaces_agfs_listing_failure():
    enumerator = backfill.ContentBackfillEnumerator(FailingAgfs(), object())

    with pytest.raises(RuntimeError, match="failed to list AGFS directory /local") as exc:
        enumerator._list_dir_names("/local")

    assert isinstance(exc.value.__cause__, OSError)


def test_tree_enumeration_surfaces_source_failure():
    enumerator = backfill.ContentBackfillEnumerator(object(), FailingSource())

    async def collect():
        return [
            candidate
            async for candidate in enumerator._iter_tree_candidates(
                account_id="account-1",
                root_uri="viking://",
                ctx=object(),
                node_limit=None,
            )
        ]

    with pytest.raises(
        RuntimeError,
        match="failed to enumerate account account-1 tree at viking://",
    ) as exc:
        asyncio.run(collect())

    assert isinstance(exc.value.__cause__, OSError)


@pytest.mark.parametrize(
    ("flag", "attribute"),
    [
        ("--batch-size", "batch_size"),
        ("--page-size", "page_size"),
        ("--read-concurrency", "read_concurrency"),
        ("--update-sleep-ms", "update_sleep_ms"),
    ],
)
def test_legacy_noop_option_remains_accepted_with_warning(flag, attribute, capsys):
    args = backfill.build_parser().parse_args([flag, "7"])

    options = backfill.build_options(args)

    assert getattr(args, attribute) == 7
    assert not hasattr(options, attribute)
    assert f"warning: {flag} is deprecated and has no effect" in capsys.readouterr().err
