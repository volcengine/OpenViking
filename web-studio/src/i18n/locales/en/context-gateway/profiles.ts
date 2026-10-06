const profiles = {
  description:
    'How conversations use OpenViking memory and tools. Saved changes apply only to conversations that start afterwards.',
  defaultName: 'Default',
  loadFailed: "Couldn't load context profiles",
  actions: {
    new: 'New profile',
    createRecommended: 'Create with recommended settings',
    customize: 'Customize',
    reset: 'Reset to recommended',
  },
  usedBy_one: 'Used by {{count}} key',
  usedBy_other: 'Used by {{count}} keys',
  unused: 'Not used by any key',
  deleteBlocked_one: 'Used by {{count}} key — revoke it first',
  deleteBlocked_other: 'Used by {{count}} keys — revoke them first',
  empty: {
    title: 'No context profiles yet',
    description:
      'Every gateway key needs a context profile. The recommended settings recall memory for each new message, save conversations, summarize long ones and let the model use the read-only OpenViking tools. You can change them at any time.',
  },
  summary: {
    recallOn: 'On · {{tokens}} tokens per message',
    compactionOn: 'Compact at {{percent}}',
    agentWindowsOn: 'Agent-managed windows',
    toolsEnabled_one: '{{count}} tool enabled',
    toolsEnabled_other: '{{count}} tools enabled',
  },
  deleteDialog: {
    title: 'Delete “{{name}}”?',
    description:
      "No gateway key uses this profile. Deleting it can't be undone.",
    confirm: 'Delete profile',
  },
  toast: {
    created: 'Created the context profile “{{name}}”',
    saved: 'Saved “{{name}}”. New conversations will use these settings.',
    deleted: 'Deleted “{{name}}”',
    reset: 'Recommended settings restored. Save to apply them.',
  },
  editor: {
    back: 'Context profiles',
    newTitle: 'New context profile',
    create: 'Create profile',
    banner:
      'Saved changes apply to new conversations. Ongoing conversations keep the settings they started with.',
    copyName: 'Copy of {{name}}',
    sourceMissing:
      'The profile you chose to copy no longer exists, so this one starts from the recommended settings.',
    notFound: {
      title: 'This context profile no longer exists',
      description:
        'It may have been deleted. Go back to the list to choose another one.',
    },
    sections: 'Profile sections',
    unsaved: 'Unsaved changes',
    invalid: 'Fix the highlighted settings to save.',
    needsName: 'Name the profile to save it.',
    unitHint: '{{unit}} ({{value}})',
  },
  name: {
    label: 'Name',
    placeholder: 'e.g. Coding',
    description: 'Shown when you choose a profile for a gateway key.',
  },
  recall: {
    title: 'Recall memory',
    description:
      "Search OpenViking with each new user message and add what's relevant to that message.",
    profile: {
      label: 'User profile at conversation start',
      description:
        'Give the model your OpenViking profile when a conversation starts. This works independently of memory recall.',
    },
    profileMaxTokens: {
      label: 'Opening context budget',
      description:
        'Limit the profile and memory and skill catalogs provided at the start. Catalogs require the read tool. This budget is separate from recall; 0 omits all three.',
    },
    sources: {
      label: 'Sources',
      description: 'What recall searches.',
      memory: 'Memories',
      resource: 'Resources',
      skill: 'Skills',
    },
    maxTokens: {
      label: 'Budget per message',
      description:
        'The most memory added to one message, as estimated by the gateway.',
    },
    sessionMaxTokens: {
      label: 'Budget per context window',
      description:
        'The most memory added within one context window. It starts over each time the conversation is compacted. 0 turns recall off.',
    },
    scoreThreshold: {
      label: 'Relevance threshold',
      description:
        'Only results scoring at least this much, from 0 to 1, are added. Higher means fewer but closer matches.',
    },
    recallTimeout: {
      label: 'Time limit',
      description:
        "If OpenViking doesn't answer in time, the message goes to the model without memory.",
    },
    queryMaxChars: {
      label: 'Query length',
      description:
        'Recall searches with the latest user message, cut to this length.',
    },
    quotas: {
      label: 'Limit by category',
      description:
        'Set how many entries each category can add. Only categories above 0 are searched.',
      categories: {
        events: 'Events',
        entities: 'Entities',
        preferences: 'Preferences',
        experiences: 'Experiences',
        resources: 'Resources',
        skills: 'Skills',
      },
    },
  },
  capture: {
    title: 'Save conversations',
    description:
      'Write finished turns to an OpenViking session so OpenViking can extract memories.',
    idleSeconds: {
      label: 'Save the latest reply after',
      description:
        'A turn is saved as soon as the next message arrives. The latest reply waits this long for a follow-up; then it is saved and OpenViking starts extracting memories.',
    },
    commitTokens: {
      label: 'Commit after',
      description:
        'Commit once this much saved conversation is waiting. OpenViking extracts memories when a commit happens.',
    },
    keepRecentMessages: {
      label: 'Keep recent messages',
      description:
        'Each commit leaves at least this many of the latest messages, in whole turns, in the session.',
    },
  },
  longConversations: {
    title: 'Long conversations',
    description: "Keep long conversations within the model's context window.",
    compaction: {
      label: 'Compaction',
      description:
        "When a conversation nears the model's context window, the same model writes a summary of it, and the summary replaces the earlier messages.",
    },
    threshold: {
      label: 'Compact at',
      description:
        'The share of the context window, from 0.5 to 0.98, that triggers compaction.',
    },
    summaryMaxTokens: {
      label: 'Summary length',
      description: 'The most tokens the model may write for one summary.',
    },
    contextWindow: {
      label: 'Default context window',
      description:
        "Used when the upstream doesn't list this model's window. Without either, the gateway assumes 1,000,000 tokens, so set it for models with a smaller window.",
    },
    agentWindows: {
      label: 'Agent-managed context windows',
      experimental: 'Experimental',
      description:
        'Give the model two tools to start a fresh context window with its own hand-off notes, and tell it how full the window is. Compaction, when on, remains the fallback. Works only in conversations that get OpenViking tools.',
      needsTools:
        'OpenViking tools are off in this profile, so this setting has no effect.',
    },
    softRatio: {
      label: 'Soft reminder at',
      description:
        'The share of the context window at which the model is reminded to start a new window at a natural break. Sent once per window.',
    },
    hardRatio: {
      label: 'Hard reminder at',
      description:
        'The share of the context window at which the model is told to start a new window now. Sent once per window; must be above the soft reminder.',
    },
  },
  tools: {
    title: 'OpenViking tools',
    description:
      'Let the model use OpenViking tools while it answers. Supports Chat Completions, Responses and Anthropic Messages with full conversation history.',
    available: {
      label: 'Tools',
      description:
        'The recommended settings select the read-only tools and leave the ones that change data unchecked. Tools added in the future are selected automatically. The upstream must allow OpenViking tools as well.',
    },
    empty: 'Issue a gateway key to load the tool list.',
    loadFailed: "Couldn't load OpenViking tools",
    readOnly: 'Read only',
    modifiesData: 'Modifies data',
    executionNotice:
      "The gateway runs the selected tools without the client's permission prompts, and some of them change or delete data. Show tool calls reports each call in the reply; it never asks for approval first.",
    showCalls: {
      label: 'Show tool calls',
      description:
        'Add a one-line notice to the reply each time the gateway runs an OpenViking tool, so users can see the call. The model never sees these lines, and they are not saved to OpenViking.',
    },
    maxRounds: {
      label: 'Rounds per request',
      description:
        "After this many tool rounds, further OpenViking calls are refused; the model continues with the results it has and the client's own tools.",
    },
    timeoutSeconds: {
      label: 'Time limit per call',
      description: 'A call that takes longer returns an error to the model.',
    },
    resultBytes: {
      label: 'Result size',
      description: 'Longer results are cut off before the model sees them.',
    },
    totalSeconds: {
      label: 'Total time',
      description:
        'If the model is still using tools after this long, the request fails.',
    },
    totalTokens: {
      label: 'Token budget',
      description:
        "Estimated tokens that tool calls and results may add to one request. Once used up, further OpenViking calls are refused; the model continues with the results it has and the client's own tools.",
    },
  },
}

export default profiles
