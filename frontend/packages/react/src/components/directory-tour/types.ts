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
  /** Standard semantic targets include Figma artwork. Pass null to omit it. */
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
  open: boolean;
  steps: readonly DirectoryTourStep[];
  /** Inherits the nearest language scope, otherwise en-US. */
  locale?: Locale;
  currentStep?: number;
  defaultStep?: number;
  placement?: 'right' | 'left';
  /** Deadline before automatically skipping an unresolved target. Default 10000. */
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
