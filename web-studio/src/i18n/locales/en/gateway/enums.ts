const enums = {
  protocol: {
    anthropic: 'Anthropic Messages',
    chat: 'Chat Completions',
    responses: 'Responses',
  },
  vendor: {
    generic: 'Generic',
    anthropic: 'Anthropic',
    openai: 'OpenAI',
    deepseek: 'DeepSeek',
    ark: 'Volcano Engine Ark',
    byteplus: 'BytePlus ModelArk',
  },
  authMode: {
    managed: 'Managed key',
    passthrough: 'Client sends key',
  },
  kind: {
    user: 'New message',
    continuation: 'Tool step',
    auxiliary: 'Housekeeping',
    subagent: 'Sub-agent',
    count: 'Token count',
    passthrough: 'Forwarded',
    capture: 'Memory sync',
  },
  health: {
    ok: 'Connected',
    degraded: 'Degraded',
    starting: 'Starting',
  },
  captureStatus: {
    active: 'On',
    disabled: 'Off',
    retrying: 'Retrying',
    paused: 'Paused',
  },
  captureReason: {
    history_changed:
      'The conversation history changed, so saving continues in a new OpenViking session',
    continued_after_idle:
      'A saved turn continued after a pause, so saving continues in a new OpenViking session',
    history_changed_during_delivery:
      'The history changed while saving, so saving continues in a new OpenViking session',
    manual_reset: 'Resynced by an administrator',
    delivery_recovered: 'Saving works again',
    delivery_failed: 'Saving to OpenViking failed',
    invalid_archive_id: 'OpenViking returned an unexpected archive',
  },
  recallReason: {
    recalled: 'Memory added',
    empty: 'Nothing relevant found',
    disabled: 'Recall is off or the budget is used up',
    recall_timeout: 'Recall timed out',
  },
  toolSkipReason: {
    tools_require_full_history:
      'Responses requests must send the full conversation and set store: false',
    upstream_tools_disabled:
      'OpenViking tools are turned off for this upstream',
    tools_multiple_choices: 'The request asked for several choices (n > 1)',
    tools_structured_output: 'The request asked for structured output',
    tools_non_function: 'The client sent tools that are not functions',
    tools_forced_choice: 'The client required a specific tool',
    deepseek_reasoning_history_required:
      'DeepSeek thinking is on and earlier reasoning cannot be sent back',
    tool_name_collision: 'The client defines a tool with the same name',
    tools_unavailable:
      "OpenViking's tool list couldn't be loaded when this conversation started",
    tools_not_selected_at_session_start:
      'Tools were not available when this conversation started',
  },
  toolStopReason: {
    token_budget: 'Tool token budget reached',
  },
  windowReminder: {
    soft: 'Reminded the model to start a new window soon',
    hard: 'Told the model to start a new window now',
  },
  openviking: {
    unavailable: "OpenViking is unreachable or didn't answer in time",
    unauthorized: 'OpenViking rejected the key. It may be wrong or revoked.',
    httpStatus: 'OpenViking returned HTTP {{status}}',
    errorCode: 'OpenViking reported error {{code}}',
    versionMismatch: 'OpenViking is older than the gateway requires',
    identityMissing: "OpenViking couldn't tell which user this key belongs to",
    rootKey: "Root keys aren't accepted. Use a user key from this account.",
    otherAccount: 'This OpenViking key belongs to another account.',
  },
  degradation: {
    memory_store_failure: {
      label: 'Memory unavailable',
      explanation:
        "The gateway couldn't prepare memory, so the request went to the model without it.",
      action:
        "Check the gateway's disk and logs. This is expected right after a user's gateway data was deleted.",
    },
    unsafe_json: {
      label: 'Forwarded unchanged',
      explanation:
        "The request had duplicate keys or numbers that can't be re-encoded exactly, so it was forwarded as is, without memory.",
      action: 'Usually caused by the client. Nothing to change in the gateway.',
    },
    upstream_changed: {
      label: 'Upstream switched',
      explanation:
        'The upstream this conversation started on is no longer usable, so another one answered and the provider cache started over.',
      action:
        'Re-enable the original upstream, or give the client a key that includes it. A new conversation settles on the current upstream.',
    },
    missing_injection_record: {
      label: 'Memory record missing',
      explanation:
        'Memory added earlier in this conversation is no longer stored, so the history was sent without it.',
      action:
        'Usually follows storage loss or a very old conversation. Start a new conversation if it keeps happening.',
    },
    plugin_present: {
      label: 'OpenViking plugin in use',
      explanation:
        'The client already uses an OpenViking plugin, so the gateway stopped adding and saving memory for this conversation.',
      action:
        'Expected while the plugin is in use. To use gateway memory instead, remove the plugin and start a new conversation.',
    },
    ark_cache_parameters_changed: {
      label: 'Cache parameters changed',
      explanation:
        "Model, thinking, sampling, system prompt or tools differ from the conversation's first request, so Ark's prompt cache likely missed.",
      action: 'Keep these parameters the same within a conversation.',
    },
    hidden_reply_without_anchor: {
      label: 'No usable model reply',
      explanation:
        'The model used OpenViking tools but returned no content to continue the conversation with. The next request cannot reuse those tool results.',
      action: 'Retry the question or start a new conversation.',
    },
    hidden_tool_history_unavailable: {
      label: 'Tool history unavailable',
      explanation:
        'This Claude conversation used OpenViking tools, but the current request cannot use them. The gateway removed earlier thinking and continues sending the conversation text.',
      action:
        'Check why tools were skipped, restore the original settings or start a new conversation.',
    },
    hidden_tool_loop_failed: {
      label: 'OpenViking tools failed',
      explanation:
        'A model call failed while the model was using OpenViking tools, and the client received an error.',
      action:
        'Check that the upstream supports tool calls, or turn off OpenViking tools for that upstream.',
    },
    capture_parse_failure: {
      label: 'Reply not saved',
      explanation:
        "The streamed reply couldn't be read, so it wasn't saved to OpenViking. The client wasn't affected.",
      action: 'No action needed unless it happens often.',
    },
  },
}

export default enums
