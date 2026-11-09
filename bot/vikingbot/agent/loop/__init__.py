"""Agent loop entry points and compatibility imports."""

from openviking.utils.media_limits import MAX_INLINE_TOOL_RESULT_MEDIA_BYTES
from openviking.utils.rule_delivery import MAX_RULE_RESULT_CHARS
from vikingbot.agent.loop.loop import (
    AgentIterationLimitExceeded,
    AgentLoop,
    LangfuseClient,
    SubagentManager,
    _is_tool_result_success,
    _PlainTextContext,
    _PlainTextDelivered,
    _PlainTextFinal,
    render_budget_reminder,
)
from vikingbot.agent.loop.loop_compaction import (
    _base64_data_url_bytes,
    _compact_build_note,
    _compact_msg_chars,
    _compact_render_message,
    _compact_render_transcript,
    _compact_split_chunks,
    _compact_split_into_blocks,
    _compact_strip_note_header,
    _demote_historical_tool_media,
    _inline_media_bytes,
    _is_rule_result,
)
from vikingbot.agent.loop.loop_session import (
    _AcceptedSteer,
    _ActiveTurn,
    _AssistantBoundary,
    _PreparedSteer,
)
