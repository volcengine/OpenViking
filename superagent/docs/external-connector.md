# External Connector

[Overview](../README.md) · [Connector protocol](connector-protocol.md) · [Features](usage-reference.md#a-connector-you-run-yourself)

Built-in connectors cover Feishu/Lark, WeChat, Telegram, Slack, QQ, OneBot, and email. An external connector is another program you run, for an IM those do not cover. Core starts it the same way it starts a built-in connector: one child process, protocol 0.2 on stdin and stdout, logs on stderr. The program owns the IM login, the receive loop, and any credentials. Core still owns the agent session, the OpenViking space, event deduplication, and the outbound outbox.

Setup does not ask for this. Add it to the instance in `config.json`, then restart that instance.

```json
{
  "channel": "dingtalk",
  "connectors": ["dingtalk"],
  "externalConnectors": [
    {
      "id": "dingtalk",
      "command": "/usr/local/bin/ovsa-dingtalk",
      "args": [],
      "prompt": "/usr/local/share/ovsa/dingtalk.md"
    }
  ]
}
```

`id` is the connector name on inbound lines (`source=dingtalk`) and must match the `name` in the program's `spec`. It starts with a lowercase letter and then uses only lowercase letters, digits, and `-`. It cannot be `lark`, `weixin`, `email`, `telegram`, `slack`, `qq`, or `onebot`. List the same id in `connectors`. `channel` may be that id when this IM should receive unsolicited notices and email approvals; the program's `spec` must then accept `message.send` with `markdown`, and `check` must report an `owner` with a direct-chat `target`.

`command` is an absolute path. Core runs it with `shell: false` and the `args` array, never through a shell. Do not put tokens in `args` or in `config.json`. Keep them in the program's own state, as the built-in connectors keep theirs under `<dataDir>/<im>/`. `prompt` is an optional absolute path to a markdown file (at most 16 KiB are shown to the model) with rules for this IM only.

The process receives `PERSONAL_AGENT_CONNECTOR_CONFIG` as one JSON object: `channel`, `instance`, `dataDir`, `tempDir`, `projectDir`, and the usual limits. It does not receive the Feishu profile, Lark owner, or CLI paths. A spec, check, or start failure restarts only this process. On `check`, report `owner` and, when the IM has a bot id, `self`, the same way a built-in connector does. Send chat as `message.created` events. Accept the actions you declared, usually `message.send` and `message.reply`.

An external connector cannot be the runtime on Ark / Volc: that runtime has no path to Core's tools. `ov-superagent setup` keeps an external connector that is already in the instance when you change the built-in IMs. It does not add one.

See [connector-protocol.md](connector-protocol.md) for the message shapes. A program that only needs to prove the pipe can answer `spec`, `check`, and `start` and then emit one `EVENT`.
