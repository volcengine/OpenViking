import type { FileTreeInfoKey, FileTreeInfoLabels } from './types.js';

type InfoCopy = Partial<Record<FileTreeInfoKey, string>>;

/**
 * 内置英文说明。宿主按需逐键覆盖；传空字符串表示该键不显示。
 *
 * Built-in English descriptions. A host overrides them key by key; an empty
 * string hides that key.
 */
const DESCRIPTION: InfoCopy = {
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
};

/**
 * 内置英文触发行为，说明该节点在什么条件下被写入或生成。没有对应说明的键省略。
 *
 * Built-in English trigger copy: when the node is written or generated. Keys
 * without a description are left out.
 */
const TRIGGER_BEHAVIOR: InfoCopy = {
  userRoot: 'Created from the current user identity; no manual setup needed',
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
};

/**
 * 内置英文文案。`FileTreeLabels.info` 未提供的键沿用这里的值。
 *
 * Built-in English copy. Keys missing from `FileTreeLabels.info` fall back here.
 */
export const DEFAULT_INFO_LABELS: FileTreeInfoLabels = {
  description: DESCRIPTION,
  triggerBehavior: TRIGGER_BEHAVIOR,
  field: {
    description: 'Description',
    triggerBehavior: 'Trigger',
    path: 'Path',
  },
};

/**
 * 按宿主的覆盖合并内置文案，键为单位。宿主的空字符串表示该键不显示，因此
 * 合并时保留空串，由渲染方判断。
 *
 * Merges host overrides over the built-in copy, key by key. An empty host string
 * means "hide this key", so it is kept and the renderer decides.
 *
 * Returns the copy of one key, or `undefined` when the key is hidden or has no
 * copy.
 */
export function resolveInfoCopy(
  key: FileTreeInfoKey,
  hostLabels: FileTreeInfoLabels | undefined,
): { description?: string; triggerBehavior?: string } {
  const description =
    hostLabels?.description?.[key] ?? DESCRIPTION[key] ?? undefined;
  const triggerBehavior =
    hostLabels?.triggerBehavior?.[key] ?? TRIGGER_BEHAVIOR[key] ?? undefined;

  return {
    description: description || undefined,
    triggerBehavior: triggerBehavior || undefined,
  };
}
