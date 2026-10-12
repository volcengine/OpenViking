const connect = {
  description:
    'Point a model client at the gateway and authenticate with a gateway key. Pick the client you use to see its exact settings.',
  fullGuide: 'Full guide',
  address: {
    title: 'Gateway address',
    description:
      'Clients send their model requests here instead of to the provider. Anthropic clients use the address as it is; OpenAI-compatible clients add `/v1`.',
    anthropic: 'Claude Code and Anthropic SDKs',
    openai: 'OpenAI-compatible clients and SDKs',
    loopback: {
      title: "Only the gateway's own machine can use this address",
      description:
        "Clients on other machines can't reach it. Set `gateway.public_url` in `ov.conf` to an address they can reach, then restart the gateway.",
    },
    notPublic: {
      title: 'Clients may not be able to reach this address',
      description:
        'This is the address OpenViking uses to reach the gateway. Set `gateway.public_url` in `ov.conf` to the address clients should use, then restart the gateway.',
    },
    keys: 'Clients authenticate with a gateway key (`ovgw_…`) in place of a provider API key.',
    manageKeys: 'Manage keys',
  },
  guide: {
    title: 'Set up a client',
    description:
      "Pick the client you use. The snippets already contain this gateway's address.",
    clients: 'Clients',
    protocol: 'Calls the gateway with',
    upstreams: 'Available upstreams: {{names}}',
    upstreamsMore: 'Available upstreams: {{names}} and {{count}} more',
    separator: ', ',
    noUpstream: {
      title: 'Add an upstream for {{protocols}} first',
      description:
        'None of the enabled upstreams speaks {{protocols}}. The gateway only forwards a request to an upstream that speaks the same protocol.',
      descriptionPicked:
        'None of the enabled upstreams speaks {{protocols}}. The gateway only forwards a request to an upstream that speaks the same protocol. Add one, or set the client up with another protocol.',
      action: 'Add upstream',
      hint: 'No enabled upstream for this client yet',
      protocolHint: 'No enabled upstream speaks this protocol yet',
    },
    placeholders: 'Replace the placeholders',
    goodToKnow: 'Good to know',
  },
  placeholders: {
    key: 'A gateway key issued under Keys',
    model: 'A model name the upstream accepts',
    session:
      'An id that stays the same within one conversation, such as your chat id',
  },
  snippets: {
    env: 'Environment variables',
    config: 'Configuration',
    key: 'Terminal',
    settings: 'Client settings',
    python: 'Python',
    curl: 'Terminal',
    connection: 'Connection',
    headers: 'Headers',
    endpoints: 'Addresses',
  },
  clients: {
    'claude-code': {
      name: 'Claude Code',
      intro: "Anthropic's command-line coding agent.",
      steps: {
        env: 'Set these variables in the shell you start Claude Code from, or add them to your shell profile.',
        start: 'Start `claude` as usual.',
      },
      identity:
        'Claude Code sends its own session id, so the gateway recognizes each conversation automatically, also after `--resume`.',
      notes: {
        address:
          'Use the address without `/v1`; Claude Code adds the path itself.',
        hints:
          "`CLAUDE_CODE_GATEWAY_HINT_HEADERS=1` makes Claude Code label sub-agent, compaction and background requests, so the gateway doesn't search memory for them or save them as conversation turns.",
        models:
          "The upstream must accept the model names Claude Code asks for: leave its model list empty, list those names, or map them to your provider's model with aliases.",
        subscription:
          "A Claude subscription login doesn't work through the gateway. Give the upstream a provider API key.",
      },
    },
    codex: {
      name: 'Codex CLI',
      intro:
        "OpenAI's command-line coding agent. It sends the full history with every request, which is what the gateway needs for Responses.",
      steps: {
        config:
          'Add the gateway as a provider in `~/.codex/config.toml`. The two top-level lines must come before any `[section]`.',
        key: 'Put the gateway key in the environment Codex runs in.',
        start: 'Start `codex` as usual.',
      },
      identity:
        'Codex sends its own session id, so the gateway recognizes each conversation automatically, also after `codex resume`.',
      notes: {
        websocket:
          'Codex first tries a WebSocket connection. The gateway declines it, and Codex switches to HTTP automatically.',
        metadata:
          "Codex may warn that it has no metadata for an unfamiliar model name. The warning doesn't affect requests.",
        login:
          "A ChatGPT login doesn't work through the gateway. Give the upstream a provider API key.",
      },
    },
    chat: {
      name: 'Chat clients & SDKs',
      intro: 'Any client or SDK that speaks the OpenAI Chat Completions API.',
      steps: {
        settings:
          'Enter these values wherever the client asks for an OpenAI-compatible provider.',
        python: 'From code, point the OpenAI SDK at the gateway.',
        curl: 'Or send a test request from a terminal.',
      },
      identity:
        'Send `X-OpenViking-Session` with an id that stays the same within one conversation, such as your chat id. Without it, the gateway matches conversations by their history, and retries or identical chats may start a new conversation.',
      notes: {
        streaming:
          "When streaming, request usage with `stream_options.include_usage`. Otherwise streamed replies carry no token counts, so Requests can't show them and the gateway can't tell when a long conversation is about to fill the context window.",
        otherApis:
          'SDKs for the other APIs work the same way: point the Anthropic SDK at the address without `/v1`, and send the full history with `store: false` in every Responses request. The upstream must speak the same API as the SDK.',
        models:
          "When a client lists models, the gateway answers with the models and aliases configured on the key's upstreams.",
      },
    },
    'open-webui': {
      name: 'Open WebUI',
      intro:
        'Self-hosted chat interface. Add the gateway as an OpenAI-compatible connection.',
      steps: {
        connection:
          'In Admin Panel → Settings → Connections, add an OpenAI-compatible connection with this URL and key.',
        headers:
          'Add these custom headers to the connection, so each chat is its own conversation and background tasks are recognized.',
        env: "Set this in Open WebUI's environment. Content retrieved from attached files then goes into the system message instead of rewriting your message, which keeps the history stable between requests.",
      },
      identity:
        'The `X-OpenViking-Session` header gives every chat its own conversation.',
      notes: {
        sharedMemory:
          'One connection key is one memory owner: every Open WebUI user who chats through this connection reads and writes the memory of the OpenViking user behind the key.',
        tasks:
          "The gateway recognizes title and summary requests. If Requests shows other background tasks (tags, follow-up suggestions) as “{{kind}}”, set Open WebUI's task model to a connection that doesn't go through the gateway.",
      },
    },
    opencode: {
      name: 'OpenCode',
      intro:
        'Open-source coding agent for the terminal. Add the gateway as a custom provider, using the protocol your upstream speaks.',
      steps: {
        config:
          'Add the provider to `~/.config/opencode/opencode.json`, merged with any settings already there.',
        key: 'Put the gateway key in the environment OpenCode runs in.',
        start: 'Start OpenCode and select `openviking/<model>`.',
      },
      identity:
        'OpenCode sends its own session id, so the gateway recognizes each conversation automatically.',
      notes: {
        plugin:
          'If the OpenViking plugin for OpenCode is installed as well, the gateway steps aside for those conversations.',
      },
    },
    pi: {
      name: 'pi',
      intro:
        'Command-line coding agent. Add the gateway as a custom provider, using the protocol your upstream speaks.',
      steps: {
        config:
          "Add the provider to pi's model configuration, `~/.pi/agent/models.json`. `apiKey` reads the gateway key from an environment variable; keep the leading `$`, or pi sends the name itself as the key.",
        key: 'Export that variable before you start pi.',
      },
      identity:
        'Unless pi sends one of the headers listed under “{{section}}”, the gateway matches its conversations by their history.',
      notes: {
        extension:
          "If pi's own OpenViking extension is active, the gateway steps aside for those conversations. Use one or the other.",
      },
    },
    dsh: {
      name: 'DSH',
      intro:
        'DeepSeek Harness, an open-source agent harness. Add the gateway as a custom model provider, using the protocol your upstream speaks.',
      steps: {
        config:
          'Add the provider to the profile configuration. `web` is the profile `dsh web` uses. If the file already has an `llm-pi-ai` entry, add `openviking` under its `providers` instead of a second entry.',
        key: 'Put the gateway key in the environment DSH runs in. `apiKeyEnv` names this variable.',
        start:
          'Start `dsh web` and pick `<model>` from the `openviking` provider in the model picker.',
      },
      identity:
        'Unless DSH sends one of the headers listed under “{{section}}”, the gateway matches its conversations by their history.',
      notes: {
        webUi:
          'You can also declare it in the Web UI under Settings → Models → Add model provider → Custom model API, which writes the same file.',
        oneProtocol:
          'A DSH provider speaks one protocol. To use the gateway over several, declare one provider per protocol.',
        plugin:
          'If the OpenViking plugin for DSH is installed in the same profile, the gateway steps aside for those conversations.',
      },
    },
    ark: {
      name: 'Volcano Engine Ark and BytePlus ModelArk SDKs',
      intro:
        "Clients and SDKs already set up for Volcano Engine Ark or BytePlus ModelArk only need a new address and key. The gateway accepts Ark's own paths as well as the standard `/v1` paths.",
      steps: {
        endpoints:
          'Replace the Ark or ModelArk address with the matching gateway address, and its API key with the gateway key.',
        python: 'With the Volcano Engine Ark Python SDK:',
      },
      identity:
        'Send `X-OpenViking-Session` with an id per conversation, as with other chat clients.',
      notes: {
        routing:
          'The paths only decide which API the client speaks. Requests go to whichever upstream bound to the key speaks that API and serves the model, usually one with Volcano Engine Ark or BytePlus ModelArk as its provider.',
      },
    },
  },
  identity: {
    title: 'How conversations are recognized',
    description:
      'Memory is recalled and saved per conversation. The gateway takes the conversation id from the first of these request headers that is present:',
    fallback:
      'Without any of them, the gateway looks at the latest assistant reply in the history. If exactly one earlier conversation produced it, the request joins that conversation; otherwise it starts a new one. Retries, regenerated answers and identical chats are ambiguous, so send `X-OpenViking-Session` whenever the client lets you set headers.',
    scope:
      'Conversations belong to the OpenViking user behind the key, separately for each API. Two keys of the same user that send the same id share one conversation.',
  },
  passthrough: {
    title: 'Bring your own provider key',
    description:
      "For upstreams whose credentials are set to “{{mode}}”, every request must also carry the client's own provider API key in this header. The gateway key stays where the client normally puts its API key.",
    note: 'The gateway passes that key on to the provider and never logs it.',
    upstreams: 'Upstreams that need it: {{names}}',
    none: 'None of your upstreams needs it right now.',
  },
  plugin: {
    title: 'Using an OpenViking plugin too?',
    description:
      "If a conversation already gets memory from an OpenViking plugin, the gateway steps aside for it: no recall, no saving and no OpenViking tools, so memory isn't added twice. Other conversations are unaffected.",
    detection:
      'The gateway recognizes a plugin by the memory blocks it adds or its OpenViking tools. Integrations can also announce themselves with this header.',
    restart:
      "This lasts for the rest of the conversation. To use the gateway's memory again, start a new conversation without the plugin.",
  },
}

export default connect
