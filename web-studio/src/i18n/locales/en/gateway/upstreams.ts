const upstreams = {
  import: {
    action: 'Import from OpenViking',
    useSettings: "Use OpenViking's model settings",
    hint: "Uses the server's VLM settings",
    running: 'Importing…',
    summary_one: 'Imported or updated {{count}} upstream.',
    summary_other: 'Imported or updated {{count}} upstreams.',
    nothing: "OpenViking's model settings have nothing to import.",
    needsConfirmation:
      'These Coding Plan upstreams stay blocked until you confirm the plan allows use through the gateway:',
    skipped_one: '{{count}} credential was not imported:',
    skipped_other: '{{count}} credentials were not imported:',
    confirm: 'Review',
    dismiss: 'Dismiss',
    repeat:
      "Importing again updates addresses and credentials from OpenViking's settings without creating duplicates. Your changes to the enabled state, priority and models are kept.",
    reasons: {
      invalid_configuration:
        'The model configuration is invalid. Check the server settings and import again.',
      openai_codex:
        'Codex OAuth credentials refresh periodically and cannot be used long term by the gateway. Add an upstream manually.',
      litellm_no_api_base:
        'This LiteLLM credential has no api_base. Add api_base to the server configuration and import again.',
      litellm_unsupported_route:
        "This LiteLLM route doesn't speak an OpenAI-compatible protocol, so the gateway can't forward to it. Add an upstream manually.",
      unknown:
        'This credential could not be imported. Check the server model settings.',
    },
  },
  readOnly: {
    empty: 'Waiting for your gateway admin to add an upstream.',
    managedKey: 'Your gateway admin keeps the API key.',
    clientKey: 'Each client sends its own API key.',
    codingAllowed: 'Coding Plan, allowed by your gateway admin',
    codingBlocked: 'Coding Plan, requests blocked',
  },
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
    connection: 'Test',
    running: 'Testing…',
    hint: 'Checks the upstream connection, then lets you test a model.',
    savedOnly: 'Tests the saved settings. Save first to test your changes.',
    passthrough:
      'Each client sends its own key, so there is no stored key to test with.',
    noKey: 'Add an API key first.',
    codingPlan:
      'This Coding Plan has not been approved for model requests through the gateway.',
    result: {
      connected: 'Connected · {{status}}',
      connectionUnverified: 'Connected · {{status}}',
      ok: 'Model works · {{status}}',
      failed: 'Failed · {{status}}',
      unreachable: 'Unreachable',
      error: "Couldn't test",
    },
    explain: {
      connected: 'The provider accepted the key and returned its model list.',
      connectionUnverified:
        'The address is reachable, but the provider does not offer a model list, so the key is not verified. You can still test a model.',
      ok: 'The selected model generated a reply.',
      auth: 'The provider rejected the API key or one of the headers.',
      rateLimited:
        'The provider rate limited the request, so model generation did not complete.',
      requestRejected:
        'The provider rejected the generation request (HTTP {{status}}). Check the model name, protocol and complete endpoint URL.',
      invalidResponse:
        'The provider returned a success status, but its response does not match the selected protocol.',
      status: 'The provider answered with HTTP {{status}}.',
      unreachable:
        "The gateway couldn't connect or the request timed out. Check the base URL and the network between the gateway and the provider.",
    },
    dialog: {
      title: 'Test “{{name}}”',
      description:
        'Checks the saved connection first, then sends one small non-streaming request to test the selected model.',
      connection: 'Upstream connection',
      checkingConnection: 'Checking connection…',
      exactEndpoint:
        'An exact endpoint has no model list to check. Test a model directly.',
      model: 'Model',
      modelPlaceholder: 'Enter a provider model name',
      configuredModel:
        'Choose a configured model or enter another provider model name.',
      unconfiguredModel:
        'This upstream allows any model. Enter a model name supported by the provider.',
      response: 'Upstream response (JSON)',
      run: 'Run test',
    },
  },
  delete: {
    disable: 'Disable',
    disableHint:
      'You can disable it instead; those keys stop reaching it right away.',
    inUse_one: '{{count}} key still uses this upstream.',
    inUse_other: '{{count}} keys still use this upstream.',
    action: 'Delete',
    blocked_one: 'Used by {{count}} key — revoke it first',
    blocked_other: 'Used by {{count}} keys — revoke them first',
    title: 'Delete “{{name}}”?',
    description:
      "The upstream is deleted together with its stored API key and headers. It can't be deleted while keys still use it.",
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
    missing: 'Still to fill in or fix: {{fields}}',
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
        ark: 'For Chat and Responses use https://ark.cn-beijing.volces.com/api/v3; for Anthropic use https://ark.cn-beijing.volces.com/api/compatible. Coding Plan paths are /api/coding/v3 and /api/coding respectively. The gateway keeps each conversation’s prompt_cache_key stable for cache hits.',
        byteplus:
          'For Chat and Responses use https://ark.ap-southeast.bytepluses.com/api/v3; for Anthropic use https://ark.ap-southeast.bytepluses.com/api/compatible. Coding Plan paths are /api/coding/v3 and /api/coding respectively. The gateway keeps each conversation’s prompt_cache_key stable for cache hits.',
      },
    },
    baseUrl: {
      label: 'Base URL',
      description:
        "Use base_url from the provider's SDK documentation. The gateway appends /chat/completions, /responses or /v1/messages. Add separate upstreams when a provider uses different base URLs for different protocols. The URL may include a query; write the key as {api_key}.",
      preview: 'Requests go to',
      missingPath:
        'This provider needs a path in base_url. A domain alone sends requests to the wrong address.',
      anthropicVersion:
        'Anthropic base_url must omit /v1. The gateway appends /v1/messages.',
      removeVersion: 'Remove /v1',
      correctPath: 'Use {{url}}',
      useDefault: 'Use default',
    },
    appendEndpointPath: {
      label: 'Append protocol endpoint path',
      description:
        'Adds the path for the selected protocol to the Base URL. Turn this off when the Base URL is already the complete request endpoint.',
    },
    keyHeader: {
      label: 'Send the key as',
      authorization: 'Authorization: Bearer',
      apiKey: 'api-key header (Azure OpenAI)',
      xApiKey: 'x-api-key header',
      inUrl: 'In the URL ({api_key}); no auth header is sent',
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
      addressChanged:
        'The address has changed. Enter the API key again. The stored API key will not be sent to the new address.',
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
      addressChanged:
        "The address changed, so enter the header values again. Values stored for the old address aren't sent to the new one.",
    },
    codingPlan: {
      label: 'This key is a Coding Plan or subscription key',
      description:
        "Subscription keys are usually licensed for the provider's own tools only.",
      warning:
        'By default the gateway rejects every request to this upstream. Use a model API key instead, or, if your plan permits use outside the provider’s tools, turn on the option below.',
      allow: 'Allow this plan through the gateway',
      allowDescription:
        'Some Coding Plans only authorize use in the provider’s own tools. Check that your plan permits this before enabling it, or the provider may restrict your account.',
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
