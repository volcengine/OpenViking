# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from unittest.mock import patch

from openviking.session.memory.page_id_map import PageIdMap


class TestPageIdMap:
    def test_get_page_id_assigns_incremental_ids_for_existing_pages(self):
        pim = PageIdMap()

        id1 = pim.get_page_id("viking://user/a/memories/profile.md")
        id2 = pim.get_page_id("viking://user/a/memories/preferences/topic.md")

        assert id1 == 1
        assert id2 == 2

    def test_get_page_id_returns_same_id_for_duplicate_uri(self):
        pim = PageIdMap()

        id1 = pim.get_page_id("viking://user/a/memories/profile.md")
        id2 = pim.get_page_id("viking://user/a/memories/profile.md")

        assert id1 == id2

    def test_resolve_returns_uri_for_registered_existing_page(self):
        pim = PageIdMap()

        page_id = pim.get_page_id("viking://user/a/memories/profile.md")

        assert pim.resolve(page_id) == "viking://user/a/memories/profile.md"

    def test_resolve_returns_none_for_unknown_page_id(self):
        pim = PageIdMap()

        assert pim.resolve(999) is None

    def test_register_new_page_id_resolves_uri_for_unseen_uri(self):
        pim = PageIdMap()

        pim.register_new_page_id("viking://new-item", 105)

        assert pim.resolve(105) == "viking://new-item"

    def test_get_page_id_returns_declared_page_id_for_unseen_registered_uri(self):
        pim = PageIdMap()

        pim.register_new_page_id("viking://new-item", 105)

        assert pim.get_page_id("viking://new-item") == 105

    def test_register_new_page_id_preserves_existing_primary_id_for_seen_uri(self):
        pim = PageIdMap()

        existing_id = pim.get_page_id("viking://profile.md")
        pim.register_new_page_id("viking://profile.md", 100)

        assert pim.get_page_id("viking://profile.md") == existing_id
        assert pim.resolve(existing_id) == "viking://profile.md"
        assert pim.resolve(100) == "viking://profile.md"

    def test_existing_page_namespace_stops_at_99_without_registering_overflow(self):
        pim = PageIdMap()
        uris = [f"viking://existing/{index}.md" for index in range(1, 101)]

        assigned = [pim.get_page_id(uri) for uri in uris]

        assert assigned[:99] == list(range(1, 100))
        assert assigned[99] is None
        assert pim.resolve(100) is None
        assert uris[99] not in pim._uri_to_id

    def test_existing_page_duplicate_keeps_id_after_namespace_is_full(self):
        pim = PageIdMap()
        uris = [f"viking://existing/{index}.md" for index in range(1, 100)]
        for uri in uris:
            pim.get_page_id(uri)

        assert pim.get_page_id(uris[-1]) == 99
        assert pim.get_page_id("viking://existing/overflow.md") is None
        assert pim.get_page_id(uris[0]) == 1

    def test_existing_page_namespace_overflow_warns_only_once(self):
        pim = PageIdMap()
        for index in range(1, 100):
            pim.get_page_id(f"viking://existing/{index}.md")

        with patch("openviking.session.memory.page_id_map.logger.warning") as warning:
            assert pim.get_page_id("viking://existing/overflow-a.md") is None
            assert pim.get_page_id("viking://existing/overflow-b.md") is None

        warning.assert_called_once()
