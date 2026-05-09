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

    def test_register_new_with_declared_page_id(self):
        pim = PageIdMap()
        # LLM declares page_id=105 for a new item
        returned_id = pim.register_new("viking://new-item", page_id=105)
        assert returned_id == 105
        assert pim.resolve(105) == "viking://new-item"

    def test_register_new_with_declared_page_id_collision(self):
        pim = PageIdMap()
        # First registration with page_id=100
        pim.register_new("viking://first", page_id=100)
        # Second registration with same page_id=100 but different URI -> auto-assign
        returned_id = pim.register_new("viking://second", page_id=100)
        assert returned_id != 100  # Should get a different ID
        assert returned_id >= 100
        # Both should be resolvable
        assert pim.resolve(100) == "viking://first"
        assert pim.resolve(returned_id) == "viking://second"

    def test_register_new_declared_page_id_links_resolve_correctly(self):
        """Simulate the full flow: LLM outputs page_id=100, link uses f=100."""
        pim = PageIdMap()
        # Register an existing page
        existing_id = pim.register_existing("viking://existing-page")
        # LLM creates a new item with page_id=100
        new_id = pim.register_new("viking://new-item", page_id=100)
        # Link from existing page to new page should resolve correctly
        assert pim.resolve(existing_id) == "viking://existing-page"
        assert pim.resolve(100) == "viking://new-item"
