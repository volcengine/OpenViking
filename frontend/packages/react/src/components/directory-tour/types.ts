import type { CSSProperties, ReactNode } from 'react';
import type { Locale } from '../../shared/locale.js';

export type DirectoryTourTarget =
  | 'userMemories'
  | 'userSessions'
  | 'userResources'
  | 'sharedResources'
  | 'userExperiences'
  | 'userPeers';

export interface DirectoryTourStep {
  id: string;
  target: string;
  title: ReactNode;
  description: ReactNode;
  illustration?: ReactNode;
  nextLabel?: string;
}

export interface DirectoryTourResult {
  skippedStepIds: readonly string[];
}

export interface DirectoryTourLabels {
  close?: string;
  next?: string;
  done?: string;
  locating?: string;
  unavailable?: string;
  retry?: string;
  skip?: string;
  progress?: (current: number, total: number) => string;
}

export interface DirectoryTourProps {
  locale: Locale;
  open: boolean;
  steps: readonly DirectoryTourStep[];
  currentStep?: number;
  defaultStep?: number;
  placement?: 'right' | 'left';
  locateTimeout?: number;
  className?: string;
  style?: CSSProperties;
  /** Host loads/expands the path and resolves only after the row is rendered.
   * Must respect cancellation. No network or business DTO belongs to the tour. */
  locateTarget?: (
    target: string,
    signal: AbortSignal,
  ) => Promise<HTMLElement | null>;
  onStepChange?: (index: number) => void;
  onClose?: () => void;
  onComplete?: (result: DirectoryTourResult) => void;
  labels?: DirectoryTourLabels;
}
