const requests = {
  description:
    'Model requests that went through the gateway and what happened to memory in each. Only metadata is kept: no prompts, replies or memory text.',
  filters: {
    label: 'Show',
    all: 'All',
    messages: 'New messages',
    tools: 'Tool steps',
    issues: 'Issues',
    kind: 'Request type',
    allKinds: 'All types',
    search: 'Search model, conversation or key',
    clear: 'Clear filters',
  },
  scope: 'Filters and search cover the latest {{count}} records.',
  refreshFailed: "Couldn't refresh the list: {{message}}",
  loadFailed: "Couldn't load requests",
  empty: {
    title: 'No requests yet',
    description:
      'Requests show up here once a client sends a model request through the gateway.',
    action: 'Connect a client',
  },
  noMatch: {
    title: 'No matching requests',
    description: 'Try another filter or search term.',
  },
  pagination: {
    summary: '{{total}} records · page {{page}} of {{pageCount}}',
  },
  table: {
    time: 'Time',
    type: 'Type',
    model: 'Model',
    status: 'Status',
    tokens: 'Tokens',
    memory: 'Memory',
    saving: 'Saving',
    issue: 'Issue',
    details: 'Details',
  },
  tokens: {
    input: '{{value}} in',
    cached: '{{value}} cached',
    output: '{{value}} out',
  },
  memory: {
    recalled_one: '{{count}} memory entry added in {{duration}}',
    recalled_other: '{{count}} memory entries added in {{duration}}',
    replayed_one:
      'Memory added to {{count}} earlier message stayed in the history',
    replayed_other:
      'Memory added to {{count}} earlier messages stayed in the history',
    recallFailed: 'Recall failed',
  },
  details: {
    show: 'Show details',
    hide: 'Hide details',
    time: 'Time',
    requestId: 'Request ID',
    copyRequestId: 'Copy request ID',
    conversation: 'Conversation',
    copyConversation: 'Copy conversation ID',
    anonymous:
      "The client didn't send a conversation ID, so the gateway recognizes this conversation by its history.",
    protocol: 'Protocol',
    upstream: 'Upstream',
    upstreamDeleted: 'Deleted upstream',
    key: 'Gateway key',
    keyRevoked: 'Revoked key',
    duration: 'Duration',
    durationHint: 'From sending the request to the model until the reply ended',
    tokens: 'Tokens',
    tokenLine:
      'Input {{input}} · cached {{cached}} · cache write {{cacheWrite}} · output {{output}}',
    noUsage: "The upstream didn't report token usage",
    firstCall: 'First model call',
    toolCalls_one: 'After OpenViking tools ({{count}} call)',
    toolCalls_other: 'After OpenViking tools ({{count}} calls)',
    cache: 'Prompt cache',
    cacheEligible: 'Long enough to cache (at least {{min}} tokens)',
    cacheIneligible: 'Too short to cache (needs {{min}} tokens)',
    recall: 'Recall',
    recallResult_one: '{{count}} entry in {{duration}}',
    recallResult_other: '{{count}} entries in {{duration}}',
    context: 'Context window',
    contextUsage: 'About {{tokens}} of {{window}} tokens ({{percent}})',
    compactionApplied: 'Sent with the earlier history replaced',
    compactionWritten:
      'Summary of about {{tokens}} tokens written in {{duration}}',
    compactionFailed:
      'Compaction failed ({{reason}}), so the full history was sent',
    window: 'Window {{number}}, managed by the model',
    windowReset: 'The model started a new window',
    saving: 'Saving to OpenViking',
    nextRetry: 'Next retry at {{time}}',
    tools: 'OpenViking tools',
    toolsSkipped: 'Not offered: {{reason}}',
    toolRounds:
      'Rounds: {{rounds}} · extra model calls: {{calls}} · tokens added: about {{tokens}}',
    whatHappened: 'What happened',
    whatToDo: 'What to do',
    httpError:
      'The upstream answered with HTTP {{status}}, and the client received this error.',
    httpErrorAction:
      "Check the upstream's API key, model name and rate limits, or test the upstream.",
  },
  resync: {
    action: 'Resync conversation…',
    hint: 'Starts saving this conversation again in a new OpenViking session. Use it when saving stays paused.',
    keyRevoked:
      "The gateway key this conversation used has been revoked, so it can't be resynced.",
    title: 'Start a fresh OpenViking session for this conversation?',
    description:
      'The gateway writes the full history to the new session the next time the user sends a message. What was already saved stays in the old session, so OpenViking may extract some memories twice.',
    confirm: 'Resync',
    done: 'The conversation will be saved again with its next message.',
  },
}

export default requests
