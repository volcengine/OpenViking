import type { DirectoryTourTarget } from '../types.js';

export type DirectoryTourStepMessages = Record<
  DirectoryTourTarget,
  { title: string; description: string }
>;
