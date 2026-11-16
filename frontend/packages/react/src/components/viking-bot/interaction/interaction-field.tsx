import type { ReactNode } from 'react';
import { FormField } from '../../../shared/primitives/form-field/form-field.js';
import type {
  VikingBotInteractionField,
  VikingBotInteractionFieldContext,
} from './field-types.js';

export function InteractionField<Value>({
  config,
  value,
  onChange,
  disabled,
  children,
  ...props
}: {
  config?: VikingBotInteractionField<Value>;
  value: Value;
  onChange: (value: Value) => void;
  disabled: boolean;
  id: string;
  label?: ReactNode;
  description?: ReactNode;
  error?: ReactNode;
  group?: boolean;
  children: (
    control: VikingBotInteractionFieldContext<Value>['controlProps'],
  ) => ReactNode;
}) {
  return (
    <FormField
      {...props}
      label={config?.label ?? props.label}
      description={config?.description ?? props.description}
    >
      {(controlProps) =>
        config?.render
          ? config.render({
              value,
              onChange,
              disabled,
              error: props.error,
              controlProps,
            })
          : children(controlProps)
      }
    </FormField>
  );
}
