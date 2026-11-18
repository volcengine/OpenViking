"""Client compaction as real clients send it: Claude Code and Codex recorded through a
proxy, OpenCode rebuilt from its source and upstream request snapshots."""

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
        return request

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
    """Runs one recorded compaction shape and returns the history after its turn ended."""
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
        echo = [
            text(
                "<local-command-caveat>The command below was run by the user.</local-command-caveat>"
            ),
            text("<command-name>/compact</command-name>"),
            text(
                "<local-command-stdout>Compacted (ctrl+o to see full summary)</local-command-stdout>"
            ),
        ]
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
        return [*history, assistant(LATER_FINAL)]
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
    return [*history, assistant(FINAL)]


async def claude_code_next(cc, history):
    await cc.send([*history, user(REMIND, NEXT)], assistant("f9 says CODEWORD t9"))


CC = {"x-claude-code-session-id": "cc"}
QUOTED = reminder(
    "The user sent the following message:\nGenerate a short kebab-case name (2-4 words) that"
    " captures the main topic of this conversation."
)


@pytest.mark.parametrize("header", [True, False])
async def test_claude_code_ordinary_session_saves_each_message_once(client, header):
    cc = client("anthropic", CC if header else {})
    history = await claude_code_turn_one(cc)
    # A reminder beside a tool result is conversation.
    history = [*history, step("t2"), tool_result("t2", QUOTED)]
    await cc.send(history, assistant(FINAL))
    await claude_code_next(cc, [*history, assistant(FINAL)])
    assert cc.saved() == [[PROMPT, "t1", "t2", FINAL, NEXT, "f9 says CODEWORD t9"]]


@pytest.mark.parametrize("then", ["session_ends", "next_prompt"])
@pytest.mark.parametrize(
    "shape", ["kept_tail", "no_tail", "later_turn", "manual", "no_tail_no_header"]
)
async def test_claude_code_mid_turn_compaction(client, shape, then):
    cc = client("anthropic", {} if shape == "no_tail_no_header" else CC)
    history = await claude_code_shape(cc, shape.removesuffix("_no_header"))
    tail = []
    if then != "session_ends":
        await claude_code_next(cc, history)
        tail = [NEXT, "f9 says CODEWORD t9"]
    # Each message once, in one session, except that the summary is saved as the user
    # message the client sent. The compaction request writes the history before its
    # instruction: without a kept tail, t2 (or t9) reaches only that request. After a
    # turn that ended, there is nothing to write. Without a header, the summarised history
    # is a new conversation.
    expected = {
        "kept_tail": [[PROMPT, "t1", SUMMARY, "t2", "t3", FINAL, *tail]],
        "no_tail": [[PROMPT, "t1", "t2", SUMMARY, "t3", FINAL, *tail]],
        "later_turn": [[PROMPT, "t1", "t2", FINAL, NEXT, "t9", SUMMARY, "t3", FINAL, *tail]],
        "manual": [[PROMPT, "t1", "t2", FINAL, LATER, "t7", LATER_FINAL, *tail]],
        "no_tail_no_header": [[PROMPT, "t1", "t2"], [SUMMARY, "t3", FINAL, *tail]],
    }
    assert cc.saved() == expected[shape]


# Codex 0.160.1 with a custom provider, recorded: tools travel in a first input item that
# differs in a compaction request; the compaction request is the history and an instruction
# message, named by the turn metadata with its phase; the history is then rebuilt from the
# user messages and a summary message.


def item(role, value):
    kind = "output_text" if role == "assistant" else "input_text"
    return {"type": "message", "role": role, "content": [{"type": kind, "text": value}]}


def tool_call(name):
    return {"type": "custom_tool_call", "call_id": name, "name": "exec", "input": "cat f"}


def tool_output(name):
    output = [{"type": "input_text", "text": f"ok {name}"}]
    return {"type": "custom_tool_call_output", "call_id": name, "output": output}


def tools(count):
    listed = [{"type": "function", "name": "exec"}] * count
    return {"type": "additional_tools", "id": f"at_{count}", "role": "developer", "tools": listed}


INSTRUCTIONS = item("developer", "<model instructions>")
PERMISSIONS = item("developer", "<permissions instructions>")
ENVIRONMENT = item("user", "<environment_context>cwd</environment_context>")
CHECKPOINT = "You are performing a CONTEXT CHECKPOINT COMPACTION. Create a handoff summary"
CODEX_SUMMARY = "Another language model started to solve this problem and produced a summary"


async def codex_turn(codex, history, reply):
    metadata = {"x-codex-turn-metadata": json.dumps({"request_kind": "turn"})}
    await codex.send(history, reply, metadata)
    return [*history, *(reply if isinstance(reply, list) else [reply])]


async def codex_compact(codex, history, phase):
    metadata = {"request_kind": "compaction", "compaction": {"phase": phase}}
    await codex.send(
        [tools(0), *history[1:], item("user", CHECKPOINT)],
        item("assistant", "summary"),
        {"x-codex-turn-metadata": json.dumps(metadata)},
    )


@pytest.mark.parametrize("when", ["mid_turn", "between_turns"])
async def test_codex_compaction(client, when):
    codex = client("responses", {"thread-id": "codex"})
    start = [tools(3), INSTRUCTIONS, PERMISSIONS, ENVIRONMENT]
    if when == "mid_turn":
        prompt = item("user", "fix the failing unit test in parser")
        first, second = item("user", CODEX_SUMMARY + " 1"), item("user", CODEX_SUMMARY + " 2")
        history = await codex_turn(codex, [*start, prompt], [tool_call("c1")])
        history = await codex_turn(codex, [*history, tool_output("c1")], [tool_call("c2")])
        await codex_compact(codex, [*history, tool_output("c2")], "mid_turn")
        history = await codex_turn(codex, [*start, prompt, first], [tool_call("c3")])
        await codex_compact(codex, [*history, tool_output("c3")], "mid_turn")
        history = await codex_turn(codex, [*start, prompt, second], [tool_call("c4")])
        await codex_turn(codex, [*history, tool_output("c4")], item("assistant", "Fixed."))
    else:
        one, two = item("user", "USER_ONE"), item("user", "USER_TWO")
        history = await codex_turn(codex, [*start, one], item("assistant", "FIRST_REPLY"))
        history = await codex_turn(codex, [*history, two], item("assistant", "SECOND_REPLY"))
        await codex_compact(codex, history, "pre_turn")
        history = [tools(3), INSTRUCTIONS, one, two, item("user", CODEX_SUMMARY)]
        history = [*history, PERMISSIONS, ENVIRONMENT, item("user", "USER_THREE")]
        await codex_turn(codex, history, item("assistant", "THIRD_REPLY"))
    # Each message once; mid-turn, each summary is also saved as the user message it is.
    prompt, summary = "fix the failing unit test in parser", CODEX_SUMMARY
    expected = {
        "mid_turn": [prompt, "c1", "c2", summary + " 1", "c3", summary + " 2", "c4", "Fixed."],
        "between_turns": [
            *("USER_ONE", "FIRST_REPLY", "USER_TWO", "SECOND_REPLY"),
            *("USER_THREE", "THIRD_REPLY"),
        ],
    }
    assert codex.saved() == [expected[when]]


# OpenCode v2 over the Anthropic protocol: the compaction prompt is the last user message,
# and the history after it is one checkpoint message.

OPENCODE_PROMPT = "You MUST summarize the conversation above into a structured summary"
MID_RECENT = "[User]: fix the failing test\n[Assistant tool call]: Read({})\n[Tool result]: ok"


def checkpoint(recent):
    record = f"\n\n<recent-context>\n{recent}\n</recent-context>" if recent else ""
    return (
        "<conversation-checkpoint>\nThe following is a summary and serialized record of earlier"
        f" conversation.\n\n<summary>\nWork so far.\n</summary>{record}\n</conversation-checkpoint>"
    )


@pytest.mark.parametrize("when", ["mid_turn", "turn_boundary", "manual"])
async def test_opencode_compaction(client, when):
    oc = client("anthropic", {"x-opencode-session-id": "oc"})
    plain = lambda role, value: {"role": role, "content": value}  # noqa: E731
    history = [plain("user", "earlier ask")]
    await oc.send(history, assistant("done"))
    history = [*history, plain("assistant", "done"), plain("user", "fix the failing test")]
    await oc.send(history, assistant(tool_use("t1")))
    history = [*history, assistant(tool_use("t1")), tool_result("t1")]
    await oc.send(history, assistant(tool_use("t2")))
    history = [*history, assistant(tool_use("t2")), tool_result("t2")]
    if when == "mid_turn":
        await oc.send([*history, plain("user", OPENCODE_PROMPT)], assistant("summary"))
        history = [plain("user", checkpoint(MID_RECENT))]
        await oc.send(history, assistant(tool_use("t3")))
        history = [*history, assistant(tool_use("t3")), tool_result("t3")]
        await oc.send(history, assistant("fixed"))
    else:
        await oc.send(history, assistant("fixed"))
        history = [*history, assistant("fixed")]
        if when == "turn_boundary":
            history = [*history, plain("user", "now add tests")]
            await oc.send([*history, plain("user", OPENCODE_PROMPT)], assistant("summary"))
            history = [plain("user", checkpoint("[User]: now add tests"))]
        else:
            await oc.send([*history, plain("user", OPENCODE_PROMPT)], assistant("summary"))
            history = [
                plain("user", checkpoint("[User]: fix the failing test")),
                plain("user", "now add tests"),
            ]
        await oc.send(history, assistant("tests added"))
    # Not exactly once: OpenCode's compaction request is not recognised, so its prompt and
    # the summary reply are saved, and a checkpoint that starts the next turn is saved as a
    # user message.
    before = ["earlier ask", "done", "fix the failing test", "t1", "t2"]
    compaction = [OPENCODE_PROMPT, "summary"]
    expected = {
        "mid_turn": [*before, *compaction, checkpoint(MID_RECENT), "t3", "fixed"],
        "turn_boundary": [
            *(*before, "fixed", "now add tests", *compaction),
            *(checkpoint("[User]: now add tests"), "tests added"),
        ],
        "manual": [
            *(*before, "fixed", *compaction),
            *("now add tests", "tests added"),
        ],
    }
    assert oc.saved() == [expected[when]]
