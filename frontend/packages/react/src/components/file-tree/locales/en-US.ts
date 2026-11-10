import type { ResolvedLabels } from '../file-tree-row-context.js';

const messages = {
  search: 'Search files…',
  refresh: 'Refresh file tree',
  empty: 'No files yet',
  error: 'Failed to load',
  retry: 'Retry',
  loadMore: 'Load more',
  loading: 'Loading…',
  expand: 'Expand {name}',
  collapse: 'Collapse {name}',
  info: {
    description: {
      userRoot: 'Context data for the current user',
      memories: 'Long-term memory accumulated across sessions',
      peers: 'Context isolated per application or workspace',
      privacy: 'Keys and other sensitive configuration',
      userResources: 'Private knowledge of this user',
      sessions: 'Conversation and task execution records',
      skills: 'Skills and tools available to the agent',
      profile: 'User identity and background',
      preferences: 'Personalized user preferences',
      entities: 'People, projects, organizations and other entities',
      events: 'Key events archived by date',
      identity: 'Agent name and persona',
      soul: 'Agent behaviour guidelines and boundaries',
      cases: 'Interaction cases used to train experience',
      trajectories: 'Reusable task execution trajectories',
      experiences: 'Execution experience distilled automatically',
      sharedResources: 'External resources the agent can reference',
      agentRoot: 'Agent capabilities, tools and experience',
      agentEndpoints: 'Communication endpoints shared across the account',
      agentPayments: 'Payment capabilities shared across the account',
      agentSkills: 'Skill definitions callable across the account',
      agentTools: 'Tool configuration shared across the account',
      memoryRoot: 'Long-term memory and accumulated information',
      resourceRoot: 'External knowledge the agent can reference',
      sessionRoot: 'Raw session records of the user',
      systemRoot: 'Built-in system resources',
    },
    triggerBehavior: {
      userRoot:
        'Created from the current user identity; no manual setup needed',
      memories:
        'Committed sessions, explicit requests to remember, or continuous extraction from sessions',
      peers:
        'Created when connecting as a specific peer, isolating memory and material per peer_id',
      userResources:
        'Imported material, uploaded to this user’s private resource directory',
      sessions:
        'Created when a session calls OpenViking; messages, tool calls and archives are saved continuously',
      skills: 'Created, installed or imported as a personal skill',
      profile:
        'A stable, explicit user background or working style appears in conversation',
      preferences:
        'The user explicitly states a specific preference, habit or style',
      entities:
        'A person, project or organization worth keeping appears in conversation',
      events:
        'An important decision, commitment, arrangement, progress or result appears in dialogue',
      identity:
        'Initialized on the first memory extraction run; later updated by write operations',
      soul: 'Initialized on the first memory extraction run; later adjusted by write operations',
      cases:
        'A session with a clear task and an evaluable execution result is committed',
      trajectories:
        'A task containing decisions, tool calls or multi-step execution is committed',
      experiences: 'Distilled from task cases and execution trajectories',
      sharedResources: 'Imported material, uploaded to a shared resource space',
      agentEndpoints:
        'Configured when an integration endpoint is registered for the account',
      agentPayments:
        'Configured when a payment capability is enabled for the account',
      agentSkills:
        'Installed or imported as a shared skill available to every agent',
      agentTools: 'Configured when a tool is registered for the account',
    },
    field: {
      description: 'Description',
      triggerBehavior: 'Trigger',
      path: 'Path',
    },
  },
} satisfies Required<ResolvedLabels>;

export default messages;
