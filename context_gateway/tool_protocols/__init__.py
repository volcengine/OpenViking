# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Native protocol adapters: what the gateway knows about each wire protocol."""

from dataclasses import dataclass, field

from ..records import RecordKind as K
from .anthropic import AnthropicProtocol
from .chat import ChatProtocol
from .common import ToolProtocol
from .responses import ResponsesProtocol

PROTOCOLS = {"chat": ChatProtocol, "responses": ResponsesProtocol, "anthropic": AnthropicProtocol}


def tool_protocol(protocol: str) -> type[ToolProtocol]:
    return PROTOCOLS[protocol]


@dataclass
class ResponseCapture:
    """The reply relayed to the client, as bookkeeping reads it.

    ``nonstream`` and ``event`` feed the protocol adapter's assembler, the one the
    hidden tool loop uses, so every path reads replies and their completion alike.
    """

    protocol: str
    message: dict | None = None
    usage: dict | None = None
    response_id: str = ""
    # The reply ended the user's turn.
    complete: bool = False
    # The reply ended in calls to the client's tools; the turn goes on in the next request.
    handoff: bool = False
    output_items: list | None = None
    context_usage: dict | None = None
    assembler: ToolProtocol | None = field(default=None, repr=False, compare=False)

    @property
    def finished(self):
        """The whole reply arrived, whether it ended the turn or handed it to client tools."""
        return self.complete or self.handoff

    @property
    def output(self) -> list[dict]:
        """The reply as native history items, as the client's next request resends it.

        The recall notice a reply starts with is the gateway's: the next request strips
        it before anything is matched, so every reader of the reply goes without it too.
        """
        output = self.output_items or ([self.message] if self.message else [])
        return tool_protocol(self.protocol).strip_lead(output)

    def nonstream(self, body):
        adapter = tool_protocol(self.protocol)({})
        adapter.begin()
        adapter.load(body)
        vars(self).update(adapter.reply())

    def event(self, body):
        if self.assembler is None:
            self.assembler = tool_protocol(self.protocol)({})
            self.assembler.begin()
        self.assembler.accumulate(body)
        vars(self).update(self.assembler.reply())


def hidden_chain(messages: list[dict], protocol: str) -> list[str]:
    return tool_protocol(protocol).history_chain(messages)


def replay_hidden(
    messages: list[dict], chain: list[str], records: dict, protocol: str
) -> list[dict]:
    """Expand each matching visible span into its immutable native transcript."""
    result, offsets = [], []
    for index, (message, anchor) in enumerate(zip(messages, chain, strict=True)):
        offsets.append(len(result))
        result.append(message)
        record = records.get((K.HIDDEN, anchor))
        if record:
            count = record["visible_count"]
            if 0 < count <= index + 1:
                result[offsets[index + 1 - count] :] = record["messages"]
    return tool_protocol(protocol).join_replayed_history(result)
