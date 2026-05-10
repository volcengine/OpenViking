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
        returned_id = pim.register_new("viking://new-item", page_id=105)
        assert returned_id == 105
        assert pim.resolve(105) == "viking://new-item"

    def test_register_new_with_declared_page_id_collision(self):
        pim = PageIdMap()
        pim.register_new("viking://first", page_id=100)
        returned_id = pim.register_new("viking://second", page_id=100)
        assert returned_id != 100
        assert returned_id >= 100
        assert pim.resolve(100) == "viking://first"
        assert pim.resolve(returned_id) == "viking://second"

    def test_register_new_declared_page_id_links_resolve_correctly(self):
        pim = PageIdMap()
        existing_id = pim.register_existing("viking://existing-page")
        new_id = pim.register_new("viking://new-item", page_id=100)
        assert pim.resolve(existing_id) == "viking://existing-page"
        assert pim.resolve(100) == "viking://new-item"

    def test_existing_page_alias_with_llm_page_id(self):
        """LLM edits an existing page: URI already registered as page_id=1,
        LLM declares page_id=100. Both IDs should resolve to the same URI."""
        pim = PageIdMap()
        existing_id = pim.register_existing("viking://profile.md")
        assert existing_id == 1

        # LLM edits profile.md, declares page_id=100
        returned_id = pim.register_new("viking://profile.md", page_id=100)
        assert returned_id == 100

        # Both page_id=1 and page_id=100 resolve to the same URI
        assert pim.resolve(1) == "viking://profile.md"
        assert pim.resolve(100) == "viking://profile.md"

    def test_link_from_llm_page_id_to_existing(self):
        """Simulate: LLM declares page_id=100 for an existing page,
        then creates a link with f=100 -> t=1."""
        pim = PageIdMap()
        # Prefetch registers existing pages
        id_a = pim.register_existing("viking://a")
        id_b = pim.register_existing("viking://b")
        # LLM edits page_a, declares page_id=100
        pim.register_new("viking://a", page_id=100)
        # Link from edited page (f=100) to existing page (t=2)
        assert pim.resolve(100) == "viking://a"
        assert pim.resolve(id_b) == "viking://b"

    def test_multi_user_operation_same_page_id(self):
        """Multi-user mode: one operation produces 2 URIs but same page_id.
        Only the first URI gets the LLM-declared page_id; the second is an alias."""
        pim = PageIdMap()
        # LLM creates event with page_id=100, operation resolves to 2 URIs
        pim.register_new("viking://user/Melanie/events/charity_race.md", page_id=100)
        pim.register_alias("viking://user/Caroline/events/charity_race.md", 100)
        # resolve(100) returns the primary URI
        assert pim.resolve(100) == "viking://user/Melanie/events/charity_race.md"
        # get_id works for both URIs
        assert pim.get_id("viking://user/Melanie/events/charity_race.md") == 100
        assert pim.get_id("viking://user/Caroline/events/charity_race.md") == 100

    def test_multi_user_no_page_id_shift(self):
        """Verify that multi-user operations don't shift subsequent page_ids."""
        pim = PageIdMap()
        # First event: page_id=100, 2 URIs
        pim.register_new("viking://user/Melanie/events/event1.md", page_id=100)
        pim.register_alias("viking://user/Caroline/events/event1.md", 100)
        # Second event: page_id=101
        pim.register_new("viking://user/Melanie/events/event2.md", page_id=101)
        # page_id=100 and 101 resolve correctly, no shifting
        assert pim.resolve(100) == "viking://user/Melanie/events/event1.md"
        assert pim.resolve(101) == "viking://user/Melanie/events/event2.md"
