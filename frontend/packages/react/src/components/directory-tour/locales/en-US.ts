import type { DirectoryTourLabels } from '../types.js';

const messages: Required<DirectoryTourLabels> = {
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
