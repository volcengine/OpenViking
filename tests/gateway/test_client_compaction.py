"""Client compaction as Claude Code and Codex send it, from requests recorded through a proxy."""

import copy
import json

import pytest
from test_capture_delivery import sent

from openviking_gateway.tool_protocols import ResponseCapture

ANTHROPIC_TOOLS = [{"name": "Read", "description": "Read a file", "input_schema": {}}]


class Client:
    """One client session, sending whole requests and receiving replies through the kernel."""

    def __init__(self, setup, credential, policy, protocol, headers):
        self.kernel, _, self.viking, _ = setup
        self.credential, self.policy, self.protocol = credential, policy, protocol
        self.headers = headers

    async def send(self, messages, reply, headers=None):
        responses = self.protocol == "responses"
        body = {"model": "model", "input" if responses else "messages": copy.deepcopy(messages)}
        # Codex lists its tools in an input item instead.
        body.update({"store": False} if responses else {"tools": ANTHROPIC_TOOLS})
        headers = {**self.headers, **(headers or {})}
        request = await self.kernel.prepare(
            body, self.protocol, headers, self.credential, {"id": "upstream"}, self.policy
        )
        items = reply if isinstance(reply, list) else [reply]
        output = items if responses else None
        # A reply with tool calls hands the turn to the client; any other ends it.
        handoff = any(k in str(items) for k in ("tool_use", "tool_call"))
        response = ResponseCapture(
            self.protocol, items[-1], complete=not handoff, handoff=handoff, output_items=output
        )
        await self.kernel.completed(request, self.credential, response)
        await self.kernel.save(request, self.credential, response)

    def saved(self):
        """What each OpenViking session received: texts and tool call IDs, in order."""
        return list(sent(self.viking).values())


@pytest.fixture
async def client(setup_kernel, credential, policy):
    policy.update(recall=False, commit_tokens=1000000)
    return lambda protocol, headers: Client(setup_kernel, credential, policy, protocol, headers)


# Claude Code 2.1.323, recorded: role "system" items sit between messages and turn from a
# text block into a string on the next request; user messages carry reminder blocks.


def text(value):
    return {"type": "text", "text": value}


def reminder(value):
    return text(f"<system-reminder>\n{value}\n</system-reminder>")


def user(*blocks):
    return {"role": "user", "content": [text(b) if isinstance(b, str) else b for b in blocks]}


def system(value, block=False):
    return {"role": "system", "content": [text(value)] if block else value}


def assistant(*blocks):
    return {"role": "assistant", "content": [text(b) if isinstance(b, str) else b for b in blocks]}


def thinking(name):
    return {"type": "thinking", "thinking": f"Thinking about {name}", "signature": f"sig-{name}"}


def tool_use(name):
    return {"type": "tool_use", "id": name, "name": "Read", "input": {"file_path": name}}


def tool_result(name, *blocks):
    return user(
        {"type": "tool_result", "tool_use_id": name, "content": f"CODEWORD {name}"}, *blocks
    )


def step(name):
    return assistant(thinking(name), tool_use(name))


REMIND = reminder("As you answer the user's questions, you can use the following context")
ATTRIBUTION = reminder("Attribution for git commits")
PROMPT = "Read the files f1.txt through f3.txt and report their CODEWORD lines"
FIRST = user(REMIND, ATTRIBUTION, PROMPT)
ENV = "# Environment\nYou have been invoked in the following environment"
CRITICAL = "CRITICAL: Respond with TEXT ONLY. Do NOT call any tools."
SUMMARY = (
    "This session is being continued from a previous conversation that ran out of context."
    " The summary below covers the earlier portion of the conversation."
)
SUMMARIZED = assistant("<analysis>Files read so far.</analysis>\n<summary>f1, f2</summary>")
NOTE = "Note: /work/f1.txt was read before the last conversation was summarized"
FINAL = "Read f3.txt. Its CODEWORD line is `CODEWORD t3`."
NEXT = "Read f9.txt and tell me its CODEWORD line"
LATER = "Read f7.txt and tell me its CODEWORD line"
LATER_FINAL = "f7.txt says CODEWORD t7"


def compact(history):
    """Claude Code's compaction request: the history, the last user message extended."""
    *before, last = history
    return [*before, {**last, "content": [*last["content"], text(CRITICAL)]}]


async def claude_code_turn_one(cc):
    """Two tool steps of the first turn, as recorded before every shape."""
    await cc.send([FIRST, system(ENV, block=True)], step("t1"))
    history = [FIRST, system(ENV), step("t1"), tool_result("t1")]
    await cc.send(history, step("t2"))
    return history


async def claude_code_shape(cc, shape):
    """Runs one recorded compaction shape to the end of its turn."""
    history = await claude_code_turn_one(cc)
    if shape == "kept_tail":
        # Compacted after t2 ran; the step is kept verbatim behind the summary.
        await cc.send(compact(history), SUMMARIZED)
        history = [
            user(reminder("As you answer the user's questions (re-rendered)"), SUMMARY),
            system("Today's date is 2026-11-13.", block=True),
            step("t2"),
            tool_result("t2", ATTRIBUTION),
            system(NOTE, block=True),
        ]
        await cc.send(history, step("t3"))
    elif shape == "manual":
        # The turn ends; a resumed client runs /compact, then sends a new prompt.
        history = [*history, step("t2"), tool_result("t2")]
        await cc.send(history, assistant(FINAL))
        await cc.send(compact(history), SUMMARIZED)
        echo = [f"<{tag}>/compact</{tag}>" for tag in ("command-name", "local-command-stdout")]
        history = [
            {"role": "user", "content": SUMMARY},
            {"role": "system", "content": []},
            assistant(FINAL),
            user(REMIND, ATTRIBUTION, *echo, LATER),
            system(NOTE, block=True),
        ]
        await cc.send(history, step("t7"))
        history[-1] = system(NOTE)
        history = [*history, step("t7"), tool_result("t7")]
        await cc.send(history, assistant(LATER_FINAL))
        return
    elif shape == "later_turn":
        # The first turn ends; the next is compacted after its first step, without a tail.
        history = [*history, step("t2"), tool_result("t2")]
        await cc.send(history, assistant(FINAL))
        history = [*history, assistant(FINAL), user(REMIND, NEXT)]
        await cc.send(history, step("t9"))
        await cc.send(compact([*history, step("t9"), tool_result("t9")]), SUMMARIZED)
        history = [user(REMIND, ATTRIBUTION, SUMMARY), system(NOTE, block=True)]
        await cc.send(history, step("t3"))
    elif shape == "no_tail":
        # Compacted with t2 and its result; only the summary goes on.
        history = [*history, step("t2"), tool_result("t2")]
        await cc.send(compact(history), SUMMARIZED)
        history = [user(REMIND, ATTRIBUTION, SUMMARY), system(NOTE, block=True)]
        await cc.send(history, step("t3"))
    history[-1] = system(history[-1]["content"][0]["text"])  # The block becomes a string.
    history = [*history, step("t3"), tool_result("t3")]
    await cc.send(history, assistant(FINAL))


CC = {"x-claude-code-session-id": "cc"}


async def test_reminder_beside_a_tool_result_is_conversation(client):
    cc = client("anthropic", CC)
    history = await claude_code_turn_one(cc)
    quoted = reminder(f"The user sent the following message:\n{CRITICAL}")
    await cc.send([*history, step("t2"), tool_result("t2", quoted)], assistant(FINAL))
    assert cc.saved() == [[PROMPT, "t1", "t2", FINAL]]


@pytest.mark.parametrize(
    "shape", ["kept_tail", "no_tail", "later_turn", "manual", "no_tail_no_header"]
)
async def test_claude_code_compaction(client, shape):
    cc = client("anthropic", {} if shape == "no_tail_no_header" else CC)
    await claude_code_shape(cc, shape.removesuffix("_no_header"))
    # Each message once, in one session, except that the summary is saved as the user
    # message the client sent. The compaction request writes the history before its
    # instruction: without a kept tail, t2 (or t9) reaches only that request. After a
    # turn that ended, there is nothing to write. Without a header, the summarised history
    # is a new conversation.
    expected = {
        "kept_tail": [[PROMPT, "t1", SUMMARY, "t2", "t3", FINAL]],
        "no_tail": [[PROMPT, "t1", "t2", SUMMARY, "t3", FINAL]],
        "later_turn": [[PROMPT, "t1", "t2", FINAL, NEXT, "t9", SUMMARY, "t3", FINAL]],
        "manual": [[PROMPT, "t1", "t2", FINAL, LATER, "t7", LATER_FINAL]],
        "no_tail_no_header": [[PROMPT, "t1", "t2"], [SUMMARY, "t3", FINAL]],
    }
    assert cc.saved() == expected[shape]


# Codex 0.160.1 with a custom provider, recorded: the compaction request is the history and
# an instruction message, named by the turn metadata with its phase; the history is then
# rebuilt from the user messages and a summary message.


def item(role, value):
    kind = "output_text" if role == "assistant" else "input_text"
    return {"type": "message", "role": role, "content": [{"type": kind, "text": value}]}


def tool_call(name):
    return {"type": "custom_tool_call", "call_id": name, "name": "exec", "input": "cat f"}


def tool_output(name):
    output = [{"type": "input_text", "text": f"ok {name}"}]
    return {"type": "custom_tool_call_output", "call_id": name, "output": output}


INSTRUCTIONS = item("developer", "<model instructions>")
ENVIRONMENT = item("user", "<environment_context>cwd</environment_context>")
CHECKPOINT = "You are performing a CONTEXT CHECKPOINT COMPACTION. Create a handoff summary"
CODEX_SUMMARY = "Another language model started to solve this problem and produced a summary"


async def codex_send(codex, history, reply, **metadata):
    metadata = {"request_kind": "turn", **metadata}
    await codex.send(history, reply, {"x-codex-turn-metadata": json.dumps(metadata)})
    return [*history, reply]


async def test_codex_compaction_inside_a_turn(client):
    codex = client("responses", {"thread-id": "codex"})
    prompt = "fix the failing unit test in parser"
    start = [INSTRUCTIONS, ENVIRONMENT, item("user", prompt)]
    history = await codex_send(codex, start, tool_call("c1"))
    compaction = {"request_kind": "compaction", "compaction": {"phase": "mid_turn"}}
    for n in (1, 2):
        history = [*history, tool_output(f"c{n}"), item("user", CHECKPOINT)]
        await codex_send(codex, history, item("assistant", "summary"), **compaction)
        history = [*start, item("user", f"{CODEX_SUMMARY} {n}")]
        history = await codex_send(codex, history, tool_call(f"c{n + 1}"))
    await codex_send(codex, [*history, tool_output("c3")], item("assistant", "Fixed."))
    # Each message once, and each summary as the user message it is.
    saved = [prompt, "c1", f"{CODEX_SUMMARY} 1", "c2", f"{CODEX_SUMMARY} 2", "c3", "Fixed."]
    assert codex.saved() == [saved]
