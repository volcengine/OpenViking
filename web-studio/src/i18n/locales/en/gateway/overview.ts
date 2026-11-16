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
    providedUpstreams_one: '{{count}} upstream provided by your gateway admin',
    providedUpstreams_other:
      '{{count}} upstreams provided by your gateway admin',
    importedUpstreams_one:
      "{{count}} upstream, imported from OpenViking's model settings",
    importedUpstreams_other:
      "{{count}} upstreams, imported from OpenViking's model settings",
    someImportedUpstreams:
      "{{count}} upstreams, {{imported}} of them imported from OpenViking's model settings",
    waitUpstreams: 'Waiting for your gateway admin to add an upstream',
    providedProfiles_one: '{{count}} profile provided by your gateway admin',
    providedProfiles_other: '{{count}} profiles provided by your gateway admin',
    waitProfiles: 'Waiting for your gateway admin to add a profile',
    codingPlan:
      '{{name}} is a Coding Plan upstream. Requests to it are blocked until you confirm the plan allows it.',
    confirm: 'Review',

    title: 'Get started',
    progress: '{{done}} of {{total}} done',
    done: 'Done',
    view: 'View',
    upstream: {
      title: 'Add an upstream',
      description:
        'The model service the gateway forwards to, such as OpenAI or Volcano Engine Ark.',
      done_one: '{{count}} upstream added',
      done_other: '{{count}} upstreams added',
      action: 'Add upstream',
      manual: 'Add manually',
    },
    profile: {
      title: 'Create a context profile',
      description:
        'How memory is added to new messages and how conversations are saved.',
      done_one: '{{count}} context profile ready',
      done_other: '{{count}} context profiles ready',
      action: 'Use recommended',
    },
    key: {
      title: 'Issue a gateway key',
      description: 'Clients call the gateway with this key.',
      blocked: 'Available once the first two steps are done.',
      name: 'My key',
      waiting:
        'You can issue a key once your gateway admin adds an upstream and a profile.',
      done_one: '{{count}} key issued',
      done_other: '{{count}} keys issued',
      action: 'Issue key',
    },
    connect: {
      title: 'Connect a client',
      description:
        'Point a client at the gateway address with the key, then send a message.',
      action: 'Open setup guides',
    },
  },
  quick: {
    title: 'Quick mode',
    description:
      'Skip the steps below and get a gateway key that works right away.',
    upstreamImport:
      "Upstream: import the OpenViking server's model settings, reusing its API key and quota",
    upstreamUse: 'Upstream: use {{names}} ({{models}}, {{protocol}})',
    profileCreate:
      'Context profile: create “{{name}}” with the recommended settings',
    profileUse: 'Context profile: use “{{name}}”',
    keySelf: 'Key: issued for {{user}}',
    keyPick: 'Key: choose the OpenViking user who owns it in the next step',
    action: 'Get a key',
    running: 'Generating…',
    retry: 'Try again',
    locked:
      'Creating a context profile or importing models needs the gateway management key.',
    noUsable:
      "OpenViking's model settings have no model the gateway can relay directly. Fix the issues listed below, or add an upstream yourself in step 1.",
    keyName: 'Quick mode',
    divider: 'Or set up step by step',
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
  cacheMiss: {
    title_one: '{{count}} long conversation keeps missing the prompt cache',
    title_other: '{{count}} long conversations keep missing the prompt cache',
    description_one:
      'Its last {{streak}} model calls each sent at least {{tokens}} input tokens and got a 0% cache hit rate. This usually means the start of the request (system prompt or tool definitions) changes every time, or the upstream spreads requests across different accounts or channels.',
    description_other:
      'Their last {{streak}} model calls each sent at least {{tokens}} input tokens and got a 0% cache hit rate. This usually means the start of the request (system prompt or tool definitions) changes every time, or the upstream spreads requests across different accounts or channels.',
    action:
      "Compare with your provider's cache records. If they show hits, the upstream isn't passing cache usage back to the gateway. If not, check the system prompt for timestamps or other content that changes on every request, and make sure your relay isn't rotating between channels.",
    noWrites:
      "The upstream didn't write the cache either, so the client most likely doesn't set cache_control. Compare with your provider's cache records: hits there mean the upstream isn't passing cache usage back to the gateway.",
    streak_one: '{{count}} miss in a row',
    streak_other: '{{count}} misses in a row',
  },
  cacheUnreported: {
    title_one: "{{upstreams}} hasn't reported any cache hits lately",
    title_other: "{{upstreams}} haven't reported any cache hits lately",
    description_one:
      'Long conversations through it keep showing a 0% cache hit rate, and they count as misses in the stats. The provider may not support prompt caching, the client may not enable it, or a relay may not pass cache usage back to the gateway.',
    description_other:
      'Long conversations through them keep showing a 0% cache hit rate, and they count as misses in the stats. The providers may not support prompt caching, the client may not enable it, or a relay may not pass cache usage back to the gateway.',
    separator: ', ',
  },
  openviking: {
    title: 'OpenViking',
    description:
      'Finds memory for new messages and stores saved conversations.',
    version: 'Version',
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
