import type { DirectoryTourStepMessages } from './step-messages.js';
import type { DirectoryTourLabels } from '../types.js';

const messages: Required<DirectoryTourLabels> & {
  steps: DirectoryTourStepMessages;
} = {
  steps: {
    userMemories: {
      title: 'User memories (memories)',
      description:
        'Long-term memories your Agent automatically extracts from conversations — your identity, preferences and decisions.',
    },
    userSessions: {
      title: 'Session records (sessions)',
      description:
        'A lasting record of every conversation with your Agent and every task it runs. Return to the original context whenever you need to trace the Agent’s decisions.',
    },
    userResources: {
      title: 'Personal resources (resources)',
      description:
        'Documents, notes and knowledge you import go here. Only you can access them.',
    },
    sharedResources: {
      title: 'Team resources (resources)',
      description:
        'A shared knowledge base for the whole team — documents, conventions and past projects. Your Agent draws on the team’s experience, so you do not have to explain everything from scratch.',
    },
    userExperiences: {
      title: 'Agent experiences (experiences)',
      description:
        'Your Agent automatically turns successful tasks into experience. When a similar problem comes up, it can reuse an existing solution and avoid repeating the same mistakes.',
    },
    userPeers: {
      title: 'Object memories (peers)',
      description:
        'Memories about a specific repository, Agent or colleague stay separate in the peers subdirectory. General preferences and habits belong in memories.',
    },
  },
  close: 'Close guide',
  next: 'Next',
  done: 'Got it',
  locating: 'Locating directory…',
  unavailable: 'This directory could not be found.',
  retry: 'Retry',
  skip: 'Skip',
  progress: (current: number, total: number) => `${current}/${total}`,
};

export default messages;
