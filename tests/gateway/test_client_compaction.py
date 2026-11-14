"""Client compaction as real clients send it: Claude Code recorded through a proxy,
Codex and OpenCode rebuilt from their source and upstream request snapshots."""

import copy
import json

import pytest
from conftest import make_due
from test_review_regressions import worker_for

from openviking_gateway.tool_protocols import ResponseCapture

ANTHROPIC_TOOLS = [{"name": "Read", "description": "Read a file", "input_schema": {}}]
RESPONSES_TOOLS = [{"type": "function", "name": "shell", "parameters": {}}]


class Client:
    """One client session, sending whole requests and receiving replies through the kernel."""

    def __init__(self, setup, credential, policy, protocol, headers):
        self.kernel, self.store, self.viking, encryption = setup
        self.credential, self.policy, self.protocol, self.headers = (
            credential,
            policy,
            protocol,
            headers,
        )
        self.encryption = encryption
        self.worker = None

    async def send(self, messages, reply, headers=None, tools=True):
        field = "input" if self.protocol == "responses" else "messages"
        body = {"model": "model", field: copy.deepcopy(messages)}
        if tools:
            body["tools"] = RESPONSES_TOOLS if self.protocol == "responses" else ANTHROPIC_TOOLS
        if self.protocol == "responses":
            body["store"] = False
        request = await self.kernel.prepare(
            body,
            self.protocol,
            {**self.headers, **(headers or {})},
            self.credential,
            {"id": "upstream"},
            self.policy,
        )
        items = reply if isinstance(reply, list) else [reply]
        response = ResponseCapture(
            self.protocol,
            items[-1],
            complete=True,
            output_items=items if self.protocol == "responses" else None,
        )
        await self.kernel.completed(request, self.credential, response)
        return request

    async def drain(self):
        if self.worker is None:
            self.worker = await worker_for(
                self.store, self.encryption, self.credential, self.viking
            )
        await make_due(self.store)
        while await self.worker.once():
            pass

    def saved(self):
        """What each OpenViking session received: texts and tool call IDs, in order."""
        labels = {}
        for batch, session in zip(self.viking.writes, self.viking.write_sessions, strict=True):
            for message in batch:
                for part in message["parts"]:
                    labels.setdefault(session, []).append(part.get("text") or part["tool_id"])
        return list(labels.values())


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


async def claude_code_next(cc, history, after_idle):
    if after_idle:
        await cc.drain()
    await cc.send([*history, user(REMIND, NEXT)], assistant("f9 says CODEWORD t9"))


@pytest.fixture
def cc(setup_kernel, credential, policy):
    policy.update(recall=False, commit_tokens=1000000)
    return Client(setup_kernel, credential, policy, "anthropic", {"x-claude-code-session-id": "cc"})


async def test_claude_code_ordinary_session_saves_each_message_once(cc):
    history = await claude_code_turn_one(cc)
    history = [*history, step("t2"), tool_result("t2")]
    await cc.send(history, assistant(FINAL))
    await claude_code_next(cc, [*history, assistant(FINAL)], after_idle=False)
    await cc.drain()
    assert cc.saved() == [[PROMPT, "t1", "t2", FINAL, NEXT, "f9 says CODEWORD t9"]]
    with cc.store.connect() as c:
        ((scope, session),) = list(c.execute("SELECT scope,session FROM capture"))
    assert (await cc.store.capture.get(scope, session)).value.get("epoch", 0) == 0


@pytest.mark.parametrize("then", ["session_ends", "next_prompt", "next_prompt_after_idle"])
@pytest.mark.parametrize("shape", ["kept_tail", "no_tail", "manual"])
async def test_claude_code_mid_turn_compaction(cc, shape, then):
    history = await claude_code_shape(cc, shape)
    tail = []
    if then != "session_ends":
        await claude_code_next(cc, history, after_idle=then == "next_prompt_after_idle")
        tail = [NEXT, "f9 says CODEWORD t9"]
    await cc.drain()
    # Each message once, in one session, except that the summary is saved as the user
    # message the client sent, and a final answer the client keeps after its summary is
    # saved again. Without a kept tail, t2 reaches only the compaction request.
    expected = {
        "kept_tail": [PROMPT, "t1", SUMMARY, "t2", "t3", FINAL],
        "no_tail": [PROMPT, "t1", "t2", SUMMARY, "t3", FINAL],
        "manual": [PROMPT, "t1", "t2", FINAL, SUMMARY, FINAL, LATER, "t7", LATER_FINAL],
    }
    assert cc.saved() == [[*expected[shape], *tail]]


# Codex 0.169 with a custom provider: inline compaction on /responses, marked by its turn
# metadata; the history is rebuilt from the user messages and a summary message.


def item(role, value):
    kind = "output_text" if role == "assistant" else "input_text"
    return {"type": "message", "role": role, "content": [{"type": kind, "text": value}]}


def function_call(name):
    return {"type": "function_call", "call_id": name, "name": "shell", "arguments": "{}"}


def function_output(name):
    return {"type": "function_call_output", "call_id": name, "output": f"ok {name}"}


INSTRUCTIONS = item("developer", "<model instructions>")
PERMISSIONS = item("developer", "<permissions instructions>")
ENVIRONMENT = item("user", "<environment_context>cwd</environment_context>")
CHECKPOINT = "You are performing a CONTEXT CHECKPOINT COMPACTION. Create a handoff summary"
CODEX_SUMMARY = "Another language model started to solve this problem and produced a summary"


def kind(value):
    return {"x-codex-turn-metadata": json.dumps({"request_kind": value})}


@pytest.fixture
def codex(setup_kernel, credential, policy):
    policy.update(recall=False, commit_tokens=1000000)
    return Client(setup_kernel, credential, policy, "responses", {"thread-id": "codex"})


async def codex_turn(codex, history, reply):
    await codex.send(history, reply, kind("turn"))
    return [*history, *(reply if isinstance(reply, list) else [reply])]


@pytest.mark.parametrize("when", ["mid_turn", "between_turns"])
async def test_codex_compaction(codex, when):
    start = [INSTRUCTIONS, PERMISSIONS, ENVIRONMENT]
    if when == "mid_turn":
        prompt = item("user", "fix the failing unit test in parser")
        history = await codex_turn(codex, [*start, prompt], [function_call("c1")])
        history = await codex_turn(codex, [*history, function_output("c1")], [function_call("c2")])
        history = [*history, function_output("c2")]
        await codex.send(
            [*history, item("user", CHECKPOINT)],
            item("assistant", "summary"),
            kind("compaction"),
            tools=False,
        )
        history = [*start, prompt, item("user", CODEX_SUMMARY)]
        history = await codex_turn(codex, history, [function_call("c3")])
        await codex_turn(codex, [*history, function_output("c3")], item("assistant", "Fixed."))
    else:
        one, two = item("user", "USER_ONE"), item("user", "USER_TWO")
        history = await codex_turn(codex, [*start, one], item("assistant", "FIRST_REPLY"))
        history = await codex_turn(codex, [*history, two], item("assistant", "SECOND_REPLY"))
        await codex.send(
            [*history, item("user", CHECKPOINT)],
            item("assistant", "summary"),
            kind("compaction"),
            tools=False,
        )
        history = [INSTRUCTIONS, one, PERMISSIONS, ENVIRONMENT, two, item("user", CODEX_SUMMARY)]
        await codex_turn(
            codex, [*history, item("user", "USER_THREE")], item("assistant", "THIRD_REPLY")
        )
    await codex.drain()
    # Not exactly once: the summary is saved as a user message; mid-turn, the turn's steps
    # are lost (c1 with the tail the rebuilt history drops, c2 that only the compaction
    # request carried); between turns, the user messages kept before the summary are saved
    # again.
    expected = {
        "mid_turn": ["fix the failing unit test in parser", CODEX_SUMMARY, "c3", "Fixed."],
        "between_turns": [
            *("USER_ONE", "FIRST_REPLY", "USER_TWO", "SECOND_REPLY"),
            *("USER_ONE", "USER_TWO", CODEX_SUMMARY, "USER_THREE", "THIRD_REPLY"),
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


@pytest.fixture
def opencode(setup_kernel, credential, policy):
    policy.update(recall=False, commit_tokens=1000000)
    return Client(setup_kernel, credential, policy, "anthropic", {"x-opencode-session-id": "oc"})


@pytest.mark.parametrize("when", ["mid_turn", "turn_boundary", "manual"])
async def test_opencode_compaction(opencode, when):
    oc = opencode
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
    await oc.drain()
    # Not exactly once: OpenCode's compaction request is not recognised, so its prompt and
    # the summary reply are saved, and the checkpoint is saved as a user message.
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
            *(checkpoint("[User]: fix the failing test"), "now add tests", "tests added"),
        ],
    }
    assert oc.saved() == [expected[when]]


@pytest.fixture
def anonymous(setup_kernel, credential, policy):
    policy.update(recall=False, commit_tokens=1000000)
    return Client(setup_kernel, credential, policy, "anthropic", {})


@pytest.mark.parametrize("then", ["session_ends", "next_prompt", "next_prompt_after_idle"])
async def test_claude_code_compaction_without_a_header(anonymous, then):
    history = await claude_code_shape(anonymous, "no_tail")
    if then != "session_ends":
        await claude_code_next(anonymous, history, after_idle=then == "next_prompt_after_idle")
    await anonymous.drain()
    # As main, without the instruction and summary main saved: the step the compaction
    # request carried joins its turn, and the summarised history is a new conversation.
    tail = [] if then == "session_ends" else [NEXT, "f9 says CODEWORD t9"]
    assert anonymous.saved() == [[PROMPT, "t1", "t2"], [SUMMARY, "t3", FINAL, *tail]]


QUOTED = reminder(
    "The user sent the following message:\nGenerate a short kebab-case name (2-4 words) that"
    " captures the main topic of this conversation."
)


@pytest.mark.parametrize("note", ["quoting_a_side_request", "plain"])
@pytest.mark.parametrize("header", [True, False])
async def test_claude_code_reminder_beside_a_tool_result_is_conversation(
    setup_kernel, credential, policy, note, header
):
    policy.update(recall=False, commit_tokens=1000000)
    headers = {"x-claude-code-session-id": "cc"} if header else {}
    cc = Client(setup_kernel, credential, policy, "anthropic", headers)
    history = await claude_code_turn_one(cc)
    beside = QUOTED if note == "quoting_a_side_request" else reminder("The file f2 was modified")
    history = [*history, step("t2"), tool_result("t2", beside)]
    await cc.send(history, assistant(FINAL))
    await cc.drain()
    assert cc.saved() == [[PROMPT, "t1", "t2", FINAL]]
