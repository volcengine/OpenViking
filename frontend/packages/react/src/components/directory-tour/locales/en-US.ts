import type { DirectoryTourLabels } from '../types.js';

const messages: Required<DirectoryTourLabels> = {
  close: 'Close guide',
  next: 'Next',
  done: 'Got it',
  locating: 'Locating directory…',
  unavailable: 'This directory is unavailable. Retry or skip this step.',
  retry: 'Retry',
  skip: 'Skip',
  progress: (current: number, total: number) => `${current}/${total}`,
};

export default messages;
