const overview = {
  description:
    'How clients use the gateway: requests, prompt caching and memory, and the health of OpenViking.',
  loadFailed: "Couldn't load the overview",
  showIssues: 'Show in Requests',
  sampleNote_one:
    'Figures cover the latest {{limit}} entries of the request log. Entries are kept for {{count}} day.',
  sampleNote_other:
    'Figures cover the latest {{limit}} entries of the request log. Entries are kept for {{count}} days.',
  setup: {
    title: 'Get started',
    description:
      'Four steps to the first conversation with memory. This card goes away once a client sends its first request.',
    progress: '{{done}} of {{total}} done',
    done: 'Done',
    view: 'View',
    upstream: {
      title: 'Add an upstream',
      description:
        'The model provider the gateway forwards requests to, such as Anthropic, OpenAI or Volcano Engine Ark.',
      done_one: '{{count}} upstream added',
      done_other: '{{count}} upstreams added',
      action: 'Add upstream',
    },
    profile: {
      title: 'Create a context profile',
      description:
        'Decides how memory is added to new messages and how conversations are saved. The recommended settings suit most clients.',
      done_one: '{{count}} context profile ready',
      done_other: '{{count}} context profiles ready',
    },
    key: {
      title: 'Issue a gateway key',
      description:
        'Clients use this key to call the gateway. It belongs to one OpenViking user, whose memory the gateway reads and writes.',
      blocked: 'Add an upstream and a context profile first.',
      done_one: '{{count}} key issued',
      done_other: '{{count}} keys issued',
      action: 'Issue key',
    },
    connect: {
      title: 'Connect a client',
      description:
        'Point Claude Code, Codex CLI or a chat app at the gateway address with the key, then send a message.',
      action: 'Open setup guides',
    },
  },
  kpi: {
    requests: {
      label: 'Requests',
      hint: 'Model requests that went through the gateway. Memory sync events are not counted.',
      last: 'Last request {{time}}',
      outputTokens: '{{tokens}} output tokens',
      none: 'No requests yet',
    },
    firstCall: {
      label: 'First-call cache hit rate',
      hint: "Share of input tokens the provider served from its prompt cache on the first model call of each new user message. A healthy gateway keeps this close to what the provider reaches without it; a drop usually means memory added to earlier messages isn't sent back unchanged.",
      footnote_one: '{{number}} new message',
      footnote_other: '{{number}} new messages',
    },
    continuation: {
      label: 'Within-turn cache hit rate',
      hint: 'Share of input tokens served from the prompt cache on the model calls that continue a turn, such as after a tool result, including calls made while the model uses OpenViking tools.',
      footnote_one: '{{number}} tool step',
      footnote_other: '{{number}} tool steps',
    },
    recall: {
      label: 'Memory entries recalled',
      hint: 'Memory entries the gateway added to new messages. The average is how long a search in OpenViking took, counting only messages that searched.',
      footnote_one: 'Avg {{duration}} over {{number}} search',
      footnote_other: 'Avg {{duration}} over {{number}} searches',
      none: 'No recalls yet',
    },
  },
  openviking: {
    title: 'OpenViking',
    description:
      'Finds memory for new messages and stores saved conversations.',
    version: 'Version',
    authMode: 'Authentication',
    authModes: {
      api_key: 'API key mode',
      trusted: 'Trusted mode',
      dev: 'Development mode',
      oidc: 'OIDC mode',
      ldap: 'LDAP mode',
    },
    unreachable:
      "While OpenViking can't be reached, requests still go to the model without memory, and saving conversations is retried until it's back.",
    starting:
      "The gateway hasn't finished its first check of OpenViking yet. Refresh in a minute.",
  },
  saving: {
    title: 'Saving conversations',
    ok: 'No saving problems in recent requests.',
    retrying_one:
      '{{count}} conversation is retrying saving to OpenViking after a failure.',
    retrying_other:
      '{{count}} conversations are retrying saving to OpenViking after a failure.',
    paused_one:
      '{{count}} conversation paused saving after repeated failures. The gateway tries again every 5 minutes.',
    paused_other:
      '{{count}} conversations paused saving after repeated failures. The gateway tries again every 5 minutes.',
  },
  degraded: {
    title: 'Degraded requests',
    description:
      'Requests where the gateway had to skip or change part of its memory handling.',
    suggestion: 'Suggested action:',
    empty: {
      title: 'No degraded requests',
      description:
        'Every request in this sample got its usual memory handling.',
    },
  },
  recent: {
    title: 'Recent requests',
    columns: {
      time: 'Time',
      type: 'Type',
      model: 'Model',
      status: 'Status',
      memory: 'Memory',
    },
    empty: {
      title: 'No requests yet',
      description:
        'Requests show up here as soon as a client sends one through the gateway.',
    },
  },
}

export default overview
