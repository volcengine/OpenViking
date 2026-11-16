import type { ReactNode } from 'react';
import type {
  VikingBotInteractionAnswer,
  VikingBotScheduleValue,
} from './types.js';

export interface VikingBotInteractionFieldContext<Value> {
  value: Value;
  onChange: (value: Value) => void;
  disabled: boolean;
  error?: ReactNode;
  controlProps: {
    id: string;
    'aria-labelledby'?: string;
    'aria-describedby'?: string;
    'aria-invalid'?: true;
  };
}

export interface VikingBotInteractionField<Value> {
  label?: ReactNode;
  description?: ReactNode;
  /** Runs after built-in checks. Return a message to reject this field. */
  validate?: (
    value: Value,
    answer: VikingBotInteractionAnswer,
    context: { signal: AbortSignal },
  ) => ReactNode | Promise<ReactNode>;
  /** Replaces the control; the library retains its label, error and help text. */
  render?: (context: VikingBotInteractionFieldContext<Value>) => ReactNode;
}

/** Names follow the public answer: value, or value.mode/time/weekday. */
export interface VikingBotInteractionFields {
  value?: VikingBotInteractionField<string>;
  mode?: VikingBotInteractionField<VikingBotScheduleValue['mode']>;
  time?: VikingBotInteractionField<string>;
  weekday?: VikingBotInteractionField<1 | 2 | 3 | 4 | 5 | 6 | 7>;
}
