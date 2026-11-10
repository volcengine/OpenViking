export default {
  simple: {
    self: 'Current User memory',
    selfHint: 'self.enabled · User information and Agent experience',
    peer: 'Peer memory',
    peerHint: 'peer.enabled · Separate memories per participant',
    working_memory: 'Session summaries',
    working_memoryHint: 'working_memory.enabled · Generate archive summaries',
    typesHint:
      'memory_types · Shared by User and Peer. Agent experience types are linked; Peer excludes cases. The server validates type availability.',
    unlimited: 'No type restriction (omit memory_types)',
    defaults: 'Select default types',
    clear: 'Clear',
  },
  title: 'Memory policy',
  choose: 'Choose a memory extraction policy',
  back: 'Back',
  change: 'Change',
  select: 'Select',
  selected: 'Selected',
  enabled: 'Enabled',
  disabled: 'Disabled',
  userMemory: 'User memory extraction',
  agentMemory: 'Agent memory extraction',
  preview: 'User memory directory preview',
  previewHint:
    'Illustrative paths. Files are created when memories are extracted.',
  edit: 'Edit memory policy',
  editUser: 'Edit memory policy for {{user}}',
  saved: 'Memory policy updated',
  saveFailed: 'Failed to save memory policy',
  retry: 'Failed to load · Retry',
  saveHint:
    'Selecting a policy saves it immediately. Existing memories are kept.',
  custom: {
    userDescription:
      'Choose user profiles, preferences, events and entities to remember.',
    peerLabel: 'Peer memory extraction',
    peerDescription:
      'Keep separate memories for each participant using the same type selection as user memory and Agent experience above. Your app must identify participants using peer_id. Cases are excluded.',
    advancedSettings: 'Advanced settings',
    selfStorage: 'Save to the current User',
    selfStorageHint:
      'Controls whether selected user memories, Agent experience and other types accumulate under the current User. Peer memories can still be saved separately when enabled.',
    otherTypes: 'Other memory types',
    serverManaged:
      'Uses types enabled on the server. Switch to Specific types to choose individual types.',

    save: 'Save changes',
    extraHint:
      'Enter a type identifier already configured by your administrator. This adds it to the extraction scope; it does not create a new memory type.',
    advanced: 'Advanced: reference extension types',
    allHint:
      'Use types enabled on the server now and in the future. No individual selection is needed.',
    specified: 'Specific types',
    typesTitle: 'Long-term memory types',
    workingHint:
      'Summarize the conversation when the session is committed and archived, making it easier to review later. Turning this off skips the summary; long-term memories can still be extracted according to the settings above.',
    workingEnable: 'Generate session summaries',
    peerHint:
      'When one User serves multiple people, keep memories for each person, such as individual customer preferences. Your app must identify participants in messages using peer_id.',
    selfHint:
      'Accumulate memories across sessions, such as your preferences for a personal assistant or execution experience for a task agent.',
    scopeTitle: 'Who to keep long-term memories for',
    configurable: 'Configurable',
    name: 'Custom policy',
    edit: 'Customize memory policy',
    self: 'Build memories for this User',
    peer: 'Keep separate memories for each participant',
    working_memory: 'Session summaries',
    all: 'All enabled types',
    availability:
      'Built-in types are shown as a reference. The server validates availability when saving. Existing extension types are preserved; additional registered types can be entered below.',
    defaultOff: 'Disabled by default',
    linked:
      'cases, trajectories and experiences are selected together. Peer extraction excludes cases.',
    empty:
      'No types selected: long-term memory extraction is disabled. Working memory uses the switch above.',
    extra: 'Registered extension type name',
    add: 'Add to selection',
    apply: 'Use this policy',
    description: 'Extract memories using custom scopes and types.',
    examples: 'Configure current User, Peer and working memory independently.',
  },
  general: {
    name: 'General policy',
    description:
      'Extract user memories and Agent experience. Each Session can override the extraction scope. Peer memory extraction is enabled.',
    examples: 'Examples: personal coding assistants, group chat agents',
  },
  personal: {
    name: 'Personal memory',
    description:
      'Accumulate a person’s profile, preferences, events and entities. Agent experience and Peer extraction are disabled.',
    examples: 'Examples: personal assistants, long-term companions',
  },
  agent: {
    name: 'Agent experience',
    description:
      'Accumulate reusable cases, trajectories and experiences across tasks. Personal and Peer memory extraction are disabled.',
    examples: 'Examples: task execution agents, coding agents',
  },
  directories: {
    identity: 'Agent identity',
    soul: 'Agent values and boundaries',
    skills: 'Skill usage memory',
    tools: 'Tool usage memory',
    profile: 'User profile',
    preferences: 'Preferences and habits',
    events: 'Past events',
    entities: 'People, things and concepts',
    cases: 'Training and evaluation cases',
    trajectories: 'Execution steps and results',
    experiences: 'Reusable Agent experience',
  },
} as const
