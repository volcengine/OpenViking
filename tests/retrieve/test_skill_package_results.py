# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Skill package grouping, original hit preservation and retrieval completeness."""

import pytest

from openviking.retrieve.skill_package_retriever import SkillPackageRetriever
from openviking.retrieve.skill_results import (
    SkillResultResolver,
    package_abstract,
    skill_root_uri,
)
from openviking.server.identity import RequestContext, Role
from openviking_cli.exceptions import NotFoundError, PermissionDeniedError
from openviking_cli.retrieve.types import (
    ContextType,
    TypedQuery,
)
from openviking_cli.session.user_id import UserIdentifier

SKILLS = "viking://agent/skills"


def ctx():
    return RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER)


def row(uri, score, level=2, abstract="file summary", kind="skill"):
    return {"uri": uri, "_score": score, "level": level, "abstract": abstract, "context_type": kind}


class Files:
    def __init__(self, denied=(), missing=()):
        self.denied = set(denied)
        self.missing = set(missing)
        self.stat_calls = []

    async def stat(self, uri, ctx=None, skip_count=False):
        assert skip_count
        self.stat_calls.append(uri)
        if uri in self.denied:
            raise PermissionDeniedError("root denied", resource=uri)
        if uri in self.missing:
            raise NotFoundError(uri)
        return {"isDir": True}

    async def abstract(self, *args, **kwargs):
        pytest.fail("Skill grouping must not fetch or substitute root metadata")

    async def read(self, *args, **kwargs):
        pytest.fail("Skill grouping must not build an additional content preview")


class PagedStore:
    collection_name = "context"

    def __init__(self, records=(), children=None):
        self.records = list(records)
        self.children = children or {}
        self.calls = []
        self.child_calls = []

    def _acl_enabled(self, ctx):
        return False

    async def collection_exists_bound(self):
        return True

    def _page(self, records, kwargs):
        levels = kwargs.get("level")
        targets = kwargs.get("target_directories")
        filtered = [
            dict(record)
            for record in records
            if (not kwargs.get("context_type") or record["context_type"] == kwargs["context_type"])
            and (levels is None or record["level"] in levels)
            and (
                not targets
                or any(
                    record["uri"] == root or record["uri"].startswith(root + "/")
                    for root in targets
                )
            )
        ]
        filtered.sort(key=lambda item: item["_score"], reverse=True)
        offset = kwargs.get("offset", 0)
        return filtered[offset : offset + kwargs.get("limit", 10)]

    async def search_in_tenant(self, ctx, **kwargs):
        self.calls.append(kwargs)
        return self._page(self.records, kwargs)

    async def filter_in_tenant(self, ctx, **kwargs):
        self.calls.append(kwargs)
        return self._page(self.records, kwargs)

    async def search_children_in_tenant(self, ctx, parent_uri, **kwargs):
        self.child_calls.append({"parent_uri": parent_uri, **kwargs})
        return self._page(self.children.get(parent_uri, []), kwargs)


def query(target=SKILLS):
    return TypedQuery("backup recovery", ContextType.SKILL, "", target_directories=[target])


@pytest.mark.asyncio
async def test_quick_fills_distinct_skills_beyond_fixed_overfetch():
    records = [row(SKILLS, 1, 0), row(f"{SKILLS}/.a.update-backup-123/SKILL.md", 1)]
    records.extend(row(f"{SKILLS}/a/ref/{i}.md", 0.99 - i / 1000) for i in range(120))
    records.append(row(f"{SKILLS}/b/ref/backup.md", 0.5))
    store = PagedStore(records)
    files = Files()
    result = await SkillPackageRetriever(store, None).retrieve_skills(
        query(),
        ctx(),
        limit=2,
        skill_resolver=SkillResultResolver(files, ctx()),
    )
    assert [skill_root_uri(item.uri) for item in result.matched_contexts] == [
        f"{SKILLS}/a",
        f"{SKILLS}/b",
    ]
    assert result.matched_contexts[0].uri == f"{SKILLS}/a/ref/0.md"
    assert result.matched_contexts[0].level == 2
    assert result.matched_contexts[0].abstract == "file summary"
    assert result.matched_contexts[1].score == 0.5
    assert [call["offset"] for call in store.calls] == list(range(0, len(records), 10))
    assert files.stat_calls == [f"{SKILLS}/a", f"{SKILLS}/b"]


@pytest.mark.asyncio
async def test_quick_fills_after_root_denial_and_missing_root():
    records = [row(f"{SKILLS}/denied/ref/{i}.md", 0.99) for i in range(10)]
    records.extend(row(f"{SKILLS}/missing/ref/{i}.md", 0.9, abstract="") for i in range(10))
    records.extend([row(f"{SKILLS}/b/SKILL.md", 0.8), row(f"{SKILLS}/c/SKILL.md", 0.7)])
    files = Files(denied=[f"{SKILLS}/denied"], missing=[f"{SKILLS}/missing"])
    store = PagedStore(records)
    result = await SkillPackageRetriever(store, None).retrieve_skills(
        query(),
        ctx(),
        limit=2,
        skill_resolver=SkillResultResolver(files, ctx()),
    )
    assert [item.uri for item in result.matched_contexts] == [
        f"{SKILLS}/b/SKILL.md",
        f"{SKILLS}/c/SKILL.md",
    ]
    assert [call["offset"] for call in store.calls] == [0, 10, 20]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("level", "relative_uri", "score", "abstract"),
    [
        (0, "reference/.abstract.md", 0.92, "directory abstract"),
        (1, "reference/.overview.md", 0.95, "directory overview"),
        (2, "reference/SKILL.md", 0.8, "nested definition"),
    ],
)
async def test_scoped_hits_preserve_requested_level_and_do_not_use_outside_scores(
    level, relative_uri, score, abstract
):
    root = f"{SKILLS}/demo"
    store = PagedStore(
        [
            row(root, 1, 0, "name: wrong"),
            row(f"{root}/other.md", 0.99),
            row(f"{root}/reference", 0.92, 0, "directory abstract"),
            row(f"{root}/reference", 0.95, 1, "directory overview"),
            row(f"{root}/reference/SKILL.md", 0.8, abstract="nested definition"),
        ]
    )
    filters = {"op": "must", "field": "search_tags", "conds": ["team=infra"]}
    result = await SkillPackageRetriever(store, None).retrieve_skills(
        query(f"{root}/reference"),
        ctx(),
        limit=2,
        level=[level],
        scope_dsl=filters,
        skill_resolver=SkillResultResolver(Files(), ctx()),
    )
    [matched] = result.matched_contexts
    assert matched.uri == f"{root}/{relative_uri}"
    assert matched.level == level
    assert matched.score == score
    assert matched.abstract == abstract
    assert store.calls[0]["extra_filter"] is filters


class _AbstractFs:
    """Only the accessor `package_abstract` uses, so a stray read is a failure."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    async def abstract(self, uri, ctx=None):
        self.calls.append((uri, ctx))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("name: demo\ndescription: do things", "name: demo\ndescription: do things"),
        (f"# {SKILLS}/demo [Directory abstract is not ready]", ""),
        ("", ""),
        (NotFoundError(f"{SKILLS}/demo"), ""),
        (PermissionDeniedError("denied", resource=f"{SKILLS}/demo"), ""),
    ],
)
async def test_package_abstract_reports_only_a_generated_abstract(answer, expected):
    fs = _AbstractFs(answer)
    request_ctx = ctx()

    assert await package_abstract(fs, request_ctx, f"{SKILLS}/demo") == expected
    assert fs.calls == [(f"{SKILLS}/demo", request_ctx)]
