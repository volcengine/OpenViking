import type { DirectoryTourStep, DirectoryTourTarget } from './types.js';

/** Optional English preset; pass translated steps and illustrations in the host. */
export const openVikingDirectoryTourSteps: readonly (DirectoryTourStep & {
  target: DirectoryTourTarget;
})[] = [
  {
    id: 'memories',
    target: 'userMemories',
    title: 'User memories (memories)',
    description:
      'Long-term memories extracted from conversations: your identity, preferences and decisions.',
  },
  {
    id: 'sessions',
    target: 'userSessions',
    title: 'Conversations (sessions)',
    description:
      'Conversations and task records. Return to the original context to understand an Agent’s decisions.',
  },
  {
    id: 'personal-resources',
    target: 'userResources',
    title: 'Personal resources (resources)',
    description: 'Documents, notes and knowledge you import for your own use.',
  },
  {
    id: 'shared-resources',
    target: 'sharedResources',
    title: 'Team resources (resources)',
    description:
      'Shared documents, conventions and past projects available to the team.',
  },
  {
    id: 'experiences',
    target: 'userExperiences',
    title: 'Agent experiences (experiences)',
    description:
      'Successful tasks become reusable experience for similar problems.',
  },
  {
    id: 'peers',
    target: 'userPeers',
    title: 'Object memories (peers)',
    description:
      'Memories specific to a repository, Agent or colleague stay separate. General preferences belong in memories.',
  },
];
