import type { ReactNode } from 'react';
import type { VikingBotInteractionProps } from '../interaction/types.js';

export interface VikingBotConfirmationRow {
  label: ReactNode;
  value: ReactNode;
}

export interface VikingBotConfirmationLabels {
  title: string;
  edit: string;
  confirm: string;
  submitFailed: string;
}

/** A host-provided summary; the host owns validation, editing and submission. */
export interface VikingBotConfirmationProps extends Pick<
  VikingBotInteractionProps,
  | 'locale'
  | 'requestKey'
  | 'className'
  | 'disabled'
  | 'submitting'
  | 'error'
  | 'slots'
> {
  rows: readonly VikingBotConfirmationRow[];
  title?: ReactNode;
  onEdit?: () => void;
  onConfirm?: () => void | Promise<void>;
  labels?: Partial<VikingBotConfirmationLabels>;
}
