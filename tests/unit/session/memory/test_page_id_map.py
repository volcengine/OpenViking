# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.session.memory.page_id_map import PageIdMap


class TestPageIdMap:
    def test_register_existing_returns_1_to_99(self):
        pim = PageIdMap()
        id1 = pim.register_existing("viking://user/a/memories/profile.md")
        assert id1 == 1
        id2 = pim.register_existing("viking://user/a/memories/preferences/topic.md")
        assert id2 == 2

    def test_register_new_returns_100_plus(self):
        pim = PageIdMap()
        id1 = pim.register_new("viking://user/a/memories/events/2026/01/01/event.md")
        assert id1 == 100
        id2 = pim.register_new("viking://user/a/memories/events/2026/01/02/other.md")
        assert id2 == 101

    def test_duplicate_registration_returns_same_id(self):
        pim = PageIdMap()
        id1 = pim.register_existing("viking://user/a/memories/profile.md")
        id2 = pim.register_existing("viking://user/a/memories/profile.md")
        assert id1 == id2

    def test_resolve_returns_uri(self):
        pim = PageIdMap()
        page_id = pim.register_existing("viking://user/a/memories/profile.md")
        assert pim.resolve(page_id) == "viking://user/a/memories/profile.md"

    def test_resolve_returns_none_for_unknown(self):
        pim = PageIdMap()
        assert pim.resolve(999) is None

    def test_get_id_returns_page_id(self):
        pim = PageIdMap()
        page_id = pim.register_existing("viking://user/a/memories/profile.md")
        assert pim.get_id("viking://user/a/memories/profile.md") == page_id

    def test_get_id_returns_none_for_unknown(self):
        pim = PageIdMap()
        assert pim.get_id("viking://unknown") is None

    def test_existing_and_new_do_not_overlap(self):
        pim = PageIdMap()
        existing_id = pim.register_existing("viking://existing")
        new_id = pim.register_new("viking://new")
        assert existing_id < 100
        assert new_id >= 100

    def test_overflow_raises_error(self):
        pim = PageIdMap()
        with pytest.raises(ValueError, match="Too many existing pages"):
            for i in range(100):
                pim.register_existing(f"viking://file{i}")

    def test_has_links_enabled(self):
        pim = PageIdMap()
        assert not pim.has_links_enabled
        pim.register_existing("viking://test")
        assert pim.has_links_enabled
