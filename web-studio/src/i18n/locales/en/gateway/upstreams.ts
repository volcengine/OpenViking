const upstreams = {
  description:
    'Model providers the gateway forwards requests to. Each upstream speaks one protocol; requests are never converted.',
  add: 'Add upstream',
  loadFailed: "Couldn't load upstreams",
  empty: {
    title: 'No upstreams yet',
    description:
      'Add the model provider your clients should reach, such as OpenAI, Anthropic, DeepSeek or Volcano Engine Ark. Each gateway key then chooses which upstreams it may use.',
  },
  columns: {
    name: 'Name',
    protocol: 'Protocol',
    provider: 'Provider',
    models: 'Models',
    credentials: 'Credentials',
    priority: 'Priority',
    usedBy: 'Used by',
    enabled: 'Enabled',
    actions: 'Actions',
  },
  models: {
    any: 'Any model',
    more: '+{{count}}',
    aliases_one: '{{count}} alias',
    aliases_other: '{{count}} aliases',
  },
  credentials: {
    keyMissing: 'Key missing',
    keyMissingHint:
      'No API key is stored, so every request to this upstream fails. Add the key in the editor.',
    blocked: 'Requests blocked',
    blockedHint:
      'Marked as a Coding Plan or subscription key, so the gateway rejects every request to this upstream.',
  },
  usedBy_one: '{{count}} key',
  usedBy_other: '{{count}} keys',
  notUsed: 'No keys',
  toggle: {
    enable: 'Turn on {{name}}',
    disable: 'Turn off {{name}}',
  },
  test: {
    action: 'Test',
    connection: 'Test connection',
    running: 'Testing…',
    hint: 'Asks the provider for its model list with the stored key and headers. This checks the address and the key, not the models or chat requests.',
    savedOnly: 'Tests the saved settings. Save first to test your changes.',
    passthrough:
      'Each client sends its own key, so there is no stored key to test with.',
    noKey: 'Add an API key first.',
    result: {
      ok: 'Reachable · {{status}}',
      failed: 'Failed · {{status}}',
      unreachable: 'Unreachable',
      error: "Couldn't test",
    },
    explain: {
      ok: 'The provider accepted the key and returned its model list.',
      auth: 'The provider rejected the API key or one of the headers.',
      notFound:
        "There is no model list at this address. Check the base URL; if the provider doesn't offer a model list, real requests may still work.",
      status: 'The provider answered with HTTP {{status}}.',
      unreachable:
        "The gateway couldn't connect within 10 seconds. Check the base URL and the network between the gateway and the provider.",
    },
  },
  delete: {
    action: 'Delete',
    blocked_one: 'Used by {{count}} key — revoke it first',
    blocked_other: 'Used by {{count}} keys — revoke them first',
    title: 'Delete “{{name}}”?',
    description:
      'The upstream is deleted together with its stored API key and headers. No gateway key uses it, so no client is affected.',
    confirm: 'Delete upstream',
  },
  toast: {
    created: 'Added “{{name}}”',
    saved: 'Saved “{{name}}”',
    deleted: 'Deleted “{{name}}”',
  },
  editor: {
    back: 'Upstreams',
    newTitle: 'New upstream',
    newDescription:
      'Connect one model provider endpoint. When you issue a gateway key, you choose which upstreams it may use.',
    editDescription:
      'Changes apply as soon as you save. Priority only affects conversations that start afterwards.',
    notFound: {
      title: 'This upstream no longer exists',
      description:
        'It may have been deleted. Go back to the list to see the current upstreams.',
    },
    missing: 'Still needed: {{fields}}',
    separator: ', ',
    create: 'Add upstream',
  },
  sections: {
    endpoint: {
      title: 'Endpoint',
      description: 'Where the gateway sends requests and which API it speaks.',
    },
    credentials: {
      title: 'Credentials',
      description: 'How the gateway signs in to the provider.',
    },
    models: {
      title: 'Models',
      description:
        'Which model names this upstream serves, and what they are called at the provider.',
    },
    routing: {
      title: 'Routing',
      description: 'How the gateway chooses between upstreams.',
    },
  },
  form: {
    name: {
      label: 'Name',
      placeholder: 'e.g. OpenAI production',
      description: 'Shown in Studio and when you issue gateway keys.',
    },
    protocol: {
      label: 'Protocol',
      description:
        'Clients must call the gateway with this same API; the gateway does not convert between protocols.',
      unsupported: 'Not offered by {{vendor}}',
      options: {
        anthropic: 'Claude Code and Anthropic SDKs',
        chat: 'Most chat clients and SDKs',
        responses: 'Codex CLI; full history only',
      },
    },
    vendor: {
      label: 'Provider',
      description:
        'Limits the protocols below to the ones this provider offers. For a provider not listed here, or a compatible proxy such as LiteLLM, choose Generic.',
      hints: {
        deepseek:
          'With tools present, DeepSeek needs the reasoning of every earlier reply. The gateway restores what clients drop, so OpenViking tools work; if you turn that off below, they are only offered when a request turns thinking off.',
        ark: "Enter the Ark address without a path, such as https://ark.cn-beijing.volces.com. The gateway adds /api/v3 or /api/compatible/v1 for each protocol and keeps each conversation's prompt_cache_key stable so the cache keeps hitting.",
        byteplus:
          "Enter the ModelArk address without a path, such as https://ark.ap-southeast.bytepluses.com. The gateway adds /api/v3 or /api/compatible/v1 for each protocol and keeps each conversation's prompt_cache_key stable so the cache keeps hitting.",
      },
    },
    baseUrl: {
      label: 'Base URL',
      description:
        "The provider's API address, filled in when you choose a provider. It works with or without a trailing /v1.",
      preview: 'Requests go to',
      useDefault: 'Use default',
    },
    authMode: {
      label: 'Who provides the API key',
      managed: {
        title: 'The gateway holds the API key',
        description: 'Clients only need their gateway key.',
      },
      passthrough: {
        title: 'Each client sends its own key',
        description:
          'Besides the gateway key, each client sends its own provider key in the {{header}} header.',
      },
    },
    apiKey: {
      label: 'API key',
      placeholder: 'Paste the provider API key',
      stored: 'Key stored',
      description: 'Stored encrypted and never shown again.',
      storedDescription:
        'Stored encrypted. Leave blank to keep it, or paste a new key to replace it.',
    },
    headers: {
      label: 'Extra headers',
      description:
        'Sent with every request to the provider. Values are stored encrypted and never shown: leave a stored value blank to keep it, or remove the row to delete the header.',
      name: 'Header name',
      value: 'Value',
      add: 'Add header',
    },
    codingPlan: {
      label: 'This key is a Coding Plan or subscription key',
      description:
        "Subscription keys are usually licensed for the provider's own tools only.",
      warning:
        'While this is checked, the gateway rejects every request to this upstream. Use a model API key instead, or allow it if your plan permits use from other clients.',
      allow: 'Allow it anyway',
      allowDescription:
        'Requests are forwarded with this key. Make sure your plan allows it.',
    },
    models: {
      label: 'Models',
      placeholder: 'Type a model name and press Enter',
      description:
        'Model names clients can request. Leave empty to accept any model name.',
    },
    aliases: {
      label: 'Model aliases',
      description:
        'Lets clients use another name for a model. The gateway swaps in the upstream model before forwarding.',
      name: 'Name clients use',
      target: 'Model sent to the upstream',
      add: 'Add alias',
    },
    contextWindows: {
      label: 'Context windows',
      description:
        "Long conversations are compacted near the model's context window. A model not listed here uses the context profile's default window, or 1,000,000 tokens when that isn't set either. Use the model name sent to the upstream, after aliases.",
      model: 'Upstream model',
      tokens: 'Tokens',
      add: 'Add window',
    },
    priority: {
      label: 'Priority',
      description:
        'When several upstreams serve the same model, the highest priority wins for new conversations.',
    },
    enabled: {
      label: 'Enabled',
      description:
        "A turned-off upstream receives no requests. Conversations on it move to another upstream that serves the model, if the client's key allows one.",
    },
    gatewayTools: {
      label: 'Allow OpenViking tools',
      description:
        'Context profiles with OpenViking tools can offer them through this upstream. Turning this off also stops them in ongoing conversations.',
    },
    replayReasoning: {
      label: 'Restore reasoning the client drops',
      description:
        "Sends each reply's reasoning back with later requests when the client leaves it out. DeepSeek rejects tool requests without it, so this is on by default for DeepSeek, Ark and BytePlus.",
    },
    cacheMinTokens: {
      label: 'Minimum cacheable prompt',
      description:
        "Used only to report cache eligibility in Requests. Set it to the model's minimum cacheable prompt length.",
    },
  },
}

export default upstreams
