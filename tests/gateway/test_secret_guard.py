"""Sealed credentials: upstreams get references, only client tool calls get values."""

import asyncio
import copy
import json

import orjson
import pytest
from aiohttp import web
from conftest import MCP_TOOLS, TEXT, mcp_tool
from test_app import enable_tools
from test_compaction import INSTRUCTION_START, summary_response
from test_native_tools import native_response, request_body, visible_response
from test_recall_notice import FIELD, PATHS, reply

from openviking_gateway.client import VikingError
from openviking_gateway.models import Policy
from openviking_gateway.protocols import SSEDecoder, text_content
from openviking_gateway.secret_guard import STALE_SECONDS, Catalog, Secrets, escape, reference
from openviking_gateway.storage import digest
from openviking_gateway.tool_catalog import select_tools

TOKEN = "ghp_0123456789abcdefABCDEF"
# Needs escaping inside JSON text.
PASSWORD = 'pa"ss\\word'
CREDENTIALS = {"github": {"token": TOKEN}, "db": {"password": PASSWORD, "port": "5432"}}
TOKEN_REF = reference("alice", "github", "token")
PASSWORD_REF = reference("alice", "db", "password")


def catalog(credentials=CREDENTIALS, owner="alice"):
    return Catalog(owner, {target: (1, values) for target, values in credentials.items()})


def leaked(value):
    """Whether ``value`` spells a registered value, at any depth of JSON text."""
    text = value.decode() if isinstance(value, bytes) else orjson.dumps(value).decode()
    forms = {f for v in (TOKEN, PASSWORD) for f in (v, escape(v), escape(escape(v)))}
    return any(form in text for form in forms)


# Sealing


def test_seal_is_idempotent_and_returns_unchanged_values_themselves():
    guard = catalog()
    body = {
        "model": "model",
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": f"use {TOKEN} or {TOKEN}"}]},
            {"role": "assistant", "content": "nothing here"},
        ],
    }
    sealed, count = guard.seal(body)
    assert count == 2 and sealed["messages"][0]["content"][0]["text"] == (
        f"use {TOKEN_REF} or {TOKEN_REF}"
    )
    # Only the changed path is copied; the input stays as it was.
    assert sealed["messages"][1] is body["messages"][1] and TOKEN in str(body)
    assert guard.seal(sealed) == (sealed, 0) and guard.seal(sealed)[0] is sealed
    miss = {"messages": [{"role": "user", "content": "port 5432"}], TOKEN: 1}
    # Keys are not text, and short values are never replaced.
    assert guard.seal(miss)[0] is miss
    assert Catalog().seal(body) == (body, 0) and not Catalog()


def test_only_known_references_shield_their_text():
    # The target name is itself a registered value, inside every reference to the target.
    guard = catalog({"acme-corp": {"key": "sk-live-1234567"}, "other": {"name": "acme-corp"}})
    known = reference("alice", "acme-corp", "key")
    sealed, count = guard.seal(f"{known} and acme-corp")
    assert count == 1 and sealed == f"{known} and {reference('alice', 'other', 'name')}"
    # A reference made up around a value protects nothing.
    forged = "{{ov_privacy:alice:viking://~/privacy/credential/x#/values/sk-live-1234567}}"
    assert guard.seal(forged)[0] == forged.replace("sk-live-1234567", known)


def test_signed_and_binary_content_is_left_alone_but_tools_never_are():
    # In the standard base64 alphabet, so a data URI can spell it.
    key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    guard = catalog({**CREDENTIALS, "aws": {"secret": key}})
    # Base64 that happens to spell a value.
    payload, uri = "QUJD" * 100 + TOKEN, "data:image/png;base64," + "QUJD" * 100 + key
    exempt = [
        {"type": "thinking", "thinking": TOKEN, "signature": "s"},
        {"type": "redacted_thinking", "data": TOKEN},
        {"type": "reasoning", "summary": [{"type": "summary_text", "text": TOKEN}]},
        {"type": "text", "text": "hi", "signature": TOKEN},
        {"type": "item", "encrypted_content": TOKEN},
        {"type": "image", "source": {"type": "base64", "data": payload}},
        {"type": "input_image", "image_url": uri},
    ]
    for value in exempt:
        assert guard.seal(value)[0] is value
    # Only a whole data URI is binary: text after one is still text.
    text = f"data:;base64,QUJD then {TOKEN}"
    assert guard.seal(text) == (text.replace(TOKEN, TOKEN_REF), 1)
    sealed = [
        # Argument names never exempt a tool's input, nor shapes its output.
        {
            "type": "tool_use",
            "id": "t",
            "name": "x",
            "input": {"signature": TOKEN, "data": payload},
        },
        {"type": "tool_result", "tool_use_id": "t", "content": [{"type": "text", "text": TOKEN}]},
        {
            "type": "tool_result",
            "tool_use_id": "t",
            "content": [{"type": "reasoning", "text": TOKEN}, {"type": "image", "data": payload}],
        },
        {"type": "function_call", "call_id": "c", "arguments": json.dumps({"key": TOKEN})},
        {"type": "function_call_output", "call_id": "c", "output": f"data: {TOKEN}"},
        {"type": "custom_tool_call_output", "call_id": "c", "output": uri},
        {"role": "tool", "tool_call_id": "c", "content": f"data:{TOKEN}", "signature": TOKEN},
    ]
    for value in sealed:
        new, count = guard.seal(value)
        assert count and not leaked(new) and key not in str(new), value


def test_values_inside_json_text_are_sealed_in_their_escaped_form():
    guard = catalog()
    arguments = json.dumps({"command": f"login {PASSWORD}"})
    assert escape(PASSWORD) in arguments
    call = {"id": "c", "type": "function", "function": {"name": "x", "arguments": arguments}}
    sealed, count = guard.seal(call)
    assert count == 1
    assert json.loads(sealed["function"]["arguments"]) == {"command": f"login {PASSWORD_REF}"}


def test_values_are_sealed_as_common_json_encoders_escape_them():
    values = {"latin": "pässwörd-123", "aws": "wJalrXUtnFEMI/K7MDENG", "html": "p&ss<w>rd-123"}
    guard = catalog({"keys": values})
    for field, value in values.items():
        python = json.dumps({"v": value})
        minimal = json.dumps({"v": value}, ensure_ascii=False)
        go = minimal.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
        # PHP writes the slash escaped.
        for text in {python, python.replace("/", "\\/"), minimal.replace("/", "\\/"), go}:
            sealed, count = guard.seal(text)
            assert count == 1 and json.loads(sealed) == {"v": reference("alice", "keys", field)}


def test_a_value_in_several_fields_seals_to_its_smallest_reference():
    guard = catalog(
        {"b": {"key": "shared-value"}, "a": {"zz": "shared-value", "y": "shared-value"}}
    )
    smallest = min(reference("alice", t, f) for t, f in (("b", "key"), ("a", "zz"), ("a", "y")))
    assert guard.seal("shared-value") == (smallest, 1)
    # Every one of them still unseals to the value.
    for target, field in (("b", "key"), ("a", "zz")):
        call = {"type": "custom_tool_call", "input": reference("alice", target, field)}
        assert guard.unseal(call)[0]["input"] == "shared-value"


# Unsealing


def test_unseal_escapes_values_in_json_text_and_keeps_inputs_raw():
    guard = catalog()
    command, expected = f"login {PASSWORD_REF} {TOKEN_REF}", f"login {PASSWORD} {TOKEN}"
    raw = json.dumps({"command": command})
    # Arguments as JSON text, by the type of the call or event that holds them.
    for kind, field in [
        ("input_json_delta", "partial_json"),
        ("function_call", "arguments"),
        ("response.function_call_arguments.done", "arguments"),
    ]:
        opened, count = guard.unseal({"type": kind, field: raw})
        assert count == 2 and json.loads(opened[field]) == {"command": expected}
    chat = {"type": "function", "function": {"name": "shell", "arguments": raw}}
    opened, count = guard.unseal({"choices": [{"message": {"tool_calls": [chat]}}]})
    arguments = opened["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
    assert count == 2 and json.loads(arguments) == {"command": expected}
    # Some Chat streams leave the type out.
    untyped = {"index": 0, "function": {"name": "shell", "arguments": raw}}
    opened, count = guard.unseal({"choices": [{"delta": {"tool_calls": [untyped]}}]})
    arguments = opened["choices"][0]["delta"]["tool_calls"][0]["function"]["arguments"]
    assert count == 2 and json.loads(arguments) == {"command": expected}
    # Arguments as values: an object input, and custom tools' plain text.
    opened, count = guard.unseal({"type": "tool_use", "input": {"command": [command]}})
    assert count == 2 and opened["input"] == {"command": [expected]}
    for value in [
        {"type": "custom_tool_call", "input": command},
        {"type": "response.custom_tool_call_input.done", "input": command},
    ]:
        assert guard.unseal(value) == ({**value, "input": expected}, 2)
    opened, count = guard.unseal({"type": "custom", "custom": {"name": "x", "input": command}})
    assert count == 2 and opened["custom"]["input"] == expected


def test_unseal_opens_only_the_callers_own_known_client_call_references():
    guard = catalog()
    untouched = [
        # Another owner, an unknown field, a value too short to seal, a malformed reference.
        {"type": "tool_use", "input": {"a": reference("bob", "github", "token")}},
        {"type": "tool_use", "input": {"a": reference("alice", "github", "missing")}},
        {"type": "tool_use", "input": {"a": reference("alice", "db", "port")}},
        {"type": "tool_use", "input": {"a": TOKEN_REF.replace("#/values/", "#/value/")}},
        # Spellings OpenViking accepts but does not render would seal back to another text.
        {"type": "tool_use", "input": [TOKEN_REF.replace("{{", "{{ ").replace("}}", "\t}}")]},
        {"type": "tool_use", "input": [TOKEN_REF.replace("github", "git%68ub")]},
        # Text, thinking and vendor-run tools are never unsealed.
        {
            "content": [
                {"type": "text", "text": TOKEN_REF},
                {"type": "thinking", "thinking": TOKEN_REF},
            ]
        },
        {"content": [{"type": "server_tool_use", "input": {"query": TOKEN_REF}}]},
        {"output": [{"type": "message", "content": [{"type": "output_text", "text": TOKEN_REF}]}]},
        {"output": [{"type": "mcp_call", "arguments": json.dumps({"q": TOKEN_REF})}]},
        {
            "choices": [
                {"message": {"content": TOKEN_REF, "function_call": {"arguments": TOKEN_REF}}}
            ]
        },
    ]
    for value in untouched:
        assert guard.unseal(value) == (value, 0) and guard.unseal(value)[0] is value
    assert Catalog().unseal({"type": "tool_use", "input": [TOKEN_REF]})[1] == 0


def test_unseal_never_changes_what_the_gateway_keeps():
    guard = catalog()
    final = {
        "content": [
            {"type": "text", "text": "Running it"},
            {"type": "tool_use", "id": "t", "name": "shell", "input": {"command": TOKEN_REF}},
        ]
    }
    kept = copy.deepcopy(final)
    opened, count = guard.unseal(final)
    assert count == 1 and final == kept and opened["content"][0] is final["content"][0]
    assert opened["content"][1]["input"] == {"command": TOKEN}


def test_responses_deltas_that_split_a_reference_carry_whole_arguments_first():
    guard = catalog()
    arguments = json.dumps({"command": f"login {PASSWORD_REF}"})
    cut = arguments.index("ov_privacy") + 3

    def call(index, fragments, text):
        events = [{"type": "response.output_item.added", "output_index": index, "item": {}}]
        events += [
            {"type": "response.function_call_arguments.delta", "output_index": index, "delta": f}
            for f in fragments
        ]
        events.append(
            {
                "type": "response.function_call_arguments.done",
                "output_index": index,
                "arguments": text,
            }
        )
        return events

    plain = json.dumps({"command": "pwd"})
    events = [
        *call(0, [arguments[:cut], arguments[cut:]], arguments),
        *call(1, [plain[:3], plain[3:]], plain),
    ]
    opened, count = guard.unseal(events)
    deltas = [e["delta"] for e in opened if e["type"].endswith(".delta") and e["output_index"] == 0]
    expected = json.dumps({"command": f"login {PASSWORD}"})
    assert deltas == [expected, ""] and opened[3]["arguments"] == expected and count == 2
    # A call without a reference streams exactly as the upstream sent it.
    assert all(a is b for a, b in zip(opened[4:], events[4:], strict=True))
    unchanged = events[4:]
    assert guard.unseal(unchanged) == (unchanged, 0) and guard.unseal(unchanged)[0] is unchanged


# Loading


class Privacy:
    """OpenViking's credential tree and values for one caller."""

    def __init__(self, credentials):
        self.credentials = {t: (1, v) for t, v in credentials.items()}
        self.paths, self.failure = [], None

    async def request(self, method, path, key, body=None, timeout=30):
        assert (method, key) == ("GET", "user-key")
        self.paths.append(path)
        await asyncio.sleep(0)
        if self.failure:
            raise self.failure
        if path == "/api/v1/privacy-configs/tree?category=credential":
            targets = [{"target_key": t, "version": v} for t, (v, _) in self.credentials.items()]
            return {
                "owner_user_id": "alice",
                "categories": [{"category": "credential", "targets": targets}],
            }
        version, values = self.credentials[path.rsplit("/", 1)[1]]
        return {"meta": {}, "current": {"version": version, "values": values}}


async def test_catalog_refreshes_changed_targets_and_survives_short_outages(caplog):
    privacy = Privacy(CREDENTIALS)
    secrets = Secrets(privacy)
    first = await asyncio.gather(*(secrets.catalog("user-key") for _ in range(5)))
    assert all(c is first[0] for c in first) and first[0].seal(TOKEN) == (TOKEN_REF, 1)
    assert sorted(privacy.paths) == [
        "/api/v1/privacy-configs/credential/db",
        "/api/v1/privacy-configs/credential/github",
        "/api/v1/privacy-configs/tree?category=credential",
    ]
    # Within the refresh interval nothing is read; after it, only the tree, then changed targets.
    privacy.paths.clear()
    assert await secrets.catalog("user-key") is first[0] and not privacy.paths
    secrets.fresh.clear()
    assert await secrets.catalog("user-key") is first[0] and len(privacy.paths) == 1
    privacy.credentials["db"] = (2, {"password": "rotated-password"})
    secrets.fresh.clear()
    rotated = await secrets.catalog("user-key")
    assert privacy.paths[1:] == [
        "/api/v1/privacy-configs/tree?category=credential",
        "/api/v1/privacy-configs/credential/db",
    ]
    assert rotated.seal("rotated-password")[1] == 1 and rotated.seal(PASSWORD)[1] == 0
    # A failed refresh keeps the last catalog for a while, then fails closed.
    privacy.failure = VikingError("openviking_unavailable")
    secrets.fresh.clear()
    assert await secrets.catalog("user-key") is rotated
    # The log names the cause, such as a server without the credential tree, never a value.
    assert "openviking_unavailable" in caplog.text and "rotated-password" not in caplog.text
    for name, (read_at, value) in secrets.last.items():
        secrets.last[name] = read_at - STALE_SECONDS - 1, value
    secrets.fresh.clear()
    with pytest.raises(VikingError):
        await secrets.catalog("user-key")
    # Without credentials there is nothing to seal, which is no failure.
    privacy.failure, privacy.credentials = None, {}
    assert not await Secrets(privacy).catalog("user-key")


def test_profile_switches_and_the_tools_they_withdraw():
    with pytest.raises(ValueError, match="unseal_tool_calls requires seal_secrets"):
        Policy(unseal_tool_calls=True)
    tools = [*MCP_TOOLS, mcp_tool("privacy_resolve", {"selector": TEXT}, ("selector",))]

    def offered(**policy):
        return {
            t["function"]["name"] for t in select_tools(tools, {"disabled_tools": [], **policy})
        }

    assert "openviking_privacy_resolve" in offered()
    assert "openviking_privacy_resolve" not in offered(seal_secrets=True)
    assert "openviking_find" in offered(seal_secrets=True)


# Through the gateway


async def profile(client, admin, **policy):
    response = await client.put(
        "/admin/policies/default",
        headers=admin,
        json={
            "name": "Sealed",
            "gateway_tools": False,
            "recall": False,
            "compaction": False,
            "seal_secrets": True,
            **policy,
        },
    )
    assert response.status_code == 200, response.text


def serve(app, model=None, credentials=CREDENTIALS, reads=None):
    """Serve the caller's credentials from the fake OpenViking; ``model`` answers the rest."""

    async def handler(request):
        if not request.path.startswith("/api/v1/privacy-configs/"):
            return await model(request) if model else None
        assert request.headers["X-API-Key"] == "user-key"
        if reads is not None:
            reads.append(request.path)
        if credentials is None:
            return web.json_response({"status": "error"}, status=503)
        if request.path.endswith("/tree"):
            targets = [{"target_key": t, "version": 1, "fields": []} for t in credentials]
            result = {
                "owner_user_id": "alice",
                "categories": [{"category": "credential", "targets": targets}],
            }
        else:
            values = credentials[request.path.rsplit("/", 1)[1]]
            result = {"meta": {}, "current": {"version": 1, "values": values}}
        return web.json_response({"status": "ok", "result": result})

    app.state.test_backend["handler"] = handler


def upstream(protocol, streaming, requests, respond):
    """The model: records each request's bytes and answers ``respond(number)``."""

    async def answer(request):
        if request.path != PATHS[protocol]:
            return None
        requests.append(await request.read())
        return await reply(request, protocol, respond(len(requests)), streaming)

    return answer


def history(protocol):
    """A conversation in which the user, a client tool call and its result hold values."""
    arguments = {"command": f"login {TOKEN} {PASSWORD}"}
    said, result = {"role": "user", "content": f"My token is {TOKEN}"}, f"logged in as {TOKEN}"
    if protocol == "anthropic":
        call = {"type": "tool_use", "id": "c-1", "name": "shell", "input": arguments}
        output = {"type": "tool_result", "tool_use_id": "c-1", "content": result}
        return [
            said,
            {"role": "assistant", "content": [call]},
            {"role": "user", "content": [output, {"type": "text", "text": "Continue"}]},
        ]
    if protocol == "chat":
        function = {"name": "shell", "arguments": json.dumps(arguments)}
        return [
            said,
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "c-1", "type": "function", "function": function}],
            },
            {"role": "tool", "tool_call_id": "c-1", "content": result},
            {"role": "user", "content": "Continue"},
        ]
    return [
        said,
        {
            "type": "function_call",
            "call_id": "c-1",
            "name": "shell",
            "arguments": json.dumps(arguments),
        },
        {"type": "function_call_output", "call_id": "c-1", "output": result},
        {"role": "user", "content": "Continue"},
    ]


def headers(key, **extra):
    return {"Authorization": "Bearer " + key["key"], **extra}


@pytest.mark.parametrize("protocol", ["chat", "anthropic", "responses"])
@pytest.mark.parametrize("streaming", [False, True])
async def test_the_upstream_gets_references_for_every_registered_value(
    running_gateway, protocol, streaming
):
    app, client, admin, key, _, _ = running_gateway
    await profile(client, admin)
    requests, reads = [], []
    serve(
        app,
        upstream(protocol, streaming, requests, lambda n: native_response(protocol, n)),
        reads=reads,
    )
    body = {**request_body(protocol, streaming), FIELD[protocol]: history(protocol)}
    response = await client.post(PATHS[protocol], headers=headers(key), json=body)
    assert response.status_code == 200, response.text
    [sent] = requests
    assert not leaked(sent) and TOKEN_REF.encode() in sent and PASSWORD_REF.encode() in sent
    # The catalog is read once, with the caller's own key, and cached.
    response = await client.post(PATHS[protocol], headers=headers(key), json=body)
    assert response.status_code == 200 and not leaked(requests[1])
    assert len(reads) == 3
    logs = (await client.get("/admin/logs", headers=admin)).json()
    assert logs[0]["secrets_sealed"] == 4 and "secrets_sealed_egress" not in logs[0]


async def test_recall_and_its_summary_are_sealed_on_the_way_out(running_gateway):
    app, client, admin, key, _, _ = running_gateway
    upstream_settings = {
        "name": "chat",
        "protocol": "chat",
        "base_url": app.state.openviking_url + "/v1",
        "api_key": "model-secret",
        "models": ["model"],
        "context_windows": {"model": 1024},
    }
    response = await client.put("/admin/upstreams/chat", headers=admin, json=upstream_settings)
    assert response.status_code == 200, response.text
    await profile(client, admin, recall=True, compaction=True)
    sent, summaries = [], []

    async def model(request):
        if request.path == "/api/v1/search/search":
            # A memory written before the value was registered still holds it.
            entry = {"uri": "viking://user/alice/memories/deploy.md", "text": f"key {TOKEN}"}
            result = {"entries": [entry], "rendered": f"deploy.md\nkey {TOKEN}"}
            return web.json_response({"status": "ok", "result": result})
        if request.path != PATHS["chat"]:
            return None
        raw = await request.read()
        if text_content(orjson.loads(raw)["messages"][-1]).startswith(INSTRUCTION_START):
            summaries.append(raw)
            return web.json_response(summary_response("chat"))
        sent.append(raw)
        message = {"role": "assistant", "content": "hello"}
        return web.json_response(
            {
                "choices": [{"message": message, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 5},
            }
        )

    serve(app, model)
    messages = [{"role": "user", "content": "How do I deploy?"}]
    body = {"model": "model", "messages": messages}
    session = headers(key, **{"X-OpenViking-Session": "sealed-summary"})
    for text in ("Next?", None):
        response = await client.post(PATHS["chat"], headers=session, json=body)
        assert response.status_code == 200, response.text
        if text:
            messages += [
                {"role": "assistant", "content": "hello"},
                {"role": "user", "content": text},
            ]
    [summary] = summaries
    assert TOKEN_REF.encode() in sent[0] and TOKEN_REF.encode() in summary
    assert not any(leaked(raw) for raw in [*sent, summary])
    logs = (await client.get("/admin/logs", headers=admin)).json()
    assert logs[-1]["secrets_sealed_egress"] == 1 and "secrets_sealed" not in logs[-1]
    assert logs[0]["secrets_sealed_egress"] >= 1 and logs[0]["compaction_tokens"]


@pytest.mark.parametrize("protocol", ["chat", "anthropic", "responses"])
async def test_recall_and_gateway_tool_results_are_sealed_before_they_are_kept_or_sent(
    running_gateway, monkeypatch, protocol
):
    app, client, admin, key, _, _ = running_gateway
    await enable_tools(client, admin, seal_secrets=True, recall=True)
    app.state.test_backend["catalog"] = [
        *MCP_TOOLS,
        mcp_tool("privacy_resolve", {"selector": TEXT}, ("selector",)),
    ]
    requests, kept = [], []
    put = app.state.store.replay.put

    async def keep(scope, session, kind, anchor, value):
        kept.append(value)
        return await put(scope, session, kind, anchor, value)

    monkeypatch.setattr(app.state.store.replay, "put", keep)
    answer = upstream(
        protocol, True, requests, lambda n: native_response(protocol, n, owned=n <= 2)
    )

    async def model(request):
        if request.path == "/api/v1/search/search":
            entry = {"uri": "viking://user/alice/memories/deploy.md", "text": f"key {TOKEN}"}
            result = {"entries": [entry], "rendered": f"deploy.md\nkey {TOKEN}"}
            return web.json_response({"status": "ok", "result": result})
        if request.path == "/mcp":
            # What privacy_resolve would return for a credential.
            content = [{"type": "text", "text": json.dumps({"value": TOKEN})}]
            return web.json_response({"jsonrpc": "2.0", "id": 1, "result": {"content": content}})
        return await answer(request)

    serve(app, model)
    body = request_body(protocol, True)
    response = await client.post(PATHS[protocol], headers=headers(key), json=body)
    assert response.status_code == 200, response.text
    first, *continuations = requests
    names = {
        t.get("name") or t.get("function", {}).get("name") for t in orjson.loads(first)["tools"]
    }
    assert "openviking_search" in names and "openviking_privacy_resolve" not in names
    assert len(continuations) == 2 and TOKEN_REF.encode() in continuations[-1]
    assert not any(leaked(raw) for raw in requests)
    # Recall and tool receipts are kept as references too.
    assert kept and not leaked(kept)
    # Each value the gateway added counts once: the recall and two tool results.
    logs = (await client.get("/admin/logs", headers=admin)).json()
    assert logs[0]["secrets_sealed_egress"] == 3 and logs[0]["hidden_rounds"] == 2


def client_reply(protocol, response, streaming):
    """The reply as the client keeps it in its history."""
    if protocol != "chat":
        visible = visible_response(protocol, response, streaming)
        if protocol == "responses":
            return visible["output"]
        return [{"role": "assistant", "content": visible["content"]}]
    if not streaming:
        message = response.json()["choices"][0]["message"]
    else:
        message = {"role": "assistant", "content": ""}
        for frame in SSEDecoder().feed(response.content):
            for choice in (SSEDecoder.data(frame) or {}).get("choices", []):
                delta = dict(choice["delta"])
                if calls := delta.pop("tool_calls", None):
                    message["tool_calls"] = [
                        {k: v for k, v in c.items() if k != "index"} for c in calls
                    ]
                message["content"] += delta.get("content") or ""
    return [{k: v for k, v in message.items() if k in {"role", "content", "tool_calls"}}]


def shell_command(protocol, output):
    """The command of the reply's client shell call."""
    if protocol == "anthropic":
        return output[0]["content"][-1]["input"]["command"]
    if protocol == "chat":
        return json.loads(output[0]["tool_calls"][-1]["function"]["arguments"])["command"]
    return json.loads(output[-1]["arguments"])["command"]


def calling(protocol, number, command):
    """Upstream reply ``number``: the first one calls the client's shell with ``command``."""
    value = native_response(protocol, number, mixed=number == 1)
    if number == 1:
        if protocol == "anthropic":
            value["content"][-1]["input"] = {"command": command}
        elif protocol == "chat":
            call = value["choices"][0]["message"]["tool_calls"][-1]
            call["function"]["arguments"] = json.dumps({"command": command})
        else:
            value["output"][-1]["arguments"] = json.dumps({"command": command})
    return value


def tool_result(protocol, text):
    if protocol == "anthropic":
        content = [{"type": "tool_result", "tool_use_id": "client-1", "content": text}]
        return {"role": "user", "content": content}
    if protocol == "chat":
        return {"role": "tool", "tool_call_id": "client-1", "content": text}
    return {"type": "function_call_output", "call_id": "client-1", "output": text}


@pytest.mark.parametrize("protocol", ["chat", "anthropic", "responses"])
@pytest.mark.parametrize("streaming", [False, True])
async def test_client_tool_calls_get_values_and_the_next_turn_continues(
    running_gateway, protocol, streaming
):
    app, client, admin, key, _, _ = running_gateway
    await profile(client, admin, unseal_tool_calls=True)
    requests, upstream_replies = [], []

    def respond(number):
        value = calling(protocol, number, f"login {PASSWORD_REF} {TOKEN_REF}")
        upstream_replies.append(copy.deepcopy(value))
        return value

    serve(app, upstream(protocol, streaming, requests, respond))
    body = request_body(protocol, streaming)
    # Anonymous: the next turn finds its session by the reply it resends.
    response = await client.post(PATHS[protocol], headers=headers(key), json=body)
    assert response.status_code == 200, response.text
    output = client_reply(protocol, response, streaming)
    # Values JSON text needs escaped arrive as valid JSON; nothing else changed.
    assert shell_command(protocol, output) == f"login {PASSWORD} {TOKEN}"
    if protocol == "responses" and streaming:
        events = [SSEDecoder.data(f) for f in SSEDecoder().feed(response.content)]
        deltas = [
            e["delta"] for e in events if e["type"] == "response.function_call_arguments.delta"
        ]
        [done] = [e for e in events if e["type"] == "response.function_call_arguments.done"]
        assert "".join(deltas) == deltas[0] == done["arguments"] == output[-1]["arguments"]
    body[FIELD[protocol]] += [*output, tool_result(protocol, "ok")]
    response = await client.post(PATHS[protocol], headers=headers(key), json=body)
    assert response.status_code == 200, response.text
    # The resent call goes upstream exactly as the model wrote it.
    resent = orjson.loads(requests[1])[FIELD[protocol]]
    first = upstream_replies[0]
    if protocol == "anthropic":
        assert resent[1] == {"role": "assistant", "content": first["content"]}
    elif protocol == "chat":
        assert {k: v for k, v in resent[1].items() if k != "reasoning_content"} == (
            first["choices"][0]["message"]
        )
    else:
        assert resent[1 : 1 + len(first["output"])] == first["output"]
    assert not any(leaked(raw) for raw in requests)
    logs = (await client.get("/admin/logs", headers=admin)).json()
    assert len({log["session"] for log in logs}) == 1
    assert logs[1]["secrets_unsealed"] == 2 and logs[0]["secrets_sealed"] == 2
    assert "secrets_unsealed" not in logs[0]
    # What the gateway saves for memory has the references, not the values.
    capture = await app.state.store.capture.get(
        digest("tenant\0alice\0" + protocol), logs[0]["session"]
    )
    saved = orjson.dumps(capture.value)
    assert capture.value["pending"] and PASSWORD_REF.encode() in saved and not leaked(saved)


@pytest.mark.parametrize("protocol", ["chat", "anthropic", "responses"])
async def test_replies_without_references_pass_the_tool_loop_unchanged(running_gateway, protocol):
    app, client, admin, key, _, _ = running_gateway
    await profile(client, admin, unseal_tool_calls=True)
    requests, upstream_replies = [], []

    def respond(number):
        upstream_replies.append(calling(protocol, number, "pwd"))
        return copy.deepcopy(upstream_replies[-1])

    serve(app, upstream(protocol, False, requests, respond))
    response = await client.post(
        PATHS[protocol], headers=headers(key), json=request_body(protocol, False)
    )
    assert response.status_code == 200, response.text
    value, upstream_reply = response.json(), upstream_replies[0]
    if protocol == "chat":
        assert value["choices"][0]["message"] == upstream_reply["choices"][0]["message"]
    else:
        key_name = "content" if protocol == "anthropic" else "output"
        assert value[key_name] == upstream_reply[key_name]
    logs = (await client.get("/admin/logs", headers=admin)).json()
    assert "secrets_unsealed" not in logs[0] and not logs[0].get("hidden_rounds")


async def test_unavailable_credentials_fail_the_request_before_the_upstream(running_gateway):
    app, client, admin, key, seen, _ = running_gateway
    await profile(client, admin)
    serve(app, credentials=None)
    body = {"model": "model", "messages": [{"role": "user", "content": "Hi"}]}
    response = await client.post(PATHS["chat"], headers=headers(key), json=body)
    assert response.status_code == 503 and response.json()["reason"] == "secrets_unavailable"
    assert not seen
    # A request without a body has nothing to seal, so it does not wait for credentials.
    response = await client.get("/v1/files", headers=headers(key))
    assert response.status_code == 200 and seen[-1][0] == "/v1/files"


@pytest.mark.parametrize("field", ["seal_secrets", "unseal_tool_calls"])
async def test_the_context_header_cannot_change_the_switches(running_gateway, field):
    app, client, admin, key, seen, _ = running_gateway
    await profile(client, admin)
    serve(app)
    response = await client.post(
        PATHS["chat"],
        headers=headers(key, **{"X-OpenViking-Context": json.dumps({field: False})}),
        json={"model": "model", "messages": [{"role": "user", "content": TOKEN}]},
    )
    assert response.status_code == 400 and field in response.json()["detail"]
    assert not seen


async def test_a_miss_forwards_the_request_bytes_unchanged(running_gateway):
    app, client, admin, key, seen, _ = running_gateway
    await profile(client, admin, profile=False, capture=False)
    serve(app)
    raw = b'{"model":  "model", "messages": [{"role": "user", "content": "nothing secret"}]}'
    content = {"Content-Type": "application/json"}
    response = await client.post(PATHS["chat"], headers=headers(key, **content), content=raw)
    assert response.status_code == 200, response.text
    assert seen[-1][1] == raw
    logs = (await client.get("/admin/logs", headers=admin)).json()
    assert not {"secrets_sealed", "secrets_sealed_egress"} & logs[0].keys()


async def test_the_live_profile_decides_and_bodies_it_cannot_read_are_refused(running_gateway):
    app, client, admin, key, seen, _ = running_gateway
    files = []

    async def upload(request):
        if request.path == "/v1/files":
            files.append(await request.read())
            return web.json_response({})

    serve(app, upload)
    body = {"model": "model", "messages": [{"role": "user", "content": TOKEN}]}
    session = headers(key, **{"X-OpenViking-Session": "live"})
    response = await client.post(PATHS["chat"], headers=session, json=body)
    assert response.status_code == 200 and leaked(seen[-1][1])
    # A session started before sealing was turned on is sealed from then on.
    await profile(client, admin)
    response = await client.post(PATHS["chat"], headers=session, json=body)
    assert response.status_code == 200 and not leaked(seen[-1][1])
    count = len(seen)
    duplicate = f'{{"model": "model", "model": "model", "prompt": "{TOKEN}"}}'.encode()
    for path, extra, raw, status in [
        (PATHS["chat"], {"Content-Encoding": "gzip"}, orjson.dumps(body), 415),
        (PATHS["chat"], {}, b'{"model": "model", "model": "model", "messages": []}', 400),
        # JSON the gateway could only forward byte for byte, off the model protocols too.
        ("/v1/completions", {}, duplicate, 400),
    ]:
        response = await client.post(
            path, headers={**session, **extra, "Content-Type": "application/json"}, content=raw
        )
        assert response.status_code == status, response.text
    assert len(seen) == count
    # Bodies off the model protocols, such as file uploads, pass through.
    response = await client.post(
        "/v1/files", headers={**session, "Content-Type": "text/plain"}, content=b"not json"
    )
    assert response.status_code == 200 and files == [b"not json"]
