# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Original JSON output protocol for memory extraction."""

from __future__ import annotations

import json
from typing import Any

from openviking.session.memory.extraction_output_protocol import prompt_fragments as prompts
from openviking.session.memory.extraction_output_protocol.base import (
    ExtractionOutputContext,
    ExtractionOutputProtocol,
)
from openviking.session.memory.tools import add_tool_call_pair_to_messages
from openviking.session.memory.utils import parse_json_with_stability


class JsonExtractionOutputProtocol(ExtractionOutputProtocol):
    """Preserve the existing JSON prompt and tolerant parsing behavior."""

    name = "json"

    def render_tool_result_messages(
        self,
        context: ExtractionOutputContext,
        *,
        call_id: str | int,
        tool_name: str,
        params: dict[str, Any],
        result: Any,
        source: str,
    ) -> list[dict[str, Any]]:
        del context, source
        messages: list[dict[str, Any]] = []
        add_tool_call_pair_to_messages(
            messages,
            call_id=call_id,
            tool_name=tool_name,
            params=params,
            result=result,
        )
        return messages

    def render_contract(self, context: ExtractionOutputContext) -> str:
        schema = json.dumps(context.operations_model.model_json_schema(), ensure_ascii=False)
        return (
            "## Output Format\n"
            "The final output of the model must strictly follow the JSON Schema format shown below:\n"
            f"```json\n{schema}\n```"
        )

    def render_reference_rules(self, context: ExtractionOutputContext) -> str:
        link_rules = prompts.JSON_LINK_RULES if context.link_enabled else ""
        if context.add_only:
            return "".join((prompts.JSON_ADD_ONLY_REFERENCE_RULES, link_rules))
        removal_rule = (
            prompts.JSON_PATCH_REMOVAL_RULE
            if context.supports_string_patch
            else prompts.JSON_FULL_REMOVAL_RULE
        )
        return "".join((prompts.JSON_UPSERT_REFERENCE_RULES, removal_rule, link_rules))

    def parse(
        self, content: str, context: ExtractionOutputContext
    ) -> tuple[Any | None, str | None]:
        return parse_json_with_stability(
            content=content,
            model_class=context.operations_model,
            expected_fields=list(context.operations_model.model_fields),
        )

    def render_final_instruction(self, context: ExtractionOutputContext) -> str:
        fields = list(context.operations_model.model_fields)
        if not context.add_only:
            fields = ["delete_ids", *fields]
        skeleton = json.dumps(
            {name: [] for name in dict.fromkeys(fields)},
            ensure_ascii=False,
            indent=2,
        )
        return (
            "You have reached the maximum number of tool call iterations. "
            "Do not call any more tools. Return your final result now as ONLY a valid JSON object "
            "matching the required schema. Do not include explanations or markdown. "
            "If there are no memory changes, return this exact empty-shape JSON with all fields present:\n"
            f"{skeleton}"
        )

    def render_format_retry(self, error: str | None = None) -> str:
        del error
        return (
            "Your previous output could not be parsed as valid JSON. "
            "Please output ONLY a valid JSON object matching the required schema. "
            "Do not include any explanation, markdown formatting, or text outside the JSON."
        )

    def render_patch_repair(self, patch_errors: list[dict[str, Any]]) -> str:
        details = json.dumps(patch_errors, ensure_ascii=False, indent=2)
        return (
            "The SEARCH/REPLACE or DELETE patch could not be applied to the target memory file. "
            "The SEARCH or DELETE text must be copied exactly from the read result of the file bound to that operation's page_id. "
            "The matched text must occur exactly once in the target file. "
            "If it occurs more than once, include enough contiguous surrounding context to make it unique. "
            "Do not use match text from the conversation or from another page. "
            "If you copy from numbered read output, exclude the `line_number<TAB>` prefix from SEARCH, REPLACE, and DELETE text. "
            "If found_in_other_uris is non-empty, diagnose this as a possible page_id mismatch and choose the correct target page_id or rewrite the patch for the current page_id; do not silently move the patch. "
            "Regenerate the complete operations JSON, including previous successful operations and fixed failed operations. "
            "Output ONLY the complete JSON object matching the required schema.\n\n"
            f"Failed patch operations:\n{details}"
        )

    def render_resolution_repair(self, issues: list[dict[str, Any]]) -> str:
        details = json.dumps(issues, ensure_ascii=False, indent=2)
        return (
            "Some event operations could not resolve a safe write target. "
            "Return only corrected event operations for the failed items below; leave every "
            "other memory-type field, delete_ids, and links empty. The server has preserved "
            "all successful operations from the previous response. "
            "Reuse exactly the page_id shown for each failed event. "
            "For event ranges, use valid in-bounds message indexes and include the user-role "
            "message that establishes the event so its owner can be resolved. "
            "Do not target a disallowed or ambiguous peer. Output ONLY one JSON object matching "
            "the required schema.\n\n"
            f"Resolution issues:\n{details}"
        )
