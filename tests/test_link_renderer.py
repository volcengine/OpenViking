import pytest

from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.utils.link_renderer import LinkRenderer
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils


class TestRelativePath:
    def test_same_directory(self):
        result = LinkRenderer.relative_path(
            "viking://user/Caroline/memories/profile.md",
            "viking://user/Caroline/memories/identity.md",
        )
        assert result == "identity.md"

    def test_target_in_subdirectory(self):
        result = LinkRenderer.relative_path(
            "viking://user/Caroline/memories/profile.md",
            "viking://user/Caroline/memories/events/2023/08/17/pride.md",
        )
        assert result == "events/2023/08/17/pride.md"

    def test_source_in_subdirectory(self):
        result = LinkRenderer.relative_path(
            "viking://user/Caroline/memories/events/2023/08/17/pride.md",
            "viking://user/Caroline/memories/profile.md",
        )
        assert result == "../../../../profile.md"

    def test_cross_subdirectory(self):
        result = LinkRenderer.relative_path(
            "viking://user/Caroline/memories/events/2023/08/17/pride.md",
            "viking://user/Caroline/memories/entities/people/alice.md",
        )
        assert result == "../../../../entities/people/alice.md"

    def test_cross_scope_returns_none(self):
        result = LinkRenderer.relative_path(
            "viking://user/Caroline/memories/profile.md",
            "viking://agent/Bot/memories/skills/pdf.md",
        )
        assert result is None

    def test_different_user_returns_none(self):
        result = LinkRenderer.relative_path(
            "viking://user/Caroline/memories/profile.md",
            "viking://user/Melanie/memories/profile.md",
        )
        assert result is None

    def test_different_user_same_scope_prefix(self):
        # "user" matches, but "Caroline" != "Melanie" so common < 2
        result = LinkRenderer.relative_path(
            "viking://user/Caroline/memories/profile.md",
            "viking://user/Melanie/memories/events/2023/pride.md",
        )
        assert result is None

    def test_same_file_returns_empty(self):
        result = LinkRenderer.relative_path(
            "viking://user/Caroline/memories/profile.md",
            "viking://user/Caroline/memories/profile.md",
        )
        # Same file: common = all segments, up=0, down=empty -> empty string
        assert result == ""


class TestRenderLinks:
    def test_single_link(self):
        content = "Caroline attended a support group meeting."
        links = [
            {
                "from_uri": "viking://user/Caroline/memories/profile.md",
                "to_uri": "viking://user/Caroline/memories/entities/groups/lgbtq_support_group.md",
                "weight": 1.0,
                "match_text": "support",
            }
        ]
        result = LinkRenderer.render_links(
            content,
            "viking://user/Caroline/memories/profile.md",
            links,
        )
        assert (
            result
            == "Caroline attended a [support](entities/groups/lgbtq_support_group.md) group meeting."
        )

    def test_case_insensitive_match(self):
        content = "Caroline attended a Support group meeting."
        links = [
            {
                "from_uri": "viking://user/Caroline/memories/profile.md",
                "to_uri": "viking://user/Caroline/memories/entities/groups/lgbtq_support_group.md",
                "weight": 1.0,
                "match_text": "support",
            }
        ]
        result = LinkRenderer.render_links(
            content,
            "viking://user/Caroline/memories/profile.md",
            links,
        )
        assert (
            result
            == "Caroline attended a [Support](entities/groups/lgbtq_support_group.md) group meeting."
        )

    def test_word_boundary_no_substring_match(self):
        content = "She is a car enthusiast."
        links = [
            {
                "from_uri": "viking://user/Caroline/memories/profile.md",
                "to_uri": "viking://user/Caroline/memories/entities/vehicles/car.md",
                "weight": 1.0,
                "match_text": "car",
            }
        ]
        result = LinkRenderer.render_links(
            content,
            "viking://user/Caroline/memories/profile.md",
            links,
        )
        assert result == "She is a [car](entities/vehicles/car.md) enthusiast."

    def test_word_boundary_no_match_inside_word(self):
        content = "Caroline went to the store."
        links = [
            {
                "from_uri": "viking://user/Caroline/memories/profile.md",
                "to_uri": "viking://user/Caroline/memories/entities/vehicles/car.md",
                "weight": 1.0,
                "match_text": "car",
            }
        ]
        result = LinkRenderer.render_links(
            content,
            "viking://user/Caroline/memories/profile.md",
            links,
        )
        assert result == "Caroline went to the store."

    def test_ascii_boundary_matches_when_surrounded_by_non_ascii_word_chars(self):
        content = "(car), car."
        links = [
            {
                "from_uri": "viking://user/Caroline/memories/profile.md",
                "to_uri": "viking://user/Caroline/memories/entities/vehicles/car.md",
                "weight": 1.0,
                "match_text": "car",
            }
        ]
        result = LinkRenderer.render_links(
            content,
            "viking://user/Caroline/memories/profile.md",
            links,
        )
        assert result == "([car](entities/vehicles/car.md)), car."

    def test_ascii_boundary_matches_next_to_cjk_text(self):
        content = "她喜欢car，也喜欢旅行。"
        links = [
            {
                "from_uri": "viking://user/Caroline/memories/profile.md",
                "to_uri": "viking://user/Caroline/memories/entities/vehicles/car.md",
                "weight": 1.0,
                "match_text": "car",
            }
        ]
        result = LinkRenderer.render_links(
            content,
            "viking://user/Caroline/memories/profile.md",
            links,
        )
        assert result == "她喜欢[car](entities/vehicles/car.md)，也喜欢旅行。"

    def test_no_match_text_skipped(self):
        content = "Some content here."
        links = [
            {
                "from_uri": "viking://user/Caroline/memories/profile.md",
                "to_uri": "viking://user/Caroline/memories/entities/foo.md",
                "weight": 1.0,
                "match_text": None,
            }
        ]
        result = LinkRenderer.render_links(
            content,
            "viking://user/Caroline/memories/profile.md",
            links,
        )
        assert result == "Some content here."

    def test_self_link_skipped(self):
        content = "This is my profile."
        links = [
            {
                "from_uri": "viking://user/Caroline/memories/profile.md",
                "to_uri": "viking://user/Caroline/memories/profile.md",
                "weight": 1.0,
                "match_text": "profile",
            }
        ]
        result = LinkRenderer.render_links(
            content,
            "viking://user/Caroline/memories/profile.md",
            links,
        )
        assert result == "This is my profile."

    def test_backlink_not_rendered(self):
        # Backlinks (where to_uri == source_uri) are not rendered
        content = "The painting features nice colors."
        links = [
            {
                "from_uri": "viking://user/Melanie/memories/entities/art/lake_sunrise.md",
                "to_uri": "viking://user/Melanie/memories/preferences/creative.md",
                "weight": 1.0,
                "match_text": "painting",
            }
        ]
        result = LinkRenderer.render_links(
            content,
            "viking://user/Melanie/memories/preferences/creative.md",
            links,
        )
        # to_uri == source_uri, so this is a self-link, skipped
        assert result == "The painting features nice colors."

    def test_cross_scope_fallback_to_full_uri(self):
        content = "The agent has a useful skill."
        links = [
            {
                "from_uri": "viking://user/Caroline/memories/profile.md",
                "to_uri": "viking://agent/Bot/memories/skills/research.md",
                "weight": 1.0,
                "match_text": "skill",
            }
        ]
        result = LinkRenderer.render_links(
            content,
            "viking://user/Caroline/memories/profile.md",
            links,
        )
        assert "viking://agent/Bot/memories/skills/research.md" in result

    def test_weight_priority(self):
        content = "She loves painting and painting is fun."
        links = [
            {
                "from_uri": "viking://user/Melanie/memories/profile.md",
                "to_uri": "viking://user/Melanie/memories/preferences/art.md",
                "weight": 0.5,
                "match_text": "painting",
            },
            {
                "from_uri": "viking://user/Melanie/memories/profile.md",
                "to_uri": "viking://user/Melanie/memories/entities/art/lake_sunrise.md",
                "weight": 1.0,
                "match_text": "painting",
            },
        ]
        result = LinkRenderer.render_links(
            content,
            "viking://user/Melanie/memories/profile.md",
            links,
        )
        # Higher weight wins, only first occurrence replaced
        assert result == "She loves [painting](entities/art/lake_sunrise.md) and painting is fun."

    def test_no_links_returns_unchanged(self):
        content = "Plain text without links."
        result = LinkRenderer.render_links(
            content,
            "viking://user/Caroline/memories/profile.md",
            [],
        )
        assert result == content

    def test_chinese_match_without_word_boundaries(self):
        content = "她喜欢角色扮演游戏，也喜欢开放世界游戏。"
        links = [
            {
                "from_uri": "viking://user/Caroline/memories/profile.md",
                "to_uri": "viking://user/Caroline/memories/entities/games/rpg.md",
                "weight": 1.0,
                "match_text": "角色扮演游戏",
            }
        ]
        result = LinkRenderer.render_links(
            content,
            "viking://user/Caroline/memories/profile.md",
            links,
        )
        assert (
            result
            == "她喜欢[角色扮演游戏](entities/games/rpg.md)，也喜欢开放世界游戏。"
        )


class TestStripLinks:
    def test_strip_relative_link(self):
        content = "See [support](../entities/groups/lgbtq_support_group.md) for details."
        result = LinkRenderer.strip_links(content)
        assert result == "See support for details."

    def test_keep_absolute_link(self):
        content = "Visit [docs](https://example.com/docs) for more."
        result = LinkRenderer.strip_links(content)
        assert result == content

    def test_keep_viking_uri_link(self):
        content = "Check [skill](viking://agent/Bot/memories/skills/research.md)."
        result = LinkRenderer.strip_links(content)
        assert result == content

    def test_keep_viking_uri_link_when_target_uses_supported_scheme(self):
        content = "Check [skill](viking://agent/Bot/memories/skills/research.md)."
        result = LinkRenderer.strip_links(content)
        assert result == content

    def test_keep_anchor_link(self):
        content = "Jump to [section](#intro)."
        result = LinkRenderer.strip_links(content)
        assert result == content

    def test_keep_absolute_path_link(self):
        content = "See [file](/absolute/path.md)."
        result = LinkRenderer.strip_links(content)
        assert result == content

    def test_multiple_links(self):
        content = "[support](../groups/support.md) and [art](../entities/art.md)"
        result = LinkRenderer.strip_links(content)
        assert result == "support and art"

    def test_mixed_links(self):
        content = "[local](../foo.md) and [web](https://example.com)"
        result = LinkRenderer.strip_links(content)
        assert result == "local and [web](https://example.com)"

    def test_no_links(self):
        content = "Just plain text."
        result = LinkRenderer.strip_links(content)
        assert result == content


class TestRoundTrip:
    def test_render_then_strip(self):
        original = "Caroline attended a support group meeting."
        links = [
            {
                "from_uri": "viking://user/Caroline/memories/profile.md",
                "to_uri": "viking://user/Caroline/memories/entities/groups/lgbtq_support_group.md",
                "weight": 1.0,
                "match_text": "support",
            }
        ]
        rendered = LinkRenderer.render_links(
            original,
            "viking://user/Caroline/memories/profile.md",
            links,
        )
        stripped = LinkRenderer.strip_links(rendered)
        assert stripped == original

    def test_render_then_strip_multiple(self):
        original = "She enjoys painting and swimming."
        links = [
            {
                "from_uri": "viking://user/Melanie/memories/profile.md",
                "to_uri": "viking://user/Melanie/memories/entities/art/lake_sunrise.md",
                "weight": 1.0,
                "match_text": "painting",
            },
            {
                "from_uri": "viking://user/Melanie/memories/profile.md",
                "to_uri": "viking://user/Melanie/memories/events/2023/08/swimming.md",
                "weight": 0.8,
                "match_text": "swimming",
            },
        ]
        rendered = LinkRenderer.render_links(
            original,
            "viking://user/Melanie/memories/profile.md",
            links,
        )
        stripped = LinkRenderer.strip_links(rendered)
        assert stripped == original

    def test_memory_file_plain_content_strips_markdown_links(self):
        memory_file = MemoryFile(
            uri="viking://user/Calvin/memories/events/2023/08/22/collab_with_frank_ocean.md",
            content="Worked with [Frank Ocean](../../../../entities/personal/calvin.md).",
        )

        assert memory_file.plain_content() == "Worked with Frank Ocean."

    def test_memory_file_utils_write_keeps_plain_text_body_and_preserves_links_metadata(self):
        memory_file = MemoryFile(
            uri="viking://user/Caroline/memories/profile.md",
            content="她喜欢角色扮演游戏，也喜欢开放世界游戏。",
            links=[
                {
                    "from_uri": "viking://user/Caroline/memories/profile.md",
                    "to_uri": "viking://user/Caroline/memories/entities/games/rpg.md",
                    "weight": 1.0,
                    "match_text": "角色扮演游戏",
                }
            ],
            extra_fields={"memory_type": "profile"},
        )

        written = MemoryFileUtils.write(memory_file)

        assert "她喜欢角色扮演游戏，也喜欢开放世界游戏。" in written
        assert "她喜欢[角色扮演游戏](entities/games/rpg.md)，也喜欢开放世界游戏。" not in written
        assert '"match_text": "角色扮演游戏"' in written

    def test_repeated_memory_file_utils_write_does_not_persist_nested_links(self):
        memory_file = MemoryFile(
            uri="viking://user/Gina/memories/profile.md",
            content="Gina",
            links=[
                {
                    "from_uri": "viking://user/Gina/memories/profile.md",
                    "to_uri": "viking://user/Gina/memories/events/2023/02/08/Gina与Jon的日常交流.md",
                    "weight": 1.0,
                    "match_text": "Gina",
                }
            ],
            extra_fields={"memory_type": "profile"},
        )

        first_write = MemoryFileUtils.write(memory_file)
        reparsed = MemoryFileUtils.read(first_write, uri=memory_file.uri)
        second_write = MemoryFileUtils.write(reparsed)

        assert "[Gina](events/2023/02/08/Gina与Jon的日常交流.md)" not in first_write
        assert "[[Gina](events/2023/02/08/Gina与Jon的日常交流.md)](events/2023/02/08/Gina与Jon的日常交流.md)" not in second_write
        assert "Gina" in second_write
