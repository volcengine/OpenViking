# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

from openviking.session.memory.utils.template_utils import TemplateUtils


class TestTemplateUtils:
    def test_render_supports_extract_context(self):
        extract_context = SimpleNamespace(get_year=lambda ranges: "2026")

        rendered = TemplateUtils.render(
            "  {{ title }} {{ extract_context.get_year(ranges) }}  ",
            {"title": "Trip", "ranges": "0-1"},
            extract_context=extract_context,
        )

        assert rendered == "Trip 2026"

    def test_find_missing_variables_ignores_extract_context(self):
        missing = TemplateUtils.find_missing_variables(
            "{{ missing_field }} {{ extract_context.get_year(ranges) }} {{ content }}",
            {"content": "Trip summary", "ranges": "0-1"},
        )

        assert missing == {"missing_field"}

    def test_render_plain_text_without_template_syntax(self):
        rendered = TemplateUtils.render("plain text", {"unused": "value"})

        assert rendered == "plain text"
